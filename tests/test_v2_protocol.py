import os
import sys
import pytest
import json
import subprocess
import signal
import time
from pathlib import Path

# 复用路径查找逻辑
def get_tq_paths():
    current_dir = Path(__file__).parent.resolve()
    base = current_dir.parent
    return str(base / "scheduler.sh"), str(base / "queue_utils.py")

SCHEDULER_PATH, UTILS_PATH = get_tq_paths()

@pytest.fixture
def v2_workspace(tmp_path):
    d = tmp_path / "task_queue_v2"
    d.mkdir()
    (d / "logs" / "tasks").mkdir(parents=True)
    
    # Copy Utils
    import shutil
    shutil.copy(UTILS_PATH, d / "queue_utils.py")
    
    # Create Scheduler Wrapper
    with open(SCHEDULER_PATH, 'r') as f:
        content = f.read()
    
    sched_script = d / "scheduler_test.sh"
    with open(sched_script, 'w') as f:
        f.write(content)
    os.chmod(sched_script, 0o755)
    
    return d, str(sched_script)

def test_workdir_persistence(v2_workspace):
    """
    核心测试：验证任务是否真的切换到了指定的 WorkDir 执行
    """
    base_dir, scheduler_script = v2_workspace
    
    # 1. 创建两个不同的目录
    dir_a = base_dir / "project_a"
    dir_a.mkdir()
    dir_b = base_dir / "project_b"
    dir_b.mkdir()
    
    # 在 dir_a 中放一个标记文件
    (dir_a / "mark_a.txt").touch()
    
    # 2. 构造任务：在 dir_a 中列出文件
    # 如果 WorkDir 生效，ls 应该能看到 mark_a.txt
    task = {
        "p": 100,
        "g": 180,
        "t": "wd_test",
        "wd": str(dir_a),
        "c": "ls -1",
        "git": "" # 显式给个空 git 字段防止潜在 None 错误 (虽不应发生)
    }
    
    q_file = base_dir / "0.queue"
    with open(q_file, 'w') as f:
        f.write(json.dumps(task) + "\n")
        
    # 3. 运行调度器
    # 使用 PIPE 捕获 stderr 以便调试
    proc = subprocess.Popen(
        ["bash", scheduler_script, "0"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        preexec_fn=os.setsid 
    )
    
    try:
        # 等待日志生成
        time.sleep(3)
        logs = list((base_dir / "logs" / "tasks").glob("*.log"))
        
        # 调试信息：如果失败，打印调度器日志
        if len(logs) == 0:
            sched_log = base_dir / "logs" / "scheduler_0.log"
            if sched_log.exists():
                print(f"\n[Scheduler Log]\n{sched_log.read_text()}")
            
            # 也可以查看 stderr
            _, stderr = proc.communicate(timeout=1)
            print(f"\n[Scheduler Stderr]\n{stderr.decode()}")
            
        assert len(logs) > 0
        
        with open(logs[0], 'r') as f:
            content = f.read()
            
        # 验证 Header
        assert f"WorkDir    : {str(dir_a)}" in content
        
        # 验证 Output (命令是在 dir_a 执行的)
        assert "mark_a.txt" in content
        
    finally:
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait()