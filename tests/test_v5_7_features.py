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
    
    test_scheduler = d / "scheduler_test.sh"
    with open(test_scheduler, 'w') as f:
        f.write(content)
    os.chmod(test_scheduler, 0o755)
    
    return d, str(test_scheduler)

# --- 测试用例 ---

def test_running_file_format_and_header(workspace):
    """
    测试 V2 协议：
    1. .running 文件为 4 行格式 (PID, Prio, LogPath, JSON)
    2. 生成的 Log 文件包含 Enhanced Header
    """
    base_dir, scheduler_script = workspace
    queue_name = "tag_test"
    
    # 构造 V2 JSONL 任务
    tag_raw = "experiment/v1"
    tag_safe = "experiment_v1"
    cmd = "echo 'Hello Tag'"
    
    import json
    task_obj = {"p": 100, "g": 180, "t": tag_raw, "c": cmd, "wd": str(base_dir)}
    
    q_file = base_dir / f"{queue_name}.queue"
    with open(q_file, 'w') as f:
        f.write(json.dumps(task_obj) + "\n")
        
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
                # V2 格式: 4 行
                if len(running_data) >= 4:
                    break
            time.sleep(0.5)
            
        # --- 验证 1: Running 文件格式 ---
        assert len(running_data) == 4
        # Line 1: PID, Line 2: Prio, Line 3: LogPath, Line 4: JSON
        assert running_data[1] == "100"
        log_path = running_data[2]
        
        # 验证 JSON Payload 是否完整
        restored_task = json.loads(running_data[3])
        assert restored_task['t'] == tag_raw
        assert restored_task['c'] == cmd
        
        # --- 验证 2: Log 文件名过滤 ---
        assert tag_safe in os.path.basename(log_path)
        
        # --- 验证 3: Log Header ---
        time.sleep(1)
        with open(log_path, 'r') as f:
            log_content = f.read()
            
        assert "Task Metadata Log (V2)" in log_content
        assert f"Tag        : {tag_raw}" in log_content
        assert f"WorkDir    : {str(base_dir)}" in log_content # 验证 WorkDir

    finally:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait()

def test_tag_persistence_after_preempt(workspace):
    """
    测试 V2 抢占：
    确保抢占后回写的任务保留了原始 JSON 结构 (含 Tag, WorkDir 等)
    """
    base_dir, scheduler_script = workspace
    queue_name = "preempt_tag_test"
    
    original_tag = "MY_IMPORTANT_TAG"
    cmd_low = "sleep 20"
    
    import json
    task_low = {"p": 100, "g": 5, "t": original_tag, "c": cmd_low, "wd": "/tmp"}
    
    q_file = base_dir / f"{queue_name}.queue"
    with open(q_file, 'w') as f:
        f.write(json.dumps(task_low) + "\n")
        
    proc = subprocess.Popen(
        ["bash", scheduler_script, queue_name],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        preexec_fn=os.setsid 
    )
    
    try:
        run_file = base_dir / f"{queue_name}.running"
        time.sleep(2)
        assert run_file.exists()
        
        # 提交高优任务
        task_high = {"p": 1, "g": 5, "t": "urgent", "c": "echo urgent"}
        with open(q_file, 'a') as f:
            f.write(json.dumps(task_high) + "\n")
            
        # 等待回写
        preempted = False
        requeued_task = None
        
        start_wait = time.time()
        while time.time() - start_wait < 10:
            if q_file.exists():
                with open(q_file, 'r') as f:
                    lines = f.readlines()
                for line in lines:
                    try:
                        t = json.loads(line)
                        if t.get('p') == 100:
                            requeued_task = t
                            preempted = True
                            break
                    except: pass
            if preempted: break
            time.sleep(1)
            
        assert preempted
        assert requeued_task['t'] == original_tag
        assert requeued_task['wd'] == "/tmp" # WorkDir 也必须被保留

    finally:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait()

# ... (test_queue_utils_locking 可以保留，但需要 update input data 格式) ...
def test_queue_utils_locking(workspace):
    base_dir, _ = workspace
    utils_script = base_dir / "queue_utils.py"
    q_file = base_dir / "lock_test.queue"
    
    import json
    # 混合写入旧格式和新格式 (测试 utils 的兼容性)
    with open(q_file, 'w') as f:
        f.write(json.dumps({"p": 100, "c": "cmd1", "t": "tag1"}) + "\n")
        f.write("10:180:tag2:cmd2\n") # 旧格式，Prio 10
        f.write(json.dumps({"p": 50, "c": "cmd3"}) + "\n")
        
    result = subprocess.check_output(
        ["python3", str(utils_script), "pop", str(q_file)], 
        text=True
    )
    
    # 验证弹出的是 Prio 10 (tag2)
    # utils 现在的输出是 TQ_TAG='tag2'
    assert "TQ_TAG='tag2'" in result
    assert "TQ_PRIO=10" in result

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