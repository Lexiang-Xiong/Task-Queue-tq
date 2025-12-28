#!/usr/bin/env python3
import cmd
import os
import sys
import re
import glob
import time
import datetime
import readline
import rlcompleter
import fcntl 
import json
import subprocess
from pathlib import Path

BASE_DIR = os.path.expanduser("~/task_queue")
LOG_DIR = os.path.join(BASE_DIR, "logs")
TASK_LOG_DIR = os.path.join(LOG_DIR, "tasks")
SCHEDULER_SCRIPT = os.path.join(BASE_DIR, "scheduler.sh")

class TaskQueueShell(cmd.Cmd):
    intro = 'Welcome to Task Queue Console v5.7 (Refined).\nType "man" for help.'
    
    def __init__(self):
        super().__init__()
        self.current_queue = "0"
        self.ensure_dirs()
        self.conda_env = os.environ.get("CONDA_DEFAULT_ENV", "base")
        self.history_cache = [] 
        self.log_context = Path(".")
        self.update_prompt()
        if 'libedit' in readline.__doc__: readline.parse_and_bind("bind ^I rl_complete")
        else: readline.parse_and_bind("tab: complete")

    def ensure_dirs(self):
        for d in [BASE_DIR, LOG_DIR, TASK_LOG_DIR]:
            if not os.path.exists(d): os.makedirs(d)

    def _parse_ids(self, args_list):
        """
        解析用户输入的 ID 列表。
        返回: (valid_indices, invalid_inputs)
        valid_indices 是 zero-based 的整数索引列表。
        """
        valid_indices = []
        invalid_inputs = []
        
        for arg in args_list:
            # 处理 "1,2,3" 这种逗号分隔的情况
            sub_args = arg.split(',')
            for s in sub_args:
                if not s.strip(): continue
                try:
                    idx = int(s) - 1 # 转为 0-based
                    if 0 <= idx < len(self.history_cache):
                        valid_indices.append(idx)
                    else:
                        invalid_inputs.append(s)
                except ValueError:
                    invalid_inputs.append(s)
        
        # 去重并排序 (从小到大)
        return sorted(list(set(valid_indices))), invalid_inputs

    def _get_cache_item(self, idx):
        """安全获取缓存项，检查是否已被标记为 None (已处理)"""
        if 0 <= idx < len(self.history_cache):
            item = self.history_cache[idx]
            if item is None:
                return None, "Moved/Deleted"
            return item, "OK"
        return None, "Out of Range"

    def _get_git_state(self, path):
        """
        获取当前代码状态的 Hash。
        1. 如果有未提交的修改 -> 创建悬空 Commit (git stash create)
        2. 如果工作区干净 -> 返回 HEAD Commit
        3. 不是 Git 仓库 -> 返回 None
        """
        try:
            # 检查是否是 git 仓库
            if not os.path.exists(os.path.join(path, ".git")):
                return None

            # 1. 尝试为未提交的变更(含Untracked)创建快照
            # git stash create 返回一个 hash，但不修改 refs/stash，完全隐形
            cmd = ['git', 'stash', 'create', '--include-untracked']
            stash_hash = subprocess.check_output(
                cmd, cwd=path, stderr=subprocess.DEVNULL
            ).decode().strip()
            
            if stash_hash:
                return stash_hash
            
            # 2. 如果工作区干净，获取 HEAD
            head_hash = subprocess.check_output(
                ['git', 'rev-parse', '--short', 'HEAD'],
                cwd=path, stderr=subprocess.DEVNULL
            ).decode().strip()
            
            return head_hash
        except:
            return None

    def _is_active(self, queue_name):
        lock_file = f"/tmp/scheduler_{queue_name}.lock"
        if os.path.exists(lock_file):
            try:
                with open(lock_file, 'r') as f: pid = int(f.read().strip())
                os.kill(pid, 0)
                return True
            except: return False
        return False
    
    def _print_dir_tree(self):
        """
        Prints a beautiful ASCII tree of directories, highlighting the current context.
        """
        root = Path(TASK_LOG_DIR)
        print(f"\033[1m[Directory Map]\033[0m")
        
        # 根节点显示
        if self.log_context == Path("."):
            print(f"\033[92m  .  <-- (Root)\033[0m")
        else:
            print(f"\033[94m  .\033[0m")

        def walk(directory, prefix=""):
            # 获取所有子目录 (不包含文件)
            try:
                subdirs = sorted([p for p in directory.iterdir() if p.is_dir()])
            except: return

            for i, d in enumerate(subdirs):
                is_last = (i == len(subdirs) - 1)
                connector = "  └── " if is_last else "  ├── "
                
                # 计算相对路径以便比较
                rel_path = d.relative_to(root)
                
                # 样式逻辑
                if rel_path == self.log_context:
                    # 当前位置：绿色 + 箭头
                    display = f"\033[92m{d.name}\033[0m \033[91m<-- YOU\033[0m"
                elif str(self.log_context).startswith(str(rel_path)):
                    # 父级路径：黄色
                    display = f"\033[93m{d.name}\033[0m"
                else:
                    # 其他目录：青色
                    display = f"\033[96m{d.name}\033[0m"
                
                print(f"{prefix}{connector}{display}")
                
                # 递归绘制
                new_prefix = prefix + ("      " if is_last else "  │   ")
                walk(d, new_prefix)

        walk(root)
        print("")

    def update_prompt(self):
        cwd_name = os.path.basename(os.getcwd())
        env_str = f"({self.conda_env}) " if self.conda_env else ""
        is_running = self._is_active(self.current_queue)
        status_part = f"\033[92m(tq:{self.current_queue}|ON)\033[0m" if is_running else f"\033[91m(tq:{self.current_queue}|OFF)\033[0m"
        
        # Log Context Indicator
        log_ctx_str = ""
        if self.log_context != Path("."):
            # 显示当前的目录名，如果是多级目录显示相对路径
            ctx_name = str(self.log_context)
            if len(ctx_name) > 15: ctx_name = "..." + ctx_name[-12:]
            log_ctx_str = f" \033[35m(L:{ctx_name})\033[0m"
        
        self.prompt = f'\033[93m{env_str}\033[0m\033[96m{cwd_name}{log_ctx_str} {status_part} > '

    def postcmd(self, stop, line):
        self.update_prompt()
        return stop

    # --- Commands ---
    def do_cd(self, arg):
        try: os.chdir(os.path.expanduser(arg) if arg else os.path.expanduser("~"))
        except Exception as e: print(f"[!] Error: {e}")

    def do_ls(self, arg): os.system("ls --color=auto " + arg)
    def do_ll(self, arg): os.system("ls -l --color=auto " + arg)

    def do_env(self, arg):
        """
        Manage Conda environments.
        Usage: 
          env list              : List all available environments
          env activate <name>   : Switch session environment
          env <name>            : Shorthand for activate
        """
        args = arg.split()
        
        # 1. 无参数：显示当前
        if not args:
            print(f"[*] Current session env: {self.conda_env}")
            return
        
        command = args[0]
        
        # 2. env list
        if command == "list":
            print("[*] invoking 'conda env list'...")
            os.system("conda env list")
            return
            
        # 3. env activate <name>
        if command == "activate":
            if len(args) < 2:
                print("[!] Usage: env activate <env_name>")
                return
            target = args[1]
        else:
            # 4. env <name> (快捷方式)
            target = command
            
        # 执行切换
        self.conda_env = target
        self.update_prompt()
        print(f"[*] Switched session env to: {self.conda_env}")

    def do_use(self, arg):
        """Switch queue."""
        if arg:
            self.current_queue = arg.strip()
            self.history_cache = [] 
            print(f"[*] Switched to queue: {self.current_queue}")

    def do_st(self, arg):
        """Show System Status."""
        print(f"\n=== System Status ({time.strftime('%H:%M:%S')}) ===")
        queues = set(os.path.basename(f).split('.')[0] for f in 
                     glob.glob(os.path.join(BASE_DIR, "*.queue")) + 
                     glob.glob(os.path.join(BASE_DIR, "*.running")))
        queues.add(self.current_queue)
        if not queues: print("[*] No queues found."); return

        for q in sorted(list(queues)):
            run_file = os.path.join(BASE_DIR, f"{q}.running")
            q_file = os.path.join(BASE_DIR, f"{q}.queue")
            is_active = self._is_active(q)
            
            status_str = "\033[91m[STOPPED]\033[0m" if not is_active else "\033[92m[IDLE]\033[0m"
            log_info = ""

            if is_active and os.path.exists(run_file):
                try:
                    with open(run_file) as f:
                        lines = f.read().splitlines()
                        # V2 Format: 
                        # Line 1: PID
                        # Line 2: Priority (int)
                        # Line 3: LogPath
                        # Line 4: JSON String
                        if len(lines) >= 4:
                            pid = lines[0]
                            prio = lines[1]
                            log_path = lines[2]
                            meta = json.loads(lines[3])
                            
                            tag = meta.get('t', 'default')
                            cmd = meta.get('c', '?')
                            workdir = meta.get('wd', '')
                            
                            cmd_short = (cmd[:30] + '...') if len(cmd) > 30 else cmd
                            log_short = os.path.basename(log_path)
                            
                            wd_info = f" \033[90m@ {os.path.basename(workdir)}\033[0m" if workdir else ""
                            tag_display = f" [{tag}]"
                            
                            status_str = f"\033[94m[RUN]\033[0m PID:{pid} Prio:{prio}{tag_display}{wd_info} | {cmd_short}"
                            log_info = f"\n         ├─ Log: \033[3m.../{log_short}\033[0m"
                        # Fallback for old running files (during upgrade)
                        elif len(lines) >= 1:
                             status_str = f"\033[94m[RUN]\033[0m PID:{lines[0]} (Legacy/Unknown)"
                except Exception as e: 
                    pass
            
            count = sum(1 for _ in open(q_file)) if os.path.exists(q_file) else 0
            pointer = "->" if q == self.current_queue else "  "
            print(f"{pointer} {q:<6} : {status_str}{log_info}")
            if count > 0: print(f"         └─ {count} tasks waiting.")
        print("")
    
    do_status = do_st

    def do_q(self, arg):
        """
        List tasks in the queue (Formatted table).
        """
        target = arg.strip() if arg else self.current_queue
        q_file = os.path.join(BASE_DIR, f"{target}.queue")
        
        if not os.path.exists(q_file) or os.path.getsize(q_file) == 0:
            print(f"[*] Queue '{target}' is empty."); return
            
        print(f"\n=== Queue Details: {target} ===")
        # 优化列宽
        print(f"{'ID':<4} | {'Prio':<5} | {'Grace':<5} | {'Tag':<12} | {'Command'}")
        print("-" * 80)
        
        import json
        try:
            with open(q_file, 'r') as f:
                for idx, line in enumerate(f.readlines()):
                    line = line.strip()
                    if not line: continue
                    
                    # 初始化默认值
                    p, g, t, c = "?", "?", "-", line
                    
                    # 1. 尝试 JSON 解析 (V6 协议)
                    if line.startswith('{'):
                        try:
                            task = json.loads(line)
                            p = task.get('p', 100)
                            g = task.get('g', 180)
                            t = task.get('t', 'default')
                            c = task.get('c', '?')
                        except:
                            pass # 解析失败保持原样
                    # 2. 旧格式兼容 (Legacy)
                    else:
                        parts = line.split(':', 3)
                        if len(parts) >= 3:
                            p = parts[0]
                            g = parts[1]
                            # 旧格式: Prio:Grace:Tag:Cmd 或 Prio:Grace:Cmd
                            if len(parts) == 4:
                                t = parts[2]
                                c = parts[3]
                            else:
                                c = parts[2]

                    # 格式化显示
                    # 截断过长的命令和Tag，防止表格错位
                    cmd_display = (c[:50] + '...') if len(c) > 50 else c
                    tag_display = (t[:12]) if len(t) > 12 else t
                    
                    print(f"{idx+1:<4} | {str(p):<5} | {str(g):<5} | {tag_display:<12} | {cmd_display}")
        except Exception as e:
            print(f"[!] Error reading queue: {e}")
            
        print("")

    def do_rm(self, arg):
        """
        Remove tasks from the QUEUE.
        Usage: rm <id> [id ...]
        """
        if not arg: print("[!] Usage: rm <id> [id ...]"); return
        
        q_file = os.path.join(BASE_DIR, f"{self.current_queue}.queue")
        if not os.path.exists(q_file): return

        # 解析 ID (复用 _parse_ids 逻辑，但针对队列行数)
        # 注意：这里不能直接用 self.history_cache，因为那是针对 log 的
        # 我们需要先读取队列行数
        try:
            with open(q_file, 'r') as f:
                lines = f.readlines()
        except: return

        # 临时构造一个假的 cache 来复用 _parse_ids 的解析逻辑
        # 或者我们手动解析，因为逻辑很简单
        args_list = arg.split()
        valid_indices = []
        
        for s in args_list:
            try:
                idx = int(s) - 1
                if 0 <= idx < len(lines):
                    valid_indices.append(idx)
                else:
                    print(f"[!] Invalid Task ID: {s}")
            except: pass
            
        if not valid_indices: return
        
        # 核心一致性逻辑：倒序排列
        valid_indices = sorted(list(set(valid_indices)), reverse=True)
        
        try:
            with open(q_file, 'w') as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    count = 0
                    for idx in valid_indices:
                        # 再次检查越界（虽然上面检查过，但为了稳健）
                        if idx < len(lines):
                            removed_line = lines.pop(idx).strip()
                            # 尝试解析 JSON 以显示友好的 Log
                            try:
                                import json
                                task = json.loads(removed_line)
                                desc = task.get('c', 'unknown')
                            except:
                                desc = removed_line
                            print(f"[*] Removed Task {idx+1}: {desc[:40]}...")
                            count += 1
                    
                    f.writelines(lines)
                    print(f"[*] Done. Removed {count} tasks from queue.")
                finally: fcntl.flock(f, fcntl.LOCK_UN)
        except Exception as e:
            print(f"[!] Error updating queue: {e}")

    def do_rmlog(self, arg):
        """
        Delete LOG files from history.
        Usage: rmlog <id> [id ...]
        """
        if not arg: print("[!] Usage: rmlog <id> [id ...]"); return
        
        if not self.history_cache:
            print("[!] History cache empty. Run 'hist' first.")
            return

        idxs, bads = self._parse_ids(arg.split())
        
        if bads:
            print(f"[!] Invalid IDs ignored: {bads}")
        
        if not idxs: return

        print(f"[*] Deleting {len(idxs)} logs...")
        
        count = 0
        # 这里 idxs 已经是从小到大排序的
        # 为了不破坏索引，我们只标记 None，不 pop，所以正序倒序都可以
        # 但为了习惯，保持原来的逻辑
        for idx in idxs:
            fpath, status = self._get_cache_item(idx)
            if fpath:
                try:
                    os.remove(fpath)
                    print(f"    - ID {idx+1}: Deleted.")
                    self.history_cache[idx] = None # 标记清除
                    count += 1
                except Exception as e:
                    print(f"    - ID {idx+1}: Failed ({e})")
            else:
                print(f"    - ID {idx+1}: Skipped ({status})")
        
        print(f"[*] Done. Removed {count} logs.")

    def do_lcd(self, arg):
        """
        Change directory inside the log store.
        Usage: lcd <folder> | lcd .. | lcd /
        """
        # 获取日志根目录的绝对路径
        base_path = Path(TASK_LOG_DIR).resolve()
        
        try:
            # 1. 计算目标绝对路径
            if not arg or arg.strip() == "/":
                # 情况 A: lcd / -> 直接回根目录
                new_abs = base_path
            else:
                # 情况 B: 相对移动
                current_abs = (base_path / self.log_context).resolve()
                new_abs = (current_abs / Path(arg)).resolve()
            
            # 2. 边界安全检查 (Jail)
            # 确保 new_abs 是 base_path 的子路径或本身
            if not str(new_abs).startswith(str(base_path)):
                print(f"[!] Cannot go above logs root.")
                return
            
            # 3. 存在性检查
            if not new_abs.exists() or not new_abs.is_dir():
                print(f"[!] Directory not found: {arg}")
                return
                
            # 4. 更新状态 (存储相对路径)
            self.log_context = new_abs.relative_to(base_path)
            
            # 提示与刷新
            location = self.log_context if str(self.log_context) != '.' else '(Root)'
            print(f"[*] Log Context: {location}")
            self.do_hist("")
            
        except Exception as e:
            print(f"[!] Error: {e}")

    def do_catg(self, arg):
        """
        Archive logs relative to current context.
        Usage: catg <id> ... <folder>
        """
        args = arg.split()
        if len(args) < 2:
            print("[!] Usage: catg <id> ... <folder_name>")
            return
            
        if not self.history_cache:
            print("[!] Run 'hist' first.")
            return

        dest_folder_name = args[-1]
        id_args = args[:-1]
        
        idxs, bads = self._parse_ids(id_args)
        if not idxs:
            print("[!] No valid IDs specified.")
            return

        # 目标目录是相对于当前 log_context 的
        base_path = Path(TASK_LOG_DIR)
        current_path = base_path / self.log_context
        dest_dir = current_path / dest_folder_name
        
        if not dest_dir.exists():
            try:
                dest_dir.mkdir(parents=True)
            except Exception as e:
                print(f"[!] Failed to create dir: {e}"); return
        
        print(f"[*] Moving {len(idxs)} logs to '{dest_folder_name}/' ...")
        
        import shutil
        count = 0
        for idx in idxs:
            fpath, status = self._get_cache_item(idx)
            if fpath:
                src_path = Path(fpath)
                dest_path = dest_dir / src_path.name
                try:
                    shutil.move(src_path, dest_path)
                    print(f"    - ID {idx+1} -> {dest_folder_name}/{src_path.name}")
                    self.history_cache[idx] = None
                    count += 1
                except Exception as e:
                    print(f"    - ID {idx+1}: Failed ({e})")
            else:
                print(f"    - ID {idx+1}: Skipped ({status})")
                
        print(f"[*] Done. Archived {count} files.")

    def do_logs(self, arg):
        """
        Quick cd to task logs directory or subfolder.
        Usage: logs [subfolder]
        """
        target_dir = TASK_LOG_DIR
        if arg:
            target_dir = os.path.join(TASK_LOG_DIR, arg)
            
        if os.path.exists(target_dir):
            os.chdir(target_dir)
            self.update_prompt()
            print(f"[*] Changed directory to: {target_dir}")
            os.system("ls -F --color=auto")
        else:
            print(f"[!] Directory not found: {target_dir}")

    def do_purge(self, arg):
        target = arg.strip() if arg else self.current_queue
        q_file = os.path.join(BASE_DIR, f"{target}.queue")
        if os.path.exists(q_file):
            if input(f"[?] Delete all in '{target}'? (y/N) ").lower() == 'y':
                os.remove(q_file); print("[*] Cleared.")

    def do_hist(self, arg):
        """
        Show directory tree and logs in current context.
        """
        if arg:
            print(f"[!] 'hist' no longer accepts arguments.")
            print(f"[!] Use 'lcd {arg}' to change view, then 'hist'.")
            return

        target_queue = self.current_queue
        base_path = Path(TASK_LOG_DIR)
        view_path = base_path / self.log_context
        
        # 1. 打印目录树
        self._print_dir_tree()

        # 2. 准备文件列表
        if self.log_context == Path("."):
            glob_pattern = f"{target_queue}_*.log"
            location_str = "(Root)"
        else:
            glob_pattern = "*.log"
            location_str = str(self.log_context)
            
        files = sorted(list(view_path.glob(glob_pattern)), 
                       key=lambda p: p.stat().st_mtime, 
                       reverse=True)
        
        self.history_cache = [str(p) for p in files]

        # 3. 打印精美表格
        print(f"\033[1m[Files in: {location_str}]\033[0m")
        
        if not files:
            print("  (No logs in this location)")
        else:
            # 表头
            print(f"\033[4m{'ID':<4} | {'Time':<19} | {'Size':<8} | {'File'}\033[0m")
            
            for idx, p in enumerate(files[:20]):
                fname = p.name
                dt_str = datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                size_kb = p.stat().st_size / 1024
                
                # 偶数行稍微变色（可选，这里保持简单）
                print(f"{idx+1:<4} | {dt_str:<19} | {size_kb:.1f} KB  | {fname}")
            
            if len(files) > 20:
                print(f"... and {len(files) - 20} more.")

        print(f"\n(Tip: 'lcd <folder>', 'view <id>', 'catg <id> <folder>', 'rmlog <id>')\n")

    def do_view(self, arg):
        if not arg: print("[!] Usage: view <id>"); return
        try:
            idx = int(arg) - 1
            if not self.history_cache: print("[!] Run 'hist' first."); return
            
            fpath, status = self._get_cache_item(idx)
            if fpath:
                os.system(f"less -R {fpath}")
            else:
                print(f"[!] Cannot view ID {idx+1}: {status}")
        except: print("[!] Invalid ID.")

    def do_start(self, arg):
        target = arg.strip() if arg else self.current_queue
        if self._is_active(target): print(f"[!] '{target}' running."); return
        lock = f"/tmp/scheduler_{target}.lock"
        if os.path.exists(lock): os.remove(lock)
        print(f"[*] Launching scheduler for '{target}'...")
        os.system(f"nohup bash {SCHEDULER_SCRIPT} {target} > {LOG_DIR}/scheduler_{target}.log 2>&1 &")
        for _ in range(20):
            if self._is_active(target): break
            time.sleep(0.1)

    def do_stop(self, arg):
        target = arg.strip() if arg else self.current_queue
        if self._is_active(target):
            try:
                with open(f"/tmp/scheduler_{target}.lock") as f: os.system(f"kill {f.read().strip()}")
            except: pass
            for _ in range(30):
                if not self._is_active(target): print(f"[*] Stopped."); break
                time.sleep(0.1)
        else: print("[!] Not running.")

    def do_kill(self, arg):
        target = arg.strip() if arg else self.current_queue
        run_file = os.path.join(BASE_DIR, f"{target}.running")
        if os.path.exists(run_file):
            try:
                with open(run_file) as f: pid = f.readline().strip()
                print(f"[*] Sending SIGTERM to Group -{pid}...")
                os.killpg(int(pid), 15)
            except: pass
        else: print("[!] No running task.")

    def do_cat(self, arg):
        target = arg.strip() if arg else self.current_queue
        run_file = os.path.join(BASE_DIR, f"{target}.running")
        if os.path.exists(run_file):
            with open(run_file) as f:
                lines = f.read().splitlines()
                # 兼容不同版本，LogPath 通常在倒数第二行或固定行，这里用新版的 Line 5 (index 4)
                if len(lines) >= 6: os.system(f"tail -n 20 {lines[4]}")
                elif len(lines) >= 5: os.system(f"tail -n 20 {lines[3]}")
    
    def do_tail(self, arg):
        target = arg.strip() if arg else self.current_queue
        os.system(f"tail -f {LOG_DIR}/scheduler_{target}.log")

    def do_man(self, arg):
        print("""
\033[1mTask Queue (tq) v6.0 (Protocol V2)\033[0m
===========================================
\033[93mNavigation & System:\033[0m
  st (status)       : Show summary of ALL queues
  ls / ll / cd      : File system navigation
  logs [folder]     : \033[96m[NEW]\033[0m Quick cd to task logs or subfolders

\033[93mEnvironment & Context:\033[0m
  env activate <name> : Switch session environment
  env <name>          : Shortcut to switch environment
  env list            : Show all valid environments 
  \033[90m* WorkDir and Git state are automatically captured on submission.\033[0m

\033[93mTask Management (Batch Supported):\033[0m
  lcd <folder>      : \033[96m[NEW]\033[0m Change log view (e.g., 'lcd best', 'lcd ..')
  hist              : List logs in current context
  view <id>         : Open log with 'less'
  rmlog <id> ...    : \033[96m[NEW]\033[0m Delete logs (e.g., 'rmlog 1 3')
  catg <id>... <dir>: Archive logs relative to current context (e.g., 'catg 1 2 best')

\033[93mQueue Operations:\033[0m
  <command>         : Submit task (e.g., 'python train.py -p 10')
  q                 : List waiting tasks
  rm <id> ...       : \033[96m[UPD]\033[0m Remove tasks from QUEUE
  purge             : Remove ALL waiting tasks

\033[93mScheduler Control:\033[0m
  start / stop      : Start/Stop scheduler
  kill              : Immediately kill current running task
  cat / tail        : Monitor running task / scheduler log

\033[93mSubmission Flags:\033[0m
  -p <int>          : Priority (lower is better, default 100)
  -g <int>          : Grace period (sec, default 180)
  -t <str>          : Tag for log (default 'default')
  -e <str>          : Conda Env (overrides session default)
""")

    def default(self, line):
        raw = line.strip()
        if not raw: return
        if raw == "EOF": return True
        if len(raw) < 2: return 
        
        import json # 确保引入

        prio, grace, tag = 100, 180, "default"
        target_env = self.conda_env 
        
        # 1. 解析 Priority
        p_match = re.search(r'\s+(-p|--priority)\s+(\d+)', raw)
        if p_match: prio = int(p_match.group(2)); raw = raw.replace(p_match.group(0), "")
        
        # 2. 解析 Grace
        g_match = re.search(r'\s+(-g|--grace)\s+(\d+)', raw)
        if g_match: grace = int(g_match.group(2)); raw = raw.replace(g_match.group(0), "")
        
        # 3. 解析 Tag
        t_match = re.search(r'\s+(-t|--tag)\s+(\S+)', raw)
        if t_match: tag = t_match.group(2); raw = raw.replace(t_match.group(0), "")

        # 4. 解析 Env
        e_match = re.search(r'\s+(-e|--env)\s+(\S+)', raw)
        if e_match: 
            target_env = e_match.group(2)
            raw = raw.replace(e_match.group(0), "")
        
        cmd_content = raw.strip()
        final_cmd = cmd_content
        
        # Conda 封装逻辑
        if target_env and target_env != "base":
            try:
                conda_base = os.popen("conda info --base 2>/dev/null").read().strip()
                if conda_base:
                    conda_sh = os.path.join(conda_base, "etc/profile.d/conda.sh")
                    if os.path.exists(conda_sh):
                        final_cmd = f"source {conda_sh} && conda activate {target_env} && {cmd_content}"
            except: pass

        q_file = os.path.join(BASE_DIR, f"{self.current_queue}.queue")
        
        # --- 核心修改区域 Start ---
        
        # 1. 获取当前工作目录
        wd = os.getcwd()
        
        # 2. 尝试获取 Git 状态 (依赖于前面添加的 _get_git_state 方法)
        git_hash = self._get_git_state(wd)

        # 3. 构造 V2 Task Object
        task_obj = {
            "p": prio,
            "g": grace,
            "t": tag,
            "c": final_cmd,
            "wd": wd,
            "git": git_hash  # 新增字段: 可能为 None, Commit Hash 或 Stash Hash
        }
        
        # --- 核心修改区域 End ---
        
        try:
            with open(q_file, 'a') as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    f.write(json.dumps(task_obj) + "\n")
                finally: fcntl.flock(f, fcntl.LOCK_UN)
            
            print(f"[+] Submitted to '{self.current_queue}' (Env: {target_env}, Tag: {tag})")
        except Exception as e: print(f"[!] Submission failed: {e}")

    def _complete_path(self, text, line, begidx, endidx):
        if not text: completions = glob.glob('*')
        else: completions = glob.glob(os.path.expanduser(text) + '*')
        return [c + "/" if os.path.isdir(c) else c for c in completions]
    
    def _get_conda_envs(self):
        """Helper to find available conda environments for tab completion."""
        envs = []
        try:
            # 1. 尝试从 conda info 获取 (最准确但慢)
            # 为了速度，我们这里简单扫描 envs 目录
            conda_base = os.popen("conda info --base 2>/dev/null").read().strip()
            if conda_base and os.path.exists(conda_base):
                envs_dir = os.path.join(conda_base, "envs")
                if os.path.exists(envs_dir):
                    envs = [d for d in os.listdir(envs_dir) if os.path.isdir(os.path.join(envs_dir, d))]
                # base
                envs.append("base")
        except: pass
        return envs
    
    def complete_env(self, text, line, begidx, endidx):
        """Tab completion for 'env' command."""
        # 补全子命令
        subcommands = ["list", "activate"]
        args = line.split()
        
        # 如果是输入第一个参数 (env l...)
        if len(args) == 1 or (len(args) == 2 and not line.endswith(' ')):
            # 混合补全：既补全子命令，也补全环境名（方便快捷方式）
            candidates = subcommands + self._get_conda_envs()
            return [s for s in candidates if s.startswith(text)]
            
        # 如果是输入第二个参数 (env activate m...)
        if len(args) >= 2 and args[1] == "activate":
            envs = self._get_conda_envs()
            return [e for e in envs if e.startswith(text)]
            
        return []
    
    def complete_hist(self, text, line, begidx, endidx):
        """Tab completion for hist command (directories only)."""
        base_path = Path(TASK_LOG_DIR)
        if not base_path.exists(): return []
        
        # 获取所有子目录
        subdirs = [p.name for p in base_path.iterdir() if p.is_dir()]
        
        return [d for d in subdirs if d.startswith(text)]
    
    def complete_lcd(self, text, line, begidx, endidx):
        """Tab completion for lcd command (Context-aware)."""
        base = Path(TASK_LOG_DIR).resolve()
        # 当前所在的物理目录
        curr = (base / self.log_context).resolve()
        
        # 解析用户输入: 分离出 "已输入的目录部分" 和 "正在输入的名称部分"
        # 例如 text="sub/ne" -> dir_part="sub", name_part="ne"
        dir_part, name_part = os.path.split(text)
        
        # 确定实际要扫描的目录
        try:
            target = (curr / dir_part).resolve()
        except: return []
        
        # 安全检查: 禁止扫描 logs 以外的目录 (Jail)
        if not str(target).startswith(str(base)):
            return []
            
        if not target.exists() or not target.is_dir():
            return []
            
        candidates = []
        try:
            # 扫描目标目录下的子目录
            for p in target.iterdir():
                if p.is_dir() and p.name.startswith(name_part):
                    # 构造补全候选项
                    # 必须包含 dir_part 前缀，否则 readline 会替换错误
                    val = os.path.join(dir_part, p.name) + "/"
                    candidates.append(val)
        except: pass
        
        return candidates

    def complete_cd(self, text, line, begidx, endidx): return self._complete_path(text, line, begidx, endidx)
    def complete_ls(self, text, line, begidx, endidx): return self._complete_path(text, line, begidx, endidx)
    def complete_ll(self, text, line, begidx, endidx): return self._complete_path(text, line, begidx, endidx)
    def completedefault(self, text, line, begidx, endidx): return self._complete_path(text, line, begidx, endidx)

if __name__ == '__main__':
    try: TaskQueueShell().cmdloop()
    except KeyboardInterrupt: print("\nBye.")