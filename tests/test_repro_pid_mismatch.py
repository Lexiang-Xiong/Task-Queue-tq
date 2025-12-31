import os
import sys
import time
import pytest
import shutil
import subprocess
import signal
from pathlib import Path

# --- 测试配置 ---
# 模拟一个长时间运行的任务，它会启动一个子进程（Python解释器）
TASK_SCRIPT = """
import time
import os
print(f"Task started. PID: {os.getpid()}", flush=True)
# 保持运行，等待被调度器检测
while True:
    time.sleep(1)
"""

# 模拟 nvidia-smi 的脚本
# 它会查找当前运行的任务（从 .running 文件），然后输出该任务的 子进程 PID
# 从而模拟 "显卡上跑的是子进程" 这一现象
MOCK_SMI_SCRIPT = """#!/bin/bin/env python3
import os
import sys
import subprocess

# 1. 尝试读取 .running 文件获取父进程 PID
try:
    with open("0.running", "r") as f:
        parent_pid = f.readline().strip()
except:
    sys.exit(0) # 没有任务运行，返回空

# 2. 查找父进程的子进程 (模拟 Python 子进程在跑显卡)
try:
    # pgrep -P <parent_pid> 查找子进程
    child_pid = subprocess.check_output(["pgrep", "-P", parent_pid]).decode().strip().split('\\n')[0]
    # 输出子进程 PID，假装它是显卡上的进程
    print(child_pid)
except:
    # 如果没找到子进程（还没启动完全），就什么都不输出
    pass
"""

@pytest.fixture
def reproduction_env(tmp_path):
    """
    搭建一个隔离的测试环境，包含 mock 的 nvidia-smi
    """
    # 1. 准备目录
    work_dir = tmp_path / "tq_repro"
    work_dir.mkdir()
    (work_dir / "logs" / "tasks").mkdir(parents=True)
    
    # 2. 复制核心文件
    root_dir = Path(__file__).parent.parent
    shutil.copy(root_dir / "queue_utils.py", work_dir)
    
    # 3. 创建任务脚本
    (work_dir / "task.py").write_text(TASK_SCRIPT)
    
    # 4. 创建 Mock nvidia-smi
    mock_smi = work_dir / "mock_nvidia_smi"
    mock_smi.write_text(MOCK_SMI_SCRIPT)
    mock_smi.chmod(0o755)
    
    # 5. 读取并修改 scheduler.sh
    # 关键：将 scheduler.sh 中的 'nvidia-smi' 替换为我们的 './mock_nvidia_smi'
    scheduler_content = (root_dir / "scheduler.sh").read_text()
    # 替换命令
    scheduler_content = scheduler_content.replace(
        "nvidia-smi --query-compute-apps=pid --format=csv,noheader,nounits -i \"$GPU_ID\"", 
        f"{sys.executable} ./mock_nvidia_smi"
    )
    # 缩短 sleep 时间加速测试
    scheduler_content = scheduler_content.replace("sleep 10", "sleep 1")
    scheduler_content = scheduler_content.replace("sleep 3", "sleep 1")
    
    (work_dir / "scheduler_repro.sh").write_text(scheduler_content)
    (work_dir / "scheduler_repro.sh").chmod(0o755)
    
    return work_dir

def test_pid_mismatch_logic(reproduction_env):
    """
    核心测试逻辑：
    1. 提交任务 (python task.py)，不带 exec。
    2. 调度器启动，记录 Shell PID。
    3. Mock SMI 报告 Python PID (Shell 的子进程)。
    4. 如果 Bug 存在：调度器会杀死任务 (YIELD)。
    5. 如果 Fix 生效：调度器识别出那是子进程，任务继续运行 (RUN)。
    """
    cwd = str(reproduction_env)
    
    # 1. 提交任务到 0.queue
    # 注意：我们故意不使用 exec，让它产生子进程
    import json
    task = {
        "p": 100, "g": 10, "t": "test_bug", 
        "c": f"{sys.executable} task.py", "wd": cwd
    }
    (reproduction_env / "0.queue").write_text(json.dumps(task) + "\n")
    
    # 2. 启动调度器
    print("\n[Test] Launching Scheduler...")
    proc = subprocess.Popen(
        ["bash", "scheduler_repro.sh", "0"],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid # 新进程组
    )
    
    try:
        # 3. 等待并观察
        # 我们给它 5 秒钟，足够调度器进行几次检测循环
        time.sleep(5)
        
        # 4. 检查日志
        log_file = reproduction_env / "logs" / "scheduler_0.log"
        assert log_file.exists(), "Scheduler log missing"
        
        log_content = log_file.read_text()
        print(f"\n[Scheduler Log]\n{log_content}")
        
        # 验证 1: 任务是否成功启动
        assert "START: Task" in log_content
        
        # 验证 2 (关键): 是否发生了误杀 (YIELD)
        # 如果代码未修复，这里会出现 YIELD
        # 如果代码已修复，这里不应出现 YIELD
        if "YIELD: Unmanaged PID" in log_content:
            pytest.fail("❌ Test Failed: Scheduler killed its own child process! (Bug Reproduced)")
        
        # 验证 3: 任务是否还在运行
        # 检查 .running 文件是否存在
        assert (reproduction_env / "0.running").exists(), "Task should still be running"
        
        print("\n✅ Test Passed: Scheduler correctly identified child process as safe.")
        
    finally:
        # 清理
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait()

if __name__ == "__main__":
    # 手动运行测试
    sys.exit(pytest.main(["-v", __file__]))