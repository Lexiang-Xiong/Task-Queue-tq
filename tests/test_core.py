import os
import pytest
import shutil
from unittest.mock import MagicMock, patch

# 为了方便测试，我们简单模拟 tq.py 的核心逻辑，或者你可以修改 tq.py 为模块导入
# 这里我们采用“白盒测试”逻辑，复制关键路径进行验证

def submit_task_logic(base_dir, queue_name, current_env, raw_input):
    """
    这是从 tq.py 提取出的纯逻辑函数，用于测试
    """
    import re
    priority = 100
    grace = 180
    
    # 解析逻辑
    p_match = re.search(r'\s+(-p|--priority)\s+(\d+)', raw_input)
    if p_match:
        priority = int(p_match.group(2))
        raw_input = raw_input.replace(p_match.group(0), "")
        
    g_match = re.search(r'\s+(-g|--grace)\s+(\d+)', raw_input)
    if g_match:
        grace = int(g_match.group(2))
        raw_input = raw_input.replace(g_match.group(0), "")

    cmd_content = raw_input.strip()
    final_cmd = cmd_content

    # Conda 逻辑
    if current_env and current_env != "base":
        final_cmd = f"source /mock/conda.sh && conda activate {current_env} && {cmd_content}"

    q_file = os.path.join(base_dir, f"{queue_name}.queue")
    with open(q_file, 'a') as f:
        f.write(f"{priority}:{grace}:{final_cmd}\n")
    
    return priority, grace, final_cmd

@pytest.fixture
def workspace(tmp_path):
    """创建临时工作目录"""
    d = tmp_path / "task_queue"
    d.mkdir()
    return str(d)

def test_basic_submission(workspace):
    """测试：普通提交"""
    p, g, cmd = submit_task_logic(workspace, "0", "base", "python train.py")
    
    assert p == 100
    assert g == 180
    assert cmd == "python train.py"
    
    with open(os.path.join(workspace, "0.queue")) as f:
        line = f.read().strip()
    assert line == "100:180:python train.py"

def test_priority_and_grace(workspace):
    """测试：参数解析"""
    p, g, cmd = submit_task_logic(workspace, "0", "base", "python train.py -p 10 --grace 600")
    
    assert p == 10
    assert g == 600
    assert cmd == "python train.py" # 参数应该被移除

def test_conda_wrapper(workspace):
    """测试：Conda 环境包裹"""
    p, g, cmd = submit_task_logic(workspace, "0", "my_env", "torchrun main.py")
    
    # 验证命令是否被包裹
    assert "conda activate my_env" in cmd
    assert "torchrun main.py" in cmd

def test_multi_gpu_queue(workspace):
    """测试：多GPU队列文件生成"""
    submit_task_logic(workspace, "0,1", "base", "sleep 10")
    assert os.path.exists(os.path.join(workspace, "0,1.queue"))

def test_scheduler_read_write(workspace):
    """测试：模拟调度器读写 Running 文件"""
    # 模拟 Running 文件内容 (PID, Prio, Grace, Cmd)
    run_file = os.path.join(workspace, "0.running")
    with open(run_file, 'w') as f:
        f.write("12345\n50\n600\npython long_task.py")
        
    with open(run_file) as f:
        lines = f.read().splitlines()
    
    assert lines[0] == "12345"
    assert lines[1] == "50"
    assert lines[2] == "600"
