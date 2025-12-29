import os
import sys
import pytest
import shutil
import json
from unittest.mock import patch, MagicMock
from pathlib import Path

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path: sys.path.insert(0, parent_dir)

import tq

@pytest.fixture
def log_workspace(tmp_path):
    d = tmp_path / "task_queue"
    logs = d / "logs" / "tasks"
    logs.mkdir(parents=True)
    
    # Logs
    created_files = []
    for i in range(5):
        p = logs / f"0_2025010{i}_test.log"
        p.write_text(f"Log content {i}")
        os.utime(p, (i*1000, i*1000)) 
        created_files.append(str(p))
    created_files.sort(reverse=True)
    
    # Queue
    q_file = d / "0.queue"
    with open(q_file, 'w') as f:
        for i in range(5):
            f.write(json.dumps({"c": f"task_{i}", "p": 100}) + "\n")
    
    with patch("tq.BASE_DIR", str(d)), \
         patch("tq.TASK_LOG_DIR", str(logs)):
        yield d, logs, created_files

def test_safety_lock(log_workspace):
    """测试 Home 模式下 rm 被禁用 [Fixed]"""
    d, logs, files = log_workspace
    shell = tq.TaskQueueShell()
    
    assert shell.mode == 'HOME'
    
    with patch("builtins.print") as mock_print:
        shell.do_rm("1")
        # 检查所有打印调用，而不仅是最后一次
        all_output = "".join([str(call) for call in mock_print.call_args_list])
        assert "Safety Lock" in all_output

def test_unified_rm_logs(log_workspace):
    """测试 LOGS 模式下的 rm"""
    d, logs, files = log_workspace
    shell = tq.TaskQueueShell()
    
    shell.do_hist("") # Enter LOGS mode
    assert shell.mode == 'LOGS'
    
    shell.do_rm("2") # 删除 ID 2
    
    assert not os.path.exists(files[1])
    assert shell.history_cache[1] is None

def test_unified_rm_queue(log_workspace):
    """测试 QUEUE 模式下的 rm"""
    d, logs, files = log_workspace
    shell = tq.TaskQueueShell()
    q_file = d / "0.queue"
    
    shell.do_q("") # Enter QUEUE mode
    assert shell.mode == 'QUEUE'
    
    shell.do_rm("1 3") 
    
    with open(q_file, 'r') as f:
        lines = f.readlines()
    assert len(lines) == 3
    
    tasks = [json.loads(l)['c'] for l in lines]
    assert "task_0" not in tasks
    assert "task_2" not in tasks 
    assert "task_1" in tasks

def test_back_navigation(log_workspace):
    """测试 back 指令"""
    d, logs, files = log_workspace
    shell = tq.TaskQueueShell()
    
    shell.do_hist("")
    assert shell.mode == 'LOGS'
    
    shell.do_back("")
    assert shell.mode == 'HOME'
    assert shell.history_cache == []

def test_lcd_in_logs_mode_only(log_workspace):
    """测试 lcd 的模式限制"""
    d, logs, files = log_workspace
    shell = tq.TaskQueueShell()
    
    # HOME 模式下 lcd 无效
    shell.do_lcd("subdir")
    assert shell.log_context == Path(".")
    
    # LOGS 模式下 lcd 有效
    shell.do_hist("")
    sub = logs / "subdir"
    sub.mkdir()
    shell.do_lcd("subdir")
    assert shell.log_context == Path("subdir")

def test_unified_catg(log_workspace):
    """[Restored] 测试 catg 归档"""
    d, logs, files = log_workspace
    shell = tq.TaskQueueShell()
    
    shell.do_hist("") # Enter Logs
    shell.do_catg("1 archive_folder")
    
    archive_path = logs / "archive_folder" / os.path.basename(files[0])
    assert archive_path.exists()
    assert not os.path.exists(files[0]) 
    assert shell.history_cache[0] is None

def test_lcd_navigation_details(log_workspace):
    """[Restored] 测试 lcd 导航细节"""
    d, logs, files = log_workspace
    shell = tq.TaskQueueShell()
    
    sub = logs / "subdir"
    sub.mkdir()
    
    shell.do_hist("") # Enter Logs
    
    shell.do_lcd("subdir")
    assert shell.log_context == Path("subdir")
    
    shell.do_lcd("..")
    assert shell.log_context == Path(".")
    
    # 绝对路径跳转
    shell.do_lcd("subdir")
    shell.do_lcd("/")
    assert shell.log_context == Path(".")

def test_hist_drill_down_peek(log_workspace):
    """测试 hist <folder> 的窥视功能 (不改变 Context)"""
    d, logs, files = log_workspace
    shell = tq.TaskQueueShell()
    
    sub_dir = logs / "sub_test"
    sub_dir.mkdir()
    target_log = sub_dir / "target.log"
    target_log.write_text("content inside sub")
    
    # 1. 默认 Context 在 Root
    assert shell.log_context == Path(".")
    
    # 2. 执行 hist sub_test (进入 LOGS 模式 + 窥视)
    shell.do_hist("sub_test")
    
    # 验证 Mode
    assert shell.mode == 'LOGS'
    
    # 验证 Context (应该保持在 Root，没有变成 sub_test)
    assert shell.log_context == Path(".")
    
    # 验证 Cache (应该变成了 sub_test 下的文件)
    assert len(shell.history_cache) == 1
    assert shell.history_cache[0] == str(target_log)
    
    # 3. 验证 catg 操作的相对路径
    # 当前 Context 是 Root，View 是 sub_test
    # 移动 ID 1 (sub_test/target.log) 到 "dump"
    # 应该移动到 Root/dump (相对于 Context)
    shell.do_catg("1 dump")
    
    expected_path = logs / "dump" / "target.log"
    assert expected_path.exists()


def test_note_management(log_workspace):
    """测试日志注释功能 [Fixed]"""
    d, logs, files = log_workspace
    shell = tq.TaskQueueShell()
    shell.do_hist("") # Enter logs
    
    # 1. 添加注释
    target_id = "1" # files[0]
    target_file = os.path.basename(files[0])
    comment = "This is a great result"
    
    shell.do_note(f"{target_id} {comment}")
    
    # 验证 JSON 文件生成
    notes_file = logs / ".tq_notes.json"
    assert notes_file.exists()
    data = json.loads(notes_file.read_text())
    assert data[target_file] == comment
    
    # 2. 验证 catg 移动注释
    shell.do_catg(f"{target_id} best")
    
    # [FIX] 原目录的 Note 应该没了，文件可能被自动清理
    if notes_file.exists():
        data_old = json.loads(notes_file.read_text())
        assert target_file not in data_old
    else:
        assert True # 文件不存在说明已清理，符合预期
    
    # 新目录应该有 Note
    notes_new = logs / "best" / ".tq_notes.json"
    assert notes_new.exists()
    data_new = json.loads(notes_new.read_text())
    assert data_new[target_file] == comment
    
    # 3. 验证 rm 删除注释
    shell.do_lcd("best")
    shell.history_cache = [str(logs / "best" / target_file)]
    
    shell.do_rm("1")
    
    # 验证 Note 被删除
    if notes_new.exists():
        assert target_file not in json.loads(notes_new.read_text())
    else:
        assert True # 文件被自动清理了