import os
import sys
import pytest
import json
import subprocess
from unittest.mock import patch

# 导入 tq 模块
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

import tq

@pytest.fixture
def git_workspace(tmp_path):
    """
    创建一个包含 .git 的真实工作区
    """
    repo_dir = tmp_path / "my_project"
    repo_dir.mkdir()
    
    # 初始化 git
    subprocess.run(["git", "init"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir)
    subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_dir)
    
    # 初始提交
    (repo_dir / "main.py").write_text("print('v1')")
    subprocess.run(["git", "add", "."], cwd=repo_dir)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir)
    
    # 获取初始 HEAD
    head_v1 = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], cwd=repo_dir
    ).decode().strip()
    
    # 模拟 tq 数据目录
    tq_dir = tmp_path / "task_queue"
    tq_dir.mkdir()
    (tq_dir / "logs" / "tasks").mkdir(parents=True)
    
    # Patch 路径
    with patch("tq.BASE_DIR", str(tq_dir)), \
         patch("tq.LOG_DIR", str(tq_dir / "logs")), \
         patch("tq.TASK_LOG_DIR", str(tq_dir / "logs" / "tasks")):
        yield repo_dir, tq_dir, head_v1

def test_clean_git_state(git_workspace):
    """测试：干净的工作区应该返回 HEAD Hash"""
    repo_dir, tq_dir, head_v1 = git_workspace
    
    shell = tq.TaskQueueShell()
    
    # 切换到 git 目录
    cwd_backup = os.getcwd()
    os.chdir(repo_dir)
    try:
        shell.default("python main.py")
    finally:
        os.chdir(cwd_backup)
        
    # 验证队列
    q_file = tq_dir / "0.queue"
    with open(q_file, 'r') as f:
        task = json.loads(f.readline())
        
    assert task['git'] == head_v1

def test_dirty_git_state_snapshot(git_workspace):
    """测试：脏工作区（未提交修改）应该生成新的 Snapshot Hash"""
    repo_dir, tq_dir, head_v1 = git_workspace
    
    # 1. 修改文件，制造 Dirty State
    (repo_dir / "main.py").write_text("print('v2 - dirty')")
    
    shell = tq.TaskQueueShell()
    
    cwd_backup = os.getcwd()
    os.chdir(repo_dir)
    try:
        shell.default("python main.py")
    finally:
        os.chdir(cwd_backup)
        
    # 2. 验证
    q_file = tq_dir / "0.queue"
    with open(q_file, 'r') as f:
        task = json.loads(f.readline())
    
    captured_hash = task['git']
    
    # 必须捕获到了 Hash
    assert captured_hash is not None
    assert len(captured_hash) > 0
    # 且不能是 HEAD (因为有修改)
    assert captured_hash != head_v1
    
    print(f"\n[Git Snapshot Test] HEAD: {head_v1} -> Snapshot: {captured_hash}")
    
    # 3. 验证这个 Snapshot 是有效的 Commit 对象
    # git cat-file -t <hash> 应该返回 'commit'
    obj_type = subprocess.check_output(
        ["git", "cat-file", "-t", captured_hash], cwd=repo_dir
    ).decode().strip()
    
    assert obj_type == "commit"
    
    # 4. 验证这个 Snapshot 包含我们的修改
    # git show <hash>:main.py
    file_content = subprocess.check_output(
        ["git", "show", f"{captured_hash}:main.py"], cwd=repo_dir
    ).decode()
    
    assert "v2 - dirty" in file_content

def test_non_git_directory(git_workspace):
    """测试：非 Git 目录应返回 None"""
    repo_dir, tq_dir, _ = git_workspace
    
    # 在 repo 之外创建一个普通目录
    normal_dir = repo_dir.parent / "normal_folder"
    normal_dir.mkdir()
    
    shell = tq.TaskQueueShell()
    
    cwd_backup = os.getcwd()
    os.chdir(normal_dir)
    try:
        shell.default("ls")
    finally:
        os.chdir(cwd_backup)
        
    q_file = tq_dir / "0.queue"
    with open(q_file, 'r') as f:
        task = json.loads(f.readline())
        
    assert task['git'] is None

def test_git_deep_directory(git_workspace):
    """测试：在 Git 仓库的深层子目录下提交，应能向上查找 Git 根目录"""
    repo_dir, tq_dir, head_v1 = git_workspace
    
    # 1. 创建深层目录结构
    deep_dir = repo_dir / "src" / "deep" / "module"
    deep_dir.mkdir(parents=True)
    
    shell = tq.TaskQueueShell()
    
    cwd_backup = os.getcwd()
    os.chdir(deep_dir)
    try:
        # 在深层目录提交
        shell.default("python script.py")
    finally:
        os.chdir(cwd_backup)
        
    # 2. 验证队列
    q_file = tq_dir / "0.queue"
    with open(q_file, 'r') as f:
        task = json.loads(f.readline())
        
    # 应该能捕获到 HEAD
    assert task['git'] == head_v1
    # WorkDir 应该是深层目录
    assert task['wd'] == str(deep_dir)