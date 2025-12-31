import os
import sys
import pytest
import json
import subprocess
from unittest.mock import patch, MagicMock

# --- 路径设置 ---
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

import tq
# 动态导入 queue_utils 以便进行单元测试 (Mocking)
import importlib.util
spec = importlib.util.spec_from_file_location("queue_utils", os.path.join(parent_dir, "queue_utils.py"))
queue_utils = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue_utils)

@pytest.fixture
def workspace(tmp_path):
    """创建基础工作环境"""
    d = tmp_path / "task_queue"
    d.mkdir()
    logs = d / "logs" / "tasks"
    logs.mkdir(parents=True)
    
    # Patch tq 的路径配置
    with patch("tq.BASE_DIR", str(d)), \
         patch("tq.TASK_LOG_DIR", str(logs)):
        yield d

# ==============================================================================
# 测试点 A: queue_utils.py 的 Stdout 协议安全性 (Strict Policy)
# ==============================================================================

def test_queue_utils_strict_stdout_policy(workspace):
    """
    [Critical] 验证 queue_utils.py 的 pop 操作严格遵守输出协议：
    1. 必须使用 sys.stdout.write 输出给 Shell。
    2. 绝对禁止使用 builtins.print (防止调试信息污染 Shell eval)。
    """
    q_file = workspace / "test.queue"
    task = {"p": 10, "g": 180, "t": "strict_test", "c": "echo hi"}
    q_file.write_text(json.dumps(task) + "\n")
    
    # Mock print 和 sys.stdout.write
    with patch("builtins.print") as mock_print, \
         patch("sys.stdout.write") as mock_write:
        
        # 调用模块函数
        queue_utils.pop_best_task(str(q_file))
        
        # [断言 1] 针对建议 A：旧代码使用 print，必须报错
        if mock_print.called:
            pytest.fail("❌ Test Failed: `print()` was called! You MUST use `sys.stdout.write` to avoid eval pollution.")
            
        # [断言 2] 针对新代码：必须调用 write
        assert mock_write.called, "❌ Test Failed: `sys.stdout.write` was NOT called."
        
        # [断言 3] 验证输出内容 (新代码会一次性输出)
        full_output = "".join([args[0] for args, _ in mock_write.call_args_list])
        assert "TQ_PRIO=10" in full_output


# ==============================================================================
# 测试点 C: tq.py 的 View Follow 功能
# ==============================================================================

def test_view_follow_command(workspace):
    """
    [Critical] 测试 view <id> -f 是否正确触发 tail -f
    """
    shell = tq.TaskQueueShell()
    shell.mode = 'LOGS'
    
    mock_log = workspace / "logs" / "tasks" / "test.log"
    mock_log.touch()
    shell.history_cache = [str(mock_log)]
    
    with patch("os.system") as mock_sys:
        # 调用带 -f 的命令
        shell.do_view("1 -f")
        
        # [断言] 针对建议 C：旧代码无法解析 -f，不会调用 system (或调用错误)
        if not mock_sys.called:
            pytest.fail("❌ Test Failed: `view 1 -f` did not trigger any system call. Parameter parsing failed?")
            
        # 验证命令是否为 tail -f
        call_args = mock_sys.call_args[0][0]
        assert "tail" in call_args and "-f" in call_args, \
            f"❌ Test Failed: Expected 'tail -f', got '{call_args}'"

def test_view_follow_interrupt(workspace):
    """
    [Critical] 测试在 view -f 过程中按下 Ctrl+C 是否能优雅退出
    """
    shell = tq.TaskQueueShell()
    shell.mode = 'LOGS'
    
    mock_log = workspace / "logs" / "tasks" / "test.log"
    mock_log.touch()
    shell.history_cache = [str(mock_log)]
    
    # 模拟 os.system 抛出 KeyboardInterrupt
    with patch("os.system", side_effect=KeyboardInterrupt) as mock_sys:
        with patch("builtins.print") as mock_print:
            try:
                # 尝试执行命令
                shell.do_view("1 -f")
            except ValueError:
                pytest.fail("❌ Test Failed: Old code raised ValueError (Invalid ID) instead of handling flags.")
            except KeyboardInterrupt:
                pytest.fail("❌ Test Failed: KeyboardInterrupt crashed the shell! It should be caught.")
            
            # [断言] 针对建议 C：必须捕获中断并打印提示
            # 只有 os.system 被调用了，才说明支持了 -f
            assert mock_sys.called, "❌ Test Failed: System was not called, so Interrupt logic was not tested."
            
            # 验证是否打印了 Stopped
            printed_logs = "".join([str(call) for call in mock_print.call_args_list])
            assert "Stopped" in printed_logs, "❌ Test Failed: Did not print '[Stopped]' after interrupt."