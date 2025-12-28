import sys
import os
import pytest
import json
from unittest.mock import MagicMock, patch

# --- 1. 导入 tq 模块 ---
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

import tq

# --- 2. 测试环境 Fixture ---
@pytest.fixture
def mock_workspace(tmp_path):
    d = tmp_path / "task_queue"
    d.mkdir()
    logs = d / "logs"
    logs.mkdir()
    tasks = logs / "tasks"
    tasks.mkdir()
    
    with patch("tq.BASE_DIR", str(d)), \
         patch("tq.LOG_DIR", str(logs)), \
         patch("tq.TASK_LOG_DIR", str(tasks)):
        yield d

# --- 3. 辅助函数：Mock Conda ---
@pytest.fixture
def mock_conda_system():
    real_exists = os.path.exists
    with patch("os.popen") as mock_popen, \
         patch("os.path.exists") as mock_exists:
        mock_popen.return_value.read.return_value = "/mock/anaconda3"
        def side_effect(path):
            if str(path).endswith("conda.sh"): return True
            return real_exists(path)
        mock_exists.side_effect = side_effect
        yield

# --- 测试用例 ---

def test_session_env_switching(mock_workspace, mock_conda_system):
    shell = tq.TaskQueueShell()
    shell.do_env("session_env_v1")
    shell.default("python train.py")
    
    q_file = mock_workspace / "0.queue"
    assert q_file.exists()
    
    with open(q_file, 'r') as f:
        line = f.readline().strip()
        task = json.loads(line) # V2 Protocol
        
    cmd = task['c']
    assert "source /mock/anaconda3/etc/profile.d/conda.sh" in cmd
    assert "conda activate session_env_v1" in cmd
    assert "python train.py" in cmd

def test_inline_env_override(mock_workspace, mock_conda_system):
    shell = tq.TaskQueueShell()
    shell.conda_env = "base"
    shell.default("python data_prep.py -e data_env")
    
    q_file = mock_workspace / "0.queue"
    with open(q_file, 'r') as f:
        task = json.loads(f.readline())
    
    cmd = task['c']
    assert "conda activate data_env" in cmd
    # Ensure -e is consumed
    assert "-e data_env" not in cmd.split("&&")[-1] 

def test_priority_logic_mixed(mock_workspace, mock_conda_system):
    shell = tq.TaskQueueShell()
    q_file = mock_workspace / "0.queue"
    
    # 1. 设置会话环境为 global_env
    shell.do_env("global_env")
    
    # 2. 提交三个任务
    shell.default("python task_a.py")
    shell.default("python task_b.py --flag 1 -e local_env")
    shell.default("python task_c.py -e base")
    
    # 3. 验证
    with open(q_file, 'r') as f:
        lines = f.readlines()
        
    task_a = json.loads(lines[0])
    task_b = json.loads(lines[1])
    task_c = json.loads(lines[2])
    
    # Task A -> global_env
    assert "conda activate global_env" in task_a['c']
    
    # Task B -> local_env
    assert "conda activate local_env" in task_b['c']
    assert "global_env" not in task_b['c']
    
    # Task C -> base (raw command)
    assert "source" not in task_c['c']
    assert task_c['c'] == "python task_c.py"

def test_batch_submission_simulation(mock_workspace, mock_conda_system):
    shell = tq.TaskQueueShell()
    shell.do_env("default_env")
    
    batch_commands = [
        "python step1.py -t step1",
        "python step2.py -e heavy_env -t step2 -p 50",
        "python step3.py -t step3"
    ]
    
    for cmd in batch_commands:
        shell.default(cmd)
        
    q_file = mock_workspace / "0.queue"
    with open(q_file, 'r') as f:
        tasks = [json.loads(line) for line in f.readlines()]
        
    assert len(tasks) == 3
    
    # Step 1
    assert "default_env" in tasks[0]['c']
    assert tasks[0]['t'] == "step1"
    
    # Step 2
    assert "heavy_env" in tasks[1]['c']
    assert tasks[1]['t'] == "step2"
    assert tasks[1]['p'] == 50
    
    # Step 3
    assert "default_env" in tasks[2]['c']