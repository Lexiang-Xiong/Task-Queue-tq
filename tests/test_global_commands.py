import os
import sys
import pytest
import json
from unittest.mock import patch, MagicMock, call
from pathlib import Path

# 导入模块
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path: sys.path.insert(0, parent_dir)

import tq

@pytest.fixture
def workspace(tmp_path):
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

# --- Test: do_env ---
def test_env_switching(workspace):
    """测试环境切换逻辑及 env list 显示 [Updated]"""
    shell = tq.TaskQueueShell()
    
    # 1. 默认 base
    assert shell.conda_env == "base"
    
    # 2. 切换环境
    shell.do_env("my_env")
    assert shell.conda_env == "my_env"
    
    # 3. 测试 env list (核心修复验证)
    # 我们 Mock _wrap_with_conda 来验证是否调用了它
    with patch.object(shell, '_wrap_with_conda', return_value="mocked_cmd") as mock_wrap, \
         patch("os.system") as mock_sys:
        
        shell.do_env("list")
        
        # 验证1: 调用了 wrapper，且传入了当前环境 "my_env"
        mock_wrap.assert_called_with("conda env list", "my_env")
        
        # 验证2: 实际执行的是 wrapper 返回的命令
        mock_sys.assert_called_with("mocked_cmd")

def test_default_submission_wrapper(workspace):
    """[NEW] 验证 default 提交也复用了封装逻辑"""
    shell = tq.TaskQueueShell()
    shell.conda_env = "my_env"
    
    # Mock wrapper 验证调用
    with patch.object(shell, '_wrap_with_conda', return_value="wrapped_python run.py") as mock_wrap:
        shell.default("python run.py")
        
        # 验证 default 调用了 wrapper
        mock_wrap.assert_called_with("python run.py", "my_env")
        
        # 验证队列文件写入了 wrap 后的结果
        q_file = workspace / "0.queue"
        assert "wrapped_python run.py" in q_file.read_text()

# --- Test: do_use ---
def test_use_queue_switching(workspace):
    """测试队列切换"""
    shell = tq.TaskQueueShell()
    assert shell.current_queue == "0" # Default
    
    shell.do_use("1")
    assert shell.current_queue == "1"
    
    shell.do_use("gpu_2")
    assert shell.current_queue == "gpu_2"

# --- Test: do_purge ---
def test_purge_logic(workspace):
    """测试清空队列"""
    shell = tq.TaskQueueShell()
    q_file = workspace / "0.queue"
    q_file.write_text("task1\ntask2")
    
    # Mock input='y' 确认删除
    with patch("builtins.input", return_value="y"):
        shell.do_purge("")
        
    assert not q_file.exists()

# --- Test: do_st ---
def test_status_display(workspace):
    """测试 st 指令能否正确解析系统状态 [Fixed for Colors]"""
    shell = tq.TaskQueueShell()
    
    # 1. 准备数据
    (workspace / "0.queue").touch()
    running_content = "1234\n100\n/log/path\n{\"c\": \"run.py\", \"t\": \"exp1\"}"
    (workspace / "0.running").write_text(running_content)
    (workspace / "1.queue").write_text("t1\nt2")
    
    # Mock _is_active 使得 0 显示为 ON
    with patch.object(shell, '_is_active', side_effect=lambda q: q == "0"):
        from io import StringIO
        captured = StringIO()
        sys.stdout = captured
        
        shell.do_st("")
        
        sys.stdout = sys.__stdout__
        output = captured.getvalue()
        
        # 验证 Queue 0 (Running)
        # 即使有颜色代码，这三个字符串片段应该都存在
        assert "0      :" in output
        assert "[RUN]" in output
        assert "PID:1234" in output
        assert "run.py" in output
        
        # 验证 Queue 1
        assert "1      :" in output
        assert "[STOPPED]" in output
        assert "2 tasks waiting" in output

# --- Test: do_start / do_stop ---
def test_scheduler_control(workspace):
    """测试启动/停止逻辑"""
    shell = tq.TaskQueueShell()
    
    # Test Start
    with patch("os.system") as mock_sys, \
         patch("time.sleep"): 
        shell.do_start("")
        cmd = mock_sys.call_args[0][0]
        assert "scheduler.sh 0" in cmd
        assert "nohup bash" in cmd

    # Test Stop
    with patch.object(shell, '_is_active', return_value=True), \
         patch("builtins.open", new_callable=MagicMock) as mock_open, \
         patch("os.system") as mock_sys:
             
        mock_open.return_value.__enter__.return_value.read.return_value = "9999"
        shell.do_stop("")
        mock_sys.assert_called_with("kill 9999")


def test_do_logs_shortcut(workspace):
    """测试 logs 快捷跳转指令"""
    shell = tq.TaskQueueShell()
    
    # 1. logs 无参数 -> 跳到 LOG_DIR/tasks
    expected_root = workspace / "logs" / "tasks"
    
    with patch("os.chdir") as mock_cd, \
         patch("os.system"): # 忽略 ls
        
        shell.do_logs("")
        mock_cd.assert_called_with(str(expected_root))
        
    # 2. logs sub -> 跳到 LOG_DIR/tasks/sub
    sub = expected_root / "subdir"
    sub.mkdir()
    
    with patch("os.chdir") as mock_cd, \
         patch("os.system"):
             
        shell.do_logs("subdir")
        mock_cd.assert_called_with(str(sub))

def test_pwd_command(workspace):
    """测试 pwd 指令：打印路径且不提交任务"""
    shell = tq.TaskQueueShell()
    
    # 1. 验证输出
    with patch("builtins.print") as mock_print:
        shell.do_pwd("")
        mock_print.assert_called_with(os.getcwd())
        
    # 2. 验证未提交任务 (队列文件不应被创建)
    q_file = workspace / "0.queue"
    assert not q_file.exists()

def test_exit_command(workspace):
    """测试 exit 指令"""
    shell = tq.TaskQueueShell()
    # exit 应该返回 True 以停止 cmdloop
    assert shell.do_exit("") is True

def test_prompt_path_abbreviation(workspace):
    """测试提示符中的路径缩写逻辑 (~)"""
    shell = tq.TaskQueueShell()
    
    fake_home = "/home/mock_user"
    
    with patch("os.path.expanduser", return_value=fake_home), \
         patch("os.getcwd") as mock_cwd:
        
        # Case 1: Home directory -> "~"
        mock_cwd.return_value = fake_home
        shell.update_prompt()
        # 验证颜色代码包含 \033[90m~\033[0m
        # 注意：Python 字符串字面量中 \033 和 \x1b 是一样的，测试中匹配其一即可
        assert "\x1b[90m~\x1b[0m" in shell.prompt
        
        # Case 2: Sub directory -> "~/project/src"
        mock_cwd.return_value = f"{fake_home}/project/src"
        shell.update_prompt()
        assert "\x1b[90m~/project/src\x1b[0m" in shell.prompt
        
        # Case 3: Root/External -> "/var/log"
        mock_cwd.return_value = "/var/log"
        shell.update_prompt()
        assert "\x1b[90m/var/log\x1b[0m" in shell.prompt