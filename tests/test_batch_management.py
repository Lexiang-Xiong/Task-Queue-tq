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
    
    # Queue (用于测试 rm)
    q_file = d / "0.queue"
    with open(q_file, 'w') as f:
        for i in range(5):
            f.write(json.dumps({"c": f"task_{i}", "p": 100}) + "\n")
    
    with patch("tq.BASE_DIR", str(d)), \
         patch("tq.TASK_LOG_DIR", str(logs)):
        yield d, logs, created_files

def test_hist_cache_consistency(log_workspace):
    d, logs, files = log_workspace
    shell = tq.TaskQueueShell()
    shell.do_hist("")
    assert len(shell.history_cache) == 5
    assert shell.history_cache[0] == files[0]

def test_rmlog_batch_and_stability(log_workspace):
    """测试批量删除日志 (rmlog)"""
    d, logs, files = log_workspace
    shell = tq.TaskQueueShell()
    shell.do_hist("")
    
    # 1. 删除 ID 2
    shell.do_rmlog("2")
    
    assert not os.path.exists(files[1]) 
    assert shell.history_cache[1] is None 
    assert shell.history_cache[0] is not None 
    
    # 2. 再次尝试
    shell.do_rmlog("2")
    
    # 3. 删除 ID 3
    shell.do_rmlog("3")
    assert not os.path.exists(files[2])
    assert shell.history_cache[2] is None

def test_queue_rm_batch(log_workspace):
    """测试批量删除任务队列 (rm)"""
    d, logs, files = log_workspace
    shell = tq.TaskQueueShell()
    q_file = d / "0.queue"
    
    # 队列里有 5 个任务: task_0, task_1, task_2, task_3, task_4
    # 用户 ID: 1..5
    
    # 删除 ID 2 (task_1) 和 ID 4 (task_3)
    # 倒序删除应该先删 4，再删 2，互不影响
    shell.do_rm("2 4")
    
    with open(q_file, 'r') as f:
        lines = f.readlines()
        
    assert len(lines) == 3
    tasks = [json.loads(l)['c'] for l in lines]
    
    assert "task_0" in tasks
    assert "task_1" not in tasks # Removed
    assert "task_2" in tasks
    assert "task_3" not in tasks # Removed
    assert "task_4" in tasks

def test_catg_archive(log_workspace):
    d, logs, files = log_workspace
    shell = tq.TaskQueueShell()
    shell.do_hist("")
    
    shell.do_catg("1 archive_folder")
    
    archive_path = logs / "archive_folder" / os.path.basename(files[0])
    assert archive_path.exists()
    assert not os.path.exists(files[0]) 
    assert shell.history_cache[0] is None 

def test_catg_batch(log_workspace):
    d, logs, files = log_workspace
    shell = tq.TaskQueueShell()
    shell.do_hist("")
    
    shell.do_catg("4 5 batch_folder")
    
    dest = logs / "batch_folder"
    assert (dest / os.path.basename(files[3])).exists()
    assert (dest / os.path.basename(files[4])).exists()
    
    assert shell.history_cache[3] is None
    assert shell.history_cache[4] is None

def test_lcd_navigation(log_workspace):
    d, logs, files = log_workspace
    shell = tq.TaskQueueShell()
    
    sub = logs / "subdir"
    sub.mkdir()
    
    shell.do_lcd("subdir")
    assert shell.log_context == Path("subdir")
    
    shell.do_lcd("..")
    assert shell.log_context == Path(".")
    
    shell.do_lcd("..")
    assert shell.log_context == Path(".") 
    
    shell.do_lcd("subdir")
    shell.do_lcd("/")
    assert shell.log_context == Path(".")

def test_hist_with_lcd(log_workspace):
    """测试结合 lcd 的 hist"""
    d, logs, files = log_workspace
    shell = tq.TaskQueueShell()
    
    sub_dir = logs / "sub_test"
    sub_dir.mkdir()
    target_log = sub_dir / "target.log"
    target_log.write_text("content inside sub")
    
    # 进入目录
    shell.do_lcd("sub_test")
    # 自动列出 (do_lcd 内部调用了 hist)，这里手动确认缓存
    assert len(shell.history_cache) == 1
    assert shell.history_cache[0] == str(target_log)
    
    # 验证 view
    with patch("os.system") as mock_sys:
        shell.do_view("1")
        args = mock_sys.call_args[0][0]
        assert str(target_log) in args