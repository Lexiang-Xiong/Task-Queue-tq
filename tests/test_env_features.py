import sys
import os
import pytest
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
    """
    创建一个隔离的测试环境，并 Patch 掉 tq.py 中的全局路径变量
    """
    d = tmp_path / "task_queue"
    d.mkdir()
    logs = d / "logs"
    logs.mkdir()
    tasks = logs / "tasks"
    tasks.mkdir()
    
    # Patch 全局变量，防止测试写入真实的 ~/task_queue
    with patch("tq.BASE_DIR", str(d)), \
         patch("tq.LOG_DIR", str(logs)), \
         patch("tq.TASK_LOG_DIR", str(tasks)):
        yield d

# --- 3. 辅助函数：Mock Conda ---
@pytest.fixture
def mock_conda_system():
    """
    Mock 掉 os.popen 和 os.path.exists，让 tq 以为系统里有 Conda。
    """
    # [关键修复]：在 Patch 之前捕获真实的 os.path.exists
    real_exists = os.path.exists
    
    with patch("os.popen") as mock_popen, \
         patch("os.path.exists") as mock_exists:
        
        # 1. 模拟 conda info --base 返回路径
        mock_popen.return_value.read.return_value = "/mock/anaconda3"
        
        # 2. 模拟 conda.sh 存在
        def side_effect(path):
            # 如果检查的是 conda.sh，返回 True (模拟存在)
            if str(path).endswith("conda.sh"):
                return True
            # 对于其他路径（如 logs 目录、queue文件等），使用真实的检查逻辑
            return real_exists(path)
        
        mock_exists.side_effect = side_effect
        yield

# --- 测试用例 ---

def test_session_env_switching(mock_workspace, mock_conda_system):
    """
    测试功能：do_env (切换会话默认环境)
    """
    shell = tq.TaskQueueShell()
    
    # 1. 切换会话环境
    shell.do_env("session_env_v1")
    assert shell.conda_env == "session_env_v1"
    
    # 2. 提交任务
    shell.default("python train.py")
    
    # 3. 验证队列文件
    q_file = mock_workspace / "0.queue"
    assert q_file.exists()
    
    with open(q_file, 'r') as f:
        line = f.readline().strip()
        
    # 验证是否包含了 Conda 激活命令
    # v5.7 格式: Prio:Grace:Tag:Cmd
    parts = line.split(':', 3)
    cmd = parts[3]
    
    assert "source /mock/anaconda3/etc/profile.d/conda.sh" in cmd
    assert "conda activate session_env_v1" in cmd
    assert "python train.py" in cmd

def test_inline_env_override(mock_workspace, mock_conda_system):
    """
    测试功能：-e 参数 (单行覆盖)
    """
    shell = tq.TaskQueueShell()
    
    # 保持会话环境为默认 (base)
    shell.conda_env = "base"
    
    # 使用 -e 提交
    shell.default("python data_prep.py -e data_env")
    
    q_file = mock_workspace / "0.queue"
    with open(q_file, 'r') as f:
        line = f.readline().strip()
    
    cmd = line.split(':', 3)[3]
    
    # 验证 -e 生效
    assert "conda activate data_env" in cmd
    # 验证没有把 -e 参数残留给 python 脚本
    assert "-e data_env" not in cmd.split("&&")[-1] 

def test_priority_logic_mixed(mock_workspace, mock_conda_system):
    """
    测试功能：优先级逻辑 (Inline -e > Session env > Base)
    模拟批量提交场景
    """
    shell = tq.TaskQueueShell()
    q_file = mock_workspace / "0.queue"
    
    # 1. 设置会话环境为 global_env
    shell.do_env("global_env")
    
    # 2. 提交三个任务
    # Task A: 使用会话环境
    shell.default("python task_a.py")
    
    # Task B: 使用 -e 覆盖
    shell.default("python task_b.py --flag 1 -e local_env")
    
    # Task C: 使用 -e 指定 base (即不封装)
    # 注意：tq.py 逻辑是 if target != "base" 才封装
    shell.default("python task_c.py -e base")
    
    # 3. 验证
    with open(q_file, 'r') as f:
        lines = f.readlines()
        
    cmd_a = lines[0].split(':', 3)[3]
    cmd_b = lines[1].split(':', 3)[3]
    cmd_c = lines[2].split(':', 3)[3]
    
    # Task A -> global_env
    assert "conda activate global_env" in cmd_a
    
    # Task B -> local_env (覆盖了 global)
    assert "conda activate local_env" in cmd_b
    assert "global_env" not in cmd_b
    
    # Task C -> 原生命令 (无 conda 封装)
    assert "source" not in cmd_c
    assert "conda activate" not in cmd_c
    assert cmd_c.strip() == "python task_c.py"

def test_batch_submission_simulation(mock_workspace, mock_conda_system):
    """
    测试功能：模拟粘贴多行命令时的处理
    """
    shell = tq.TaskQueueShell()
    shell.do_env("default_env")
    
    # 模拟一段批量文本
    batch_commands = [
        "python step1.py -t step1",
        "python step2.py -e heavy_env -t step2 -p 50",
        "python step3.py -t step3"
    ]
    
    for cmd in batch_commands:
        shell.default(cmd)
        
    # 读取队列
    q_file = mock_workspace / "0.queue"
    with open(q_file, 'r') as f:
        lines = f.readlines()
        
    assert len(lines) == 3
    
    # 验证 Step 1 (继承 default_env)
    assert "default_env" in lines[0]
    assert "step1" in lines[0] # Tag
    
    # 验证 Step 2 (Override heavy_env)
    assert "heavy_env" in lines[1]
    assert "default_env" not in lines[1]
    assert "step2" in lines[1] # Tag
    assert lines[1].startswith("50:") # Priority
    
    # 验证 Step 3 (回到 default_env)
    assert "default_env" in lines[2]


def test_env_cli_behavior(mock_workspace, mock_conda_system):
    """
    测试 v5.9: env list 和 env activate 的行为
    """
    shell = tq.TaskQueueShell()
    
    # Mock os.system 捕获 list 指令
    with patch("os.system") as mock_system:
        # 1. 测试 env list
        shell.do_env("list")
        mock_system.assert_called_with("conda env list")
        
        # 2. 测试 env activate my_env
        shell.do_env("activate my_env")
        assert shell.conda_env == "my_env"
        
        # 3. 测试 env short_cut
        shell.do_env("short_cut")
        assert shell.conda_env == "short_cut"
        
        # 4. 测试 env activate (无参数) -> 报错但不崩溃
        # 我们捕获 stdout 来验证是否打印了 usage
        from io import StringIO
        captured_output = StringIO()
        sys.stdout = captured_output
        shell.do_env("activate")
        sys.stdout = sys.__stdout__
        assert "Usage" in captured_output.getvalue()
        # 环境不应改变
        assert shell.conda_env == "short_cut"