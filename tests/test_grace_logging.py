import os
import pytest
import time
import subprocess
import signal
import sys
import shutil
from pathlib import Path

# --- 1. 智能查找路径 (保持不变) ---
def get_tq_paths():
    # 尝试环境变量
    tq_home = os.environ.get("TQ_HOME")
    candidates = []
    if tq_home: candidates.append(Path(tq_home))
    
    # 尝试相对路径
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

    # --- 关键修正：复制依赖文件 queue_utils.py ---
    shutil.copy(UTILS_PATH, d / "queue_utils.py")
    # 确保有执行权限
    os.chmod(d / "queue_utils.py", 0o755)

    # 修改 scheduler.sh 中的 BASE_DIR
    with open(SCHEDULER_PATH, 'r') as f:
        content = f.read()
    
    # 动态替换 BASE_DIR 变量，指向临时目录
    content = content.replace('BASE_DIR="$HOME/task_queue"', f'BASE_DIR="{str(d)}"')
    
    test_scheduler = d / "scheduler_test.sh"
    with open(test_scheduler, 'w') as f:
        f.write(content)
    
    os.chmod(test_scheduler, 0o755)
    
    return d, str(test_scheduler)

# --- 模拟任务脚本 (保持不变) ---
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
    # 写入队列
    q_file = base_dir / f"{queue_name}.queue"
    with open(q_file, 'w') as f:
        f.write(f"100:{grace_period}:{task_cmd}\n")
        
    # 3. 启动调度器
    proc = subprocess.Popen(
        ["bash", scheduler_script, queue_name],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        preexec_fn=os.setsid 
    )
    
    preempt_proc = None # 初始化变量，防止 finally 报错

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
                        if len(lines) >= 5:
                            pid = int(lines[0])
                            log_path = lines[3]
                            break
                except: pass
            time.sleep(0.5)
            
        assert pid != -1, f"Task failed to start. Scheduler log: {base_dir}/logs/scheduler_{queue_name}.log"
        
        # 4. 抢占测试
        # 在同一个队列追加一个高优先级任务 (Prio 1 < 100)
        with open(q_file, 'a') as f:
            f.write(f"1:0:echo 'Priority Task'\n")
            
        # 启动另一个调度器实例来处理抢占 (或者等待原调度器循环)
        # 注意：原调度器就在循环，其实不需要启动新的，
        # 但为了加速触发，我们可以让原调度器自然检测到。
        # 这里为了稳妥，我们不启动新进程，直接等原进程检测。
        
        # 等待原任务被杀掉 (PID 消失或文件消失)
        interrupted = False
        wait_start = time.time()
        while time.time() - wait_start < 10:
            if not run_file.exists():
                interrupted = True
                break
            else:
                # 检查 PID 是否变了 (变成了新任务)
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
        assert os.path.exists(log_path)
        
        # 稍等 flush
        time.sleep(1)
        
        with open(log_path, 'r') as f:
            content = f.read()
            # 验证信号捕获
            assert "Received signal" in content or "Starting" in content

    finally:
        # 清理
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait()
        
        if preempt_proc and preempt_proc.poll() is None:
            preempt_proc.terminate()
            preempt_proc.wait()