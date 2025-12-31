import os
import sys
import time
import json
import shutil
import subprocess
import signal
from pathlib import Path
import pytest

# 模拟任务脚本
TASK_SCRIPT = """
import time
import os
import sys
print(f"[{os.getpid()}] Task Started", flush=True)
# 模拟工作
for i in range(100):
    print(f"Tick {i}", flush=True)
    time.sleep(0.5)
"""

# [修正] 模拟 nvidia-smi 的脚本
# 输出为空，模拟真实世界中 --query-compute-apps 忽略 G 进程后的结果
MOCK_SMI_SCRIPT = """#!/bin/bash
echo ""
"""

@pytest.fixture
def test_env(tmp_path):
    work_dir = tmp_path / "tq_log_test"
    work_dir.mkdir()
    (work_dir / "logs" / "tasks").mkdir(parents=True)
    
    root_dir = Path(__file__).parent.parent
    
    # 复制核心文件
    shutil.copy(root_dir / "queue_utils.py", work_dir)
    (work_dir / "queue_utils.py").chmod(0o755)
    
    # 读取并修改 scheduler.sh
    scheduler_content = (root_dir / "scheduler.sh").read_text()
    
    # 1. 替换 nvidia-smi 调用
    scheduler_content = scheduler_content.replace(
        'nvidia-smi --query-processes=pid,type --format=csv,noheader,nounits -i "$GPU_ID"',
        './mock_smi'
    )
    scheduler_content = scheduler_content.replace(
        'nvidia-smi --query-compute-apps=pid --format=csv,noheader,nounits -i "$GPU_ID"',
        './mock_smi'
    )
    
    # 2. 加速测试循环 (加快调度器响应速度)
    scheduler_content = scheduler_content.replace("sleep 10", "sleep 0.5")
    scheduler_content = scheduler_content.replace("sleep 3", "sleep 0.5")
    
    (work_dir / "scheduler.sh").write_text(scheduler_content)
    (work_dir / "scheduler.sh").chmod(0o755)
    
    # 创建 Mock SMI
    (work_dir / "mock_smi").write_text(MOCK_SMI_SCRIPT)
    (work_dir / "mock_smi").chmod(0o755)
    
    # 创建任务脚本
    (work_dir / "task.py").write_text(TASK_SCRIPT)
    
    return work_dir

def print_debug_info(work_dir, proc):
    """辅助函数：打印调试信息"""
    print("\n" + "="*30 + " DEBUG INFO " + "="*30)
    
    # 1. 打印调度器日志
    sched_log = work_dir / "logs" / "scheduler_0.log"
    if sched_log.exists():
        print(f"--- Scheduler Log ({sched_log}) ---")
        print(sched_log.read_text())
    else:
        print("--- Scheduler Log: NOT FOUND ---")
        
    # 2. 打印队列内容
    queue_file = work_dir / "0.queue"
    if queue_file.exists():
        print(f"--- Queue File Content ---")
        print(queue_file.read_text())
        
    # 3. 打印 Running 文件
    run_file = work_dir / "0.running"
    if run_file.exists():
        print(f"--- Running File Content ---")
        print(run_file.read_text())

    print("="*72)

def test_log_persistence(test_env):
    cwd = str(test_env)
    
    # 1. 提交低优先级任务 (Prio 100)
    task_low = {
        "p": 100, "g": 1, "t": "low_task", 
        "c": f"{sys.executable} task.py", "wd": cwd
    }
    (test_env / "0.queue").write_text(json.dumps(task_low) + "\n")
    
    # 2. 启动调度器
    print("\n[*] Starting Scheduler...")
    proc = subprocess.Popen(
        ["bash", "scheduler.sh", "0"],
        cwd=cwd,
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE, 
        preexec_fn=os.setsid
    )
    
    try:
        # 3. 等待任务启动并生成日志
        max_retries = 10
        found = False
        log_file_path = None
        log_dir = test_env / "logs" / "tasks"
        
        for i in range(max_retries):
            time.sleep(0.5)
            logs = list(log_dir.glob("*.log"))
            if len(logs) > 0:
                log_file_path = logs[0]
                found = True
                break
            
        if not found:
            raise AssertionError("Timeout: Log file was not created.")
            
        print(f"[*] Initial log created: {log_file_path.name}")
        
        # 4. 提交高优先级任务 (Prio 10) -> 触发抢占
        print("[*] Submitting High Priority Task...")
        # [关键修改] 使用 sleep 5 让高优任务运行久一点，确保我们检查队列时，低优任务还在等待
        task_high = {
            "p": 10, "g": 1, "t": "high_task",
            "c": "sleep 5", "wd": cwd
        }
        with open(test_env / "0.queue", "a") as f:
            f.write(json.dumps(task_high) + "\n")
            
        # 5. 等待抢占发生 (Wait 3s)
        # 此时：高优任务运行了 3s (还剩 2s)，低优任务被抢占并回写到了队列中
        time.sleep(3) 
        
        # 验证回写数据
        queue_content = (test_env / "0.queue").read_text()
        print(f"[*] Queue content during preempt:\n{queue_content}") # Debug output
        
        assert "lp" in queue_content, "❌ JSON returned to queue MUST contain 'lp' (Log Path) field!"
        assert str(log_file_path) in queue_content, "❌ Queue JSON must point to the original log file!"
        
        # 6. 等待恢复 (Wait 4s)
        # 此时：高优任务运行结束，调度器探测到空闲，重新拉起低优任务
        time.sleep(4)
        
        # 验证日志文件数量
        logs_now = list(log_dir.glob("*.log"))
        
        # [修正] 仅统计 "low_task" 的日志。我们不关心 "high_task" 生成的日志文件。
        # 如果 low_task 没有复用旧日志，这里就会有 2 个 low_task 相关文件，测试将失败。
        low_task_logs = [l for l in logs_now if "low_task" in l.name]
        
        assert len(low_task_logs) == 1, f"❌ Log Splitting! Found {len(low_task_logs)} files for low_task: {low_task_logs}"
        
        # 验证内容 (包含 RESUME 标记)
        content = log_file_path.read_text()
        assert "RESUMED BY TQ SCHEDULER" in content, "❌ Log file missing RESUME marker"
        
        # 验证 PID 变化 (证明进程确实重启了)
        import re
        pids = re.findall(r'\[(\d+)\] Task Started', content)
        assert len(pids) >= 2, "Should see at least 2 process starts"
        assert pids[0] != pids[-1], "PIDs should be different"
        
        print("\n✅ Test Passed: Log persistence works perfectly.")
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        print_debug_info(test_env, proc)
        raise e
        
    finally:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait()

if __name__ == "__main__":
    sys.exit(pytest.main(["-v", "-s", __file__]))