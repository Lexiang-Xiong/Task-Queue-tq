import os
import pytest
import time
import subprocess
import signal
import shutil
import sys
from pathlib import Path

# --- 1. 路径获取与环境准备 (复用之前的稳健逻辑) ---
def get_tq_paths():
    tq_home = os.environ.get("TQ_HOME")
    candidates = []
    if tq_home: candidates.append(Path(tq_home))
    current_dir = Path(__file__).parent.resolve()
    candidates.append(current_dir)
    candidates.append(current_dir.parent)
    candidates.append(Path("."))
    
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

    # 复制 queue_utils.py (必须，因为 scheduler.sh 依赖它)
    shutil.copy(UTILS_PATH, d / "queue_utils.py")
    os.chmod(d / "queue_utils.py", 0o755)

    # 修改 scheduler.sh 中的 BASE_DIR
    with open(SCHEDULER_PATH, 'r') as f:
        content = f.read()
    content = content.replace('BASE_DIR="$HOME/task_queue"', f'BASE_DIR="{str(d)}"')
    
    test_scheduler = d / "scheduler_test.sh"
    with open(test_scheduler, 'w') as f:
        f.write(content)
    os.chmod(test_scheduler, 0o755)
    
    return d, str(test_scheduler)

# --- 测试用例 ---

def test_running_file_format_and_header(workspace):
    """
    测试 v5.7 核心特性：
    1. .running 文件是否为 6 行格式 (含 Tag)
    2. 生成的 Log 文件是否包含 Metadata Header
    3. 文件名是否经过安全过滤
    """
    base_dir, scheduler_script = workspace
    queue_name = "tag_test"
    
    # 构造一个带特殊字符 Tag 的任务
    # Tag: "experiment/v1" (应该被过滤为 experiment_v1)
    tag_raw = "experiment/v1"
    tag_safe = "experiment_v1"
    cmd = "echo 'Hello Tag'"
    
    # 写入队列 (v5.7 格式: Prio:Grace:Tag:Cmd)
    q_file = base_dir / f"{queue_name}.queue"
    with open(q_file, 'w') as f:
        f.write(f"100:180:{tag_raw}:{cmd}\n")
        
    # 启动调度器
    proc = subprocess.Popen(
        ["bash", scheduler_script, queue_name],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        preexec_fn=os.setsid 
    )
    
    try:
        # 等待任务运行
        start_time = time.time()
        running_data = []
        run_file = base_dir / f"{queue_name}.running"
        
        while time.time() - start_time < 5:
            if run_file.exists():
                with open(run_file) as f:
                    running_data = f.read().splitlines()
                if len(running_data) >= 6:
                    break
            time.sleep(0.5)
            
        # --- 验证 1: Running 文件格式 ---
        assert len(running_data) == 6, f"Running file should have 6 lines, got {len(running_data)}"
        # PID, Prio, Grace, Tag, LogPath, Cmd
        assert running_data[1] == "100"      # Prio
        assert running_data[3] == tag_raw    # Tag (原始值应保留在 running 文件中)
        log_path = running_data[4]
        assert running_data[5] == cmd        # Cmd
        
        # --- 验证 2: Log 文件名过滤 ---
        assert tag_safe in os.path.basename(log_path), f"Log filename should contain safe tag '{tag_safe}'"
        assert "/" not in os.path.basename(log_path) # 确保特殊字符被替换
        
        # --- 验证 3: Log Header ---
        assert os.path.exists(log_path)
        # 等待 flush
        time.sleep(1)
        with open(log_path, 'r') as f:
            log_content = f.read()
            
        print(f"\n[Log Content Preview]\n{log_content}")
        
        assert "Task Metadata Log" in log_content
        assert f"Tag        : {tag_raw}" in log_content
        assert f"Command    : {cmd}" in log_content
        assert "Hello Tag" in log_content # 确保真实输出也在

    finally:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait()

def test_tag_persistence_after_preempt(workspace):
    """
    测试 v5.7 关键修复：
    抢占发生后，Tag 应该保持原样，而不是变成 'preempted'
    """
    base_dir, scheduler_script = workspace
    queue_name = "preempt_tag_test"
    
    # 1. 提交一个低优先级长任务 (Prio 100, Tag "MY_IMPORTANT_TAG")
    original_tag = "MY_IMPORTANT_TAG"
    # 使用 sleep 模拟长任务
    cmd_low = "sleep 20"
    
    q_file = base_dir / f"{queue_name}.queue"
    with open(q_file, 'w') as f:
        f.write(f"100:5:{original_tag}:{cmd_low}\n")
        
    # 启动调度器
    proc = subprocess.Popen(
        ["bash", scheduler_script, queue_name],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        preexec_fn=os.setsid 
    )
    
    try:
        # 等待低优先级任务运行
        run_file = base_dir / f"{queue_name}.running"
        time.sleep(2) 
        assert run_file.exists()
        
        # 2. 提交一个高优先级任务 (Prio 1)
        with open(q_file, 'a') as f:
            f.write(f"1:5:urgent:echo urgent\n")
            
        # 等待抢占发生 (Running 文件变成 Prio 1 的任务，或者旧任务回到 Queue)
        # 我们检测 Queue 文件是否重新出现了 Prio 100 的任务
        preempted = False
        requeued_line = ""
        
        start_wait = time.time()
        while time.time() - start_wait < 10:
            if q_file.exists():
                with open(q_file, 'r') as f:
                    lines = f.readlines()
                # 找回 Prio 100 的任务
                for line in lines:
                    if line.startswith("100:"):
                        requeued_line = line.strip()
                        preempted = True
                        break
            if preempted: break
            time.sleep(1)
            
        assert preempted, "Task was not preempted/requeued."
        
        # --- 验证 4: Tag 保持不变 ---
        # 期望格式: 100:Grace:MY_IMPORTANT_TAG:sleep 20
        print(f"\n[Requeued Line] {requeued_line}")
        parts = requeued_line.split(':')
        assert len(parts) >= 4
        assert parts[2] == original_tag, f"Tag changed! Expected '{original_tag}', got '{parts[2]}'"
        assert parts[2] != "preempted", "Tag was wrongly overwritten by 'preempted' state"

    finally:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait()

def test_queue_utils_locking(workspace):
    """
    简单测试 queue_utils.py 的 pop 功能是否正常
    (真正的并发锁测试很难在单元测试中模拟，这里主要测试基本调用链路)
    """
    base_dir, _ = workspace
    utils_script = base_dir / "queue_utils.py"
    q_file = base_dir / "lock_test.queue"
    
    # 准备数据
    with open(q_file, 'w') as f:
        f.write("100:180:tag1:cmd1\n")
        f.write("10:180:tag2:cmd2\n") # Prio 10 更高
        f.write("50:180:tag3:cmd3\n")
        
    # 调用 pop
    result = subprocess.check_output(
        ["python3", str(utils_script), "pop", str(q_file)], 
        text=True
    ).strip()
    
    # 验证弹出的是 Prio 10
    assert "tag2" in result
    assert "cmd2" in result
    
    # 验证文件剩余内容
    with open(q_file, 'r') as f:
        content = f.read()
    assert "tag2" not in content
    assert "tag1" in content
    assert "tag3" in content