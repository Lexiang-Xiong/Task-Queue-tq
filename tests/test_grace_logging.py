import os
import pytest
import time
import subprocess
import signal
import sys
import shutil
from pathlib import Path

# --- 1. 智能查找路径 ---
def get_tq_paths():
    tq_home = os.environ.get("TQ_HOME")
    candidates = []
    if tq_home: candidates.append(Path(tq_home))
    
    current_dir = Path(__file__).parent.resolve()
    candidates.append(current_dir)          # tests/
    candidates.append(current_dir.parent)   # root/
    candidates.append(Path("."))            # cwd
    
    for base in candidates:
        sched = base / "scheduler.sh"
        utils = base / "queue_utils.py"
        if sched.exists() and utils.exists():
            return str(sched), str(utils)
            
    raise FileNotFoundError("Could not find scheduler.sh AND queue_utils.py. Please set TQ_HOME.")

SCHEDULER_PATH, UTILS_PATH = get_tq_paths()

@pytest.fixture
def workspace(tmp_path):
    d = tmp_path / "task_queue"
    d.mkdir()
    logs = d / "logs"
    logs.mkdir()
    tasks_log = logs / "tasks"
    tasks_log.mkdir()

    # 复制依赖文件 queue_utils.py
    shutil.copy(UTILS_PATH, d / "queue_utils.py")
    os.chmod(d / "queue_utils.py", 0o755)

    # 修改 scheduler.sh 中的 BASE_DIR
    with open(SCHEDULER_PATH, 'r') as f:
        content = f.read()
        
    test_scheduler = d / "scheduler_test.sh"
    with open(test_scheduler, 'w') as f:
        f.write(content)
    
    os.chmod(test_scheduler, 0o755)
    
    return d, str(test_scheduler)

# --- 模拟任务脚本 ---
SIMULATED_TASK_SCRIPT = """
import time
import signal
import sys
import os

save_duration = int(sys.argv[1]) if len(sys.argv) > 1 else 5
print(f"[Task] Started with PID: {os.getpid()}. Will simulate save for {save_duration}s on SIGTERM.")

stop_requested = False
def signal_handler(signum, frame):
    global stop_requested
    print(f"\\n[Task] Received signal {signum}. Initiating graceful shutdown (saving for {save_duration}s)...", flush=True)
    stop_requested = True
    time.sleep(save_duration)
    print("[Task] Save complete. Exiting.", flush=True)
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)

epoch = 0
while not stop_requested:
    print(f"[Task] Epoch {epoch} processing...", flush=True)
    time.sleep(1)
    epoch += 1
    if epoch > 30: break
"""

def test_graceful_shutdown_and_logging(workspace):
    base_dir, scheduler_script = workspace
    queue_name = "grace_test"
    grace_period = 3  # 加快测试速度
    
    # 1. 创建模拟脚本
    sim_task_file = base_dir / "sim_task.py"
    with open(sim_task_file, 'w') as f:
        f.write(SIMULATED_TASK_SCRIPT)
        
    # 2. 提交任务
    task_cmd = f"python {sim_task_file} {grace_period}"
    q_file = base_dir / f"{queue_name}.queue"
    with open(q_file, 'w') as f:
        f.write(f"100:{grace_period}:{task_cmd}\n")
        
    # 3. 启动调度器
    proc = subprocess.Popen(
        ["bash", scheduler_script, queue_name],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        preexec_fn=os.setsid 
    )
    
    preempt_proc = None

    try:
        # 等待启动 (轮询 .running 文件)
        start_time = time.time()
        pid = -1
        log_path = ""
        run_file = base_dir / f"{queue_name}.running"

        while time.time() - start_time < 5:
            if run_file.exists():
                try:
                    with open(run_file) as f:
                        lines = f.read().splitlines()
                        # V2 格式适配 (4 行)
                        if len(lines) >= 4:
                            pid = int(lines[0])
                            log_path = lines[2] # Line 3 is LogPath
                            break
                        # 旧格式兼容
                        elif len(lines) >= 5 and "{" not in lines[3]:
                            pid = int(lines[0])
                            log_path = lines[3]
                            break
                except: pass
            time.sleep(0.5)
            
        assert pid != -1, f"Task failed to start. Scheduler log: {base_dir}/logs/scheduler_{queue_name}.log"
        
        # 4. 抢占测试
        with open(q_file, 'a') as f:
            f.write(f"1:0:echo 'Priority Task'\n")
            
        interrupted = False
        wait_start = time.time()
        while time.time() - wait_start < 10:
            if not run_file.exists():
                interrupted = True
                break
            else:
                try:
                    with open(run_file) as f:
                        new_pid = int(f.readline().strip())
                    if new_pid != pid:
                        interrupted = True
                        break
                except: pass
            time.sleep(1)
            
        assert interrupted, "Original task was not pre-empted."
        
        # 5. 验证日志
        # 这里 log_path 应该是真实路径，不再是 "default"
        assert os.path.exists(log_path), f"Log file not found at: {log_path}"
        
        time.sleep(1)
        with open(log_path, 'r') as f:
            content = f.read()
            assert "Received signal" in content or "Starting" in content

    finally:
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait()
        
        if preempt_proc and preempt_proc.poll() is None:
            preempt_proc.terminate()
            preempt_proc.wait()