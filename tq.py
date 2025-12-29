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
import shutil
from pathlib import Path

BASE_DIR = os.path.expanduser("~/task_queue")
LOG_DIR = os.path.join(BASE_DIR, "logs")
TASK_LOG_DIR = os.path.join(LOG_DIR, "tasks")
SCHEDULER_SCRIPT = os.path.join(BASE_DIR, "scheduler.sh")

class TaskQueueShell(cmd.Cmd):
    intro = 'Welcome to Task Queue Console v2.0 (Modal Edition).\nType "man" for help.'
    
    def __init__(self):
        super().__init__()
        self.current_queue = "0"
        self.ensure_dirs()
        self.conda_env = os.environ.get("CONDA_DEFAULT_ENV", "base")
        self.history_cache = [] 
        
        # [State Machine]
        self.mode = 'HOME' # Options: HOME, QUEUE, LOGS
        self.log_context = Path(".") 
        
        self.update_prompt()
        # 配置 Readline
        if 'libedit' in readline.__doc__:
            readline.parse_and_bind("bind ^I rl_complete")
        else:
            readline.parse_and_bind("tab: complete")
            
        # 关键修复：移除 '/' 作为分隔符，确保路径补全能获取完整字符串
        try:
            delims = readline.get_completer_delims()
            if '/' in delims:
                readline.set_completer_delims(delims.replace('/', ''))
        except: pass

    def ensure_dirs(self):
        for d in [BASE_DIR, LOG_DIR, TASK_LOG_DIR]:
            if not os.path.exists(d): os.makedirs(d)

    def _parse_ids(self, args_list):
        valid_indices = []
        invalid_inputs = []
        for arg in args_list:
            sub_args = arg.split(',')
            for s in sub_args:
                if not s.strip(): continue
                try:
                    idx = int(s) - 1
                    if 0 <= idx < len(self.history_cache):
                        valid_indices.append(idx)
                    else:
                        invalid_inputs.append(s)
                except ValueError:
                    invalid_inputs.append(s)
        return sorted(list(set(valid_indices))), invalid_inputs

    def _get_cache_item(self, idx):
        if 0 <= idx < len(self.history_cache):
            item = self.history_cache[idx]
            if item is None:
                return None, "Moved/Deleted"
            return item, "OK"
        return None, "Out of Range"

    def _load_notes(self, context_path):
        """Load notes from .tq_notes.json in the given directory."""
        notes_file = context_path / ".tq_notes.json"
        if not notes_file.exists(): return {}
        try:
            with open(notes_file, 'r') as f:
                return json.load(f)
        except: return {}

    def _save_notes(self, context_path, notes_data):
        """Save notes dictionary to .tq_notes.json."""
        notes_file = context_path / ".tq_notes.json"
        try:
            # 清理空值的 Key
            clean_data = {k: v for k, v in notes_data.items() if v}
            if not clean_data:
                if notes_file.exists(): os.remove(notes_file)
            else:
                with open(notes_file, 'w') as f:
                    json.dump(clean_data, f, indent=2)
        except Exception as e: print(f"[!] Failed to save notes: {e}")

    def _get_git_state(self, path):
        """
        获取当前代码状态的 Hash。
        无论在 Git 仓库的哪一层，都尝试捕获状态。
        """
        try:
            # 1. 检查是否在 Git 仓库中 (通过 git rev-parse --is-inside-work-tree)
            # 这一步替代了之前幼稚的 .git 目录检查
            subprocess.check_call(
                ['git', 'rev-parse', '--is-inside-work-tree'], 
                cwd=path, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL
            )
            
            # 2. 尝试为未提交的变更(含Untracked)创建快照
            # git stash create 返回一个 hash，但不修改 refs/stash，完全隐形
            cmd = ['git', 'stash', 'create', '--include-untracked']
            stash_hash = subprocess.check_output(
                cmd, cwd=path, stderr=subprocess.DEVNULL
            ).decode().strip()
            
            if stash_hash:
                return stash_hash
            
            # 3. 如果工作区干净，获取 HEAD
            head_hash = subprocess.check_output(
                ['git', 'rev-parse', '--short', 'HEAD'],
                cwd=path, stderr=subprocess.DEVNULL
            ).decode().strip()
            
            return head_hash
        except subprocess.CalledProcessError:
            # Not a git repo
            return None
        except Exception:
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

    def update_prompt(self):
        try:
            cwd = os.getcwd()
            home = os.path.expanduser("~")
            if cwd == home:
                cwd_display = "~"
            elif cwd.startswith(home + os.sep):
                cwd_display = "~" + cwd[len(home):]
            else:
                cwd_display = cwd
        except:
            cwd_display = "?"

        env_str = f"({self.conda_env}) " if self.conda_env else ""
        is_running = self._is_active(self.current_queue)
        
        # 1. 基础状态
        status_icon = "ON" if is_running else "OFF"
        status_color = "\033[92m" if is_running else "\033[91m"
        base_status = f"{status_color}(tq:{self.current_queue}|{status_icon})\033[0m"
        
        # 2. 模式状态
        mode_str = ""
        if self.mode == 'QUEUE':
            mode_str = f" \033[93m[QUEUE]\033[0m"
        elif self.mode == 'LOGS':
            loc = str(self.log_context) if str(self.log_context) != "." else "Root"
            if len(loc) > 15: loc = ".." + loc[-12:]
            mode_str = f" \033[96m[LOGS:{loc}]\033[0m"
        
        self.prompt = f'\033[93m{env_str}\033[0m\033[90m{cwd_display}\033[0m {base_status}{mode_str} > '

    def postcmd(self, stop, line):
        self.update_prompt()
        return stop
    
    def emptyline(self):
        pass

    # --- Mode Switching ---
    
    def do_back(self, arg):
        """Exit current mode and return to Home."""
        if self.mode == 'HOME':
            # 如果已经在 HOME，再次 back 退出程序
            if input("Exit tq? (y/N) ").lower() == 'y':
                return True
        else:
            self.mode = 'HOME'
            self.history_cache = [] # 清空缓存
            self.update_prompt()    # <--- [修复核心] 必须显式刷新提示符字符串
            print("[*] Returned to Dashboard.")

    def do_exit(self, arg):
        """Exit the tq console."""
        return True

    def do_ls(self, arg): os.system("ls --color=auto " + arg)
    def do_ll(self, arg): os.system("ls -l --color=auto " + arg)
    def do_cd(self, arg):
        try: os.chdir(os.path.expanduser(arg) if arg else os.path.expanduser("~"))
        except Exception as e: print(f"[!] Error: {e}")
    def do_pwd(self, arg):
        """Print current working directory."""
        print(os.getcwd())

    def do_logs(self, arg):
        """
        Quick cd to task logs directory in the SHELL.
        Usage: logs [subfolder]
        """
        target_dir = TASK_LOG_DIR
        if arg:
            target_dir = os.path.join(TASK_LOG_DIR, arg)
            
        if os.path.exists(target_dir):
            try:
                os.chdir(target_dir)
                self.update_prompt() # 刷新提示符中的 CWD
                print(f"[*] Shell CWD changed to: {target_dir}")
                os.system("ls -F --color=auto")
            except Exception as e:
                print(f"[!] Error: {e}")
        else:
            print(f"[!] Directory not found: {target_dir}")

    # --- MODE: QUEUE ---

    def do_q(self, arg):
        """Enter Queue Mode: List and manage waiting tasks."""
        self.mode = 'QUEUE'
        self.update_prompt()
        self._show_queue()

    def _show_queue(self):
        target = self.current_queue
        q_file = os.path.join(BASE_DIR, f"{target}.queue")
        
        # 即使为空也显示空表，确认进入了模式
        print(f"\n=== Queue Mode: {target} ===")
        print(f"{'ID':<4} | {'Prio':<5} | {'Grace':<5} | {'Tag':<12} | {'Command'}")
        print("-" * 80)
        
        # 无论是否有内容，我们都读取文件来填充 history_cache (用于 rm)
        self.history_cache = [] # 重置
        
        lines = []
        if os.path.exists(q_file):
            with open(q_file, 'r') as f:
                lines = f.readlines()
        
        # 缓存原始行，以便 rm 使用
        self.history_cache = lines
        
        if not lines:
            print("  (Queue is empty)")
        else:
            import json
            for idx, line in enumerate(lines):
                line = line.strip()
                if not line: continue
                
                p, g, t, c = "?", "?", "-", line
                if line.startswith('{'):
                    try:
                        task = json.loads(line)
                        p = task.get('p', 100)
                        g = task.get('g', 180)
                        t = task.get('t', 'default')
                        c = task.get('c', '?')
                    except: pass
                else:
                    parts = line.split(':', 3)
                    if len(parts) >= 3:
                        p, g = parts[0], parts[1]
                        if len(parts) == 4: t, c = parts[2], parts[3]
                        else: c = parts[2]

                cmd_display = (c[:50] + '...') if len(c) > 50 else c
                tag_display = (t[:12]) if len(t) > 12 else t
                print(f"{idx+1:<4} | {str(p):<5} | {str(g):<5} | {tag_display:<12} | {cmd_display}")
        
        print(f"\n\033[94m(Actions: 'rm <id>', 'purge', 'back(or ^C)')\n")

    # --- MODE: LOGS ---

    def do_hist(self, arg):
        """Enter Logs Mode or list specific folder."""
        self.mode = 'LOGS'
        self.update_prompt()
        
        view_path = None
        if arg:
            # 解析参数为路径
            base_path = Path(TASK_LOG_DIR).resolve()
            # 支持相对路径：相对于当前 log_context
            current_abs = (base_path / self.log_context).resolve()
            try:
                target_abs = (current_abs / Path(arg)).resolve()
                
                # 安全检查
                if not str(target_abs).startswith(str(base_path)):
                    print(f"[!] Cannot go above logs root.")
                    return
                if not target_abs.exists() or not target_abs.is_dir():
                    print(f"[!] Directory not found: {arg}")
                    return
                
                view_path = target_abs
            except Exception as e:
                print(f"[!] Error: {e}"); return

        self._show_logs(view_path_override=view_path)

    def do_lcd(self, arg):
        """Change directory in Logs Mode."""
        if self.mode != 'LOGS':
            print("[!] 'lcd' only works in LOGS mode. Type 'hist' first.")
            return
            
        base_path = Path(TASK_LOG_DIR).resolve()
        try:
            if not arg or arg.strip() == "/":
                new_abs = base_path
            else:
                current_abs = (base_path / self.log_context).resolve()
                new_abs = (current_abs / Path(arg)).resolve()
            
            if not str(new_abs).startswith(str(base_path)):
                print(f"[!] Cannot go above logs root.")
                return
            
            if not new_abs.exists() or not new_abs.is_dir():
                print(f"[!] Directory not found.")
                return
                
            self.log_context = new_abs.relative_to(base_path)
            self.update_prompt()
            self._show_logs() # 自动刷新
            
        except Exception as e: print(f"[!] Error: {e}")

    def _print_dir_tree(self, view_path=None):
        """
        Prints directory tree.
        Highlights:
        - YOU: Current Context (lcd location)
        - EYE: Current View (hist location, if different)
        """
        root = Path(TASK_LOG_DIR)
        # 归一化 view_path
        if view_path: view_path = view_path.relative_to(root) if view_path.is_absolute() else view_path
        
        print(f"\033[1m[Directory Map]\033[0m")
        
        # 根节点标记
        markers = []
        if self.log_context == Path("."): markers.append("\033[91m<-- YOU\033[0m")
        if view_path == Path(".") and self.log_context != Path("."): markers.append("\033[95m<-- EYE\033[0m")
        
        root_display = f"\033[92m  .\033[0m  {' '.join(markers)}" if markers else f"\033[94m  .\033[0m"
        print(root_display)

        def walk(directory, prefix=""):
            try: subdirs = sorted([p for p in directory.iterdir() if p.is_dir()])
            except: return
            for i, d in enumerate(subdirs):
                is_last = (i == len(subdirs) - 1)
                connector = "  └── " if is_last else "  ├── "
                rel_path = d.relative_to(root)
                
                # 样式逻辑
                display_name = f"\033[96m{d.name}\033[0m" # 默认青色
                
                # 路径匹配高亮
                is_context = (rel_path == self.log_context)
                is_view = (rel_path == view_path)
                
                if is_context or is_view:
                    display_name = f"\033[92m{d.name}\033[0m" # 高亮绿色
                elif str(self.log_context).startswith(str(rel_path)):
                    display_name = f"\033[93m{d.name}\033[0m" # 父级黄色

                # 标记后缀
                suffixes = []
                if is_context: suffixes.append("\033[91m<-- YOU\033[0m")
                if is_view and not is_context: suffixes.append("\033[95m<-- EYE\033[0m")
                
                suffix_str = (" " + " ".join(suffixes)) if suffixes else ""
                
                print(f"{prefix}{connector}{display_name}{suffix_str}")
                walk(d, prefix + ("      " if is_last else "  │   "))
        walk(root)
        print("")

    def _show_logs(self, view_path_override=None):
        target_queue = self.current_queue
        base_path = Path(TASK_LOG_DIR)
        
        if view_path_override:
            view_path = view_path_override
        else:
            view_path = base_path / self.log_context
            
        self._print_dir_tree(view_path)
        
        is_root = (view_path.resolve() == base_path.resolve())
        if is_root:
            glob_pattern = f"{target_queue}_*.log"
            location_str = "(Root)"
        else:
            glob_pattern = "*.log"
            location_str = str(view_path.relative_to(base_path))
            
        files = sorted(list(view_path.glob(glob_pattern)), 
                       key=lambda p: p.stat().st_mtime, reverse=True)
        
        self.history_cache = [str(p) for p in files]
        
        # [NEW] 加载注释
        notes = self._load_notes(view_path)

        print(f"\033[1m[Files in: {location_str}]\033[0m")
        if not files:
            print("  (No logs in this location)")
        else:
            # 调整列宽以适应 Comment
            # 定义列宽常量
            ID_WIDTH = 4
            TIME_WIDTH = 19
            SIZE_WIDTH = 8
            FILE_WIDTH = 30
            COMMENT_WIDTH = 40  # 增加评论列宽

            print(f"\033[4m{'ID':<{ID_WIDTH}} | {'Time':<{TIME_WIDTH}} | {'Size':<{SIZE_WIDTH}} | {'File':<{FILE_WIDTH}} | {'Comment':<{COMMENT_WIDTH}}\033[0m")
            for idx, p in enumerate(files[:20]):
                fname = p.name
                dt_str = datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                size_kb = p.stat().st_size / 1024
                
                note = notes.get(fname, "")
                fname_display = (fname[:FILE_WIDTH-3] + "..") if len(fname) > FILE_WIDTH-1 else fname
                
                if note:
                    note_display = (note[:COMMENT_WIDTH-3] + "..") if len(note) > COMMENT_WIDTH-1 else note
                    note_display = f"\033[33m{note_display:<{COMMENT_WIDTH}}\033[0m"
                else:
                    note_display = " " * COMMENT_WIDTH
                
                print(f"{idx+1:<{ID_WIDTH}} | {dt_str:<{TIME_WIDTH}} | {size_kb:.1f} KB  | {fname_display:<{FILE_WIDTH}} | {note_display}")
            if len(files) > 20: print(f"... and {len(files) - 20} more.")

        print(f"\n\033[94m(Actions: 'note <id> <txt>', 'rm', 'lcd', 'catg', 'view', 'back')\n")

    # --- UNIFIED COMMANDS ---

    def do_rm(self, arg):
        """
        Unified Remove: Behavior depends on current mode.
        - In QUEUE mode: Removes tasks from the queue.
        - In LOGS mode: Removes log files.
        """
        if self.mode == 'HOME':
            print("[!] Safety Lock: 'rm' is disabled in Dashboard.")
            print("[!] Please enter a mode first: type 'q' (Queue) or 'hist' (Logs).")
            return

        if not arg: print("[!] Usage: rm <id> [id ...]"); return
        
        # 1. QUEUE MODE LOGIC
        if self.mode == 'QUEUE':
            q_file = os.path.join(BASE_DIR, f"{self.current_queue}.queue")
            if not os.path.exists(q_file): return
            
            # 手动解析参数，因为 history_cache 存的是行内容，索引是对齐的
            args_list = arg.split()
            valid_indices = []
            for s in args_list:
                try:
                    idx = int(s) - 1
                    if 0 <= idx < len(self.history_cache):
                        valid_indices.append(idx)
                    else: print(f"[!] Invalid ID: {s}")
                except: pass
            
            if not valid_indices: return
            valid_indices = sorted(list(set(valid_indices)), reverse=True)
            
            try:
                # 重新读取文件以确保原子性，history_cache 仅用于 ID 验证
                with open(q_file, 'r+') as f:
                    fcntl.flock(f, fcntl.LOCK_EX)
                    lines = f.readlines()
                    count = 0
                    for idx in valid_indices:
                        if idx < len(lines):
                            removed = lines.pop(idx)
                            print(f"[*] Removed Task {idx+1}")
                            count += 1
                    f.seek(0); f.truncate()
                    f.writelines(lines)
                    fcntl.flock(f, fcntl.LOCK_UN)
                self._show_queue() # 刷新视图
            except Exception as e: print(f"[!] Error: {e}")

        # 2. LOGS MODE LOGIC
        elif self.mode == 'LOGS':
            idxs, bads = self._parse_ids(arg.split())
            if bads: print(f"[!] Invalid IDs: {bads}")
            if not idxs: return

            base_path = Path(TASK_LOG_DIR)
            curr_path = base_path / self.log_context
            notes = self._load_notes(curr_path)
            notes_changed = False
            
            count = 0
            for idx in idxs:
                fpath, status = self._get_cache_item(idx)
                if fpath:
                    try:
                        os.remove(fpath)
                        print(f"    - ID {idx+1}: Deleted.")
                        self.history_cache[idx] = None
                        fname = Path(fpath).name
                        if fname in notes:
                            del notes[fname]
                            notes_changed = True
                        count += 1
                    except Exception as e: 
                        print(f"    - ID {idx+1}: Failed ({e})")  # 显示具体异常
                        print(f"[!] Cannot view ID {idx+1}: {status}")  # 带状态说明
            
            if notes_changed: self._save_notes(curr_path, notes)
            print(f"[*] Removed {count} logs.")
            # 不自动刷新，保留上下文给用户看

    def do_catg(self, arg):
        if self.mode != 'LOGS':
            print("[!] 'catg' only works in LOGS mode."); return
        
        args = arg.split()
        if len(args) < 2: 
            print("[!] Usage: catg <id> ... <folder_name>"); return
        
        dest_name = args[-1]
        idxs, _ = self._parse_ids(args[:-1])
        if not idxs: return
        
        base_path = Path(TASK_LOG_DIR)
        curr_path = base_path / self.log_context
        dest_dir = curr_path / dest_name
        
        if not dest_dir.exists():
            try:
                dest_dir.mkdir(parents=True)
                print(f"[*] Created directory: {dest_name}/")
            except Exception as e:
                print(f"[!] Failed to create dir: {e}"); return
        
        # [NEW] 加载源和目标注释
        src_notes = self._load_notes(curr_path)
        dest_notes = self._load_notes(dest_dir)
        src_changed, dest_changed = False, False
        
        count = 0
        for idx in idxs:
            fpath, status = self._get_cache_item(idx)
            if fpath:
                try:
                    fname = Path(fpath).name
                    shutil.move(fpath, dest_dir / fname)
                    print(f"    - ID {idx+1} -> {dest_name}/")
                    self.history_cache[idx] = None
                    
                    # [NEW] 移动注释
                    if fname in src_notes:
                        dest_notes[fname] = src_notes[fname]
                        del src_notes[fname]
                        src_changed = True
                        dest_changed = True
                        
                    count += 1
                except Exception as e: 
                    print(f"    - ID {idx+1}: Failed ({e})")
        
        # [NEW] 保存注释
        if src_changed: self._save_notes(curr_path, src_notes)
        if dest_changed: self._save_notes(dest_dir, dest_notes)
        
        print(f"[*] Archived {count} files.")

    def do_view(self, arg):
        # view works in logs mode
        if self.mode != 'LOGS':
            print("[!] 'view' works in LOGS mode. Type 'hist'."); return
        elif not arg: print("[!] Usage: view <id>"); return
        try:
            idx = int(arg) - 1
            fpath, status = self._get_cache_item(idx)
            if fpath: os.system(f"less -R {fpath}")
            else: print(f"[!] Cannot view: {status}")
        except: print("[!] Invalid ID")

    def do_note(self, arg):
        """
        Add or modify a comment for a log file.
        Usage: note <id> <comment text>
        """
        if self.mode != 'LOGS':
            print("[!] 'note' only works in LOGS mode."); return
            
        args = arg.split(maxsplit=1)
        if len(args) < 2:
            print("[!] Usage: note <id> <comment text>"); return
            
        try:
            idx = int(args[0]) - 1
            text = args[1].strip()
        except: print("[!] Invalid ID."); return
        
        fpath, status = self._get_cache_item(idx)
        if not fpath:
            print(f"[!] Invalid file: {status}"); return
            
        # 更新 JSON
        base_path = Path(TASK_LOG_DIR)
        curr_path = (base_path / self.log_context)
        fname = Path(fpath).name
        
        notes = self._load_notes(curr_path)
        notes[fname] = text
        self._save_notes(curr_path, notes)
        
        print(f"[*] Note added to ID {args[0]}.")
        # 刷新显示
        self._show_logs()

    # ... (Other commands: do_env, do_start, do_stop, do_kill, do_cat, do_tail) ...
    def do_env(self, arg):
        args = arg.split()
        if not args: print(f"[*] Current session env: {self.conda_env}"); return
        
        if args[0] == "list": 
            # [FIX] 使用当前会话环境包裹命令，确保 '*' 显示正确
            raw_cmd = "conda env list"
            final_cmd = self._wrap_with_conda(raw_cmd, self.conda_env)
            os.system(final_cmd)
            return
            
        if args[0] == "activate":
            if len(args)<2: return
            self.conda_env = args[1]
        else: self.conda_env = args[0]
        self.update_prompt()
        print(f"[*] Switched to: {self.conda_env}")

    def _wrap_with_conda(self, cmd, env_name):
        """Wraps a command to run inside a specific Conda environment."""
        if not env_name or env_name == "base":
            return cmd
        try:
            # 尝试获取 conda 基础路径
            base = os.popen("conda info --base 2>/dev/null").read().strip()
            if base:
                sh = os.path.join(base, "etc/profile.d/conda.sh")
                if os.path.exists(sh):
                    # [FIX] 使用 '.' 代替 'source' 以兼容 /bin/sh (dash)
                    return f". {sh} && conda activate {env_name} && {cmd}"
        except: pass
        return cmd

    def do_use(self, arg):
        """Switch current queue context."""
        if arg:
            self.current_queue = arg.strip()
            self.history_cache = [] 
            self.update_prompt()
            print(f"[*] Switched to queue: {self.current_queue}")
            # 如果在队列模式，刷新视图
            if self.mode == 'QUEUE': self._show_queue()

    def do_st(self, arg):
        """Show System Status (Queues & Running Tasks)."""
        print(f"\n=== System Status ({time.strftime('%H:%M:%S')}) ===")
        # 扫描所有 .queue 和 .running 文件
        queues = set(os.path.basename(f).split('.')[0] for f in 
                     glob.glob(os.path.join(BASE_DIR, "*.queue")) + 
                     glob.glob(os.path.join(BASE_DIR, "*.running")))
        queues.add(self.current_queue)
        
        if not queues: print("[*] No queues found."); return

        for q in sorted(list(queues)):
            run_file = os.path.join(BASE_DIR, f"{q}.running")
            q_file = os.path.join(BASE_DIR, f"{q}.queue")
            is_active = self._is_active(q)
            
            # 状态显示
            status_str = "\033[91m[STOPPED]\033[0m" if not is_active else "\033[92m[IDLE]\033[0m"
            log_info = ""

            # 解析正在运行的任务 (V6 Protocol)
            if is_active and os.path.exists(run_file):
                try:
                    with open(run_file) as f:
                        lines = f.read().splitlines()
                        if len(lines) >= 4:
                            # Line 1: PID, 2: Prio, 3: LogPath, 4: JSON
                            pid, prio, log_path = lines[0], lines[1], lines[2]
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
                except: pass
            
            # 统计等待任务数
            count = sum(1 for _ in open(q_file)) if os.path.exists(q_file) else 0
            
            pointer = "->" if q == self.current_queue else "  "
            print(f"{pointer} {q:<6} : {status_str}{log_info}")
            if count > 0: print(f"         └─ {count} tasks waiting.")
        print("")
    
    do_status = do_st

    def do_purge(self, arg):
        """Remove ALL waiting tasks from current queue."""
        target = arg.strip() if arg else self.current_queue
        q_file = os.path.join(BASE_DIR, f"{target}.queue")
        if os.path.exists(q_file):
            if input(f"[?] Delete ALL tasks in '{target}'? (y/N) ").lower() == 'y':
                os.remove(q_file)
                print("[*] Cleared.")
                if self.mode == 'QUEUE': self._show_queue()

    def do_start(self, arg):
        target = arg.strip() if arg else self.current_queue
        if self._is_active(target): 
            print(f"[!] '{target}' already running."); return
        
        lock = f"/tmp/scheduler_{target}.lock"
        if os.path.exists(lock): os.remove(lock)
        
        print(f"[*] Launching scheduler for '{target}'...")
        os.system(f"nohup bash {SCHEDULER_SCRIPT} {target} > {LOG_DIR}/scheduler_{target}.log 2>&1 &")
        
        # 轮询检测（最多2秒），确保真正启动
        for _ in range(20):  
            if self._is_active(target): 
                print(f"[*] Scheduler '{target}' started successfully.")
                break
            time.sleep(0.1)
        else:
            print("[!] Warning: Scheduler may have failed to start. Check logs.")
        
        self.update_prompt()

    def do_stop(self, arg):
        target = arg.strip() if arg else self.current_queue
        if not self._is_active(target):
            print(f"[!] Scheduler '{target}' not running.")
            return
        
        try:
            with open(f"/tmp/scheduler_{target}.lock") as f: 
                os.system(f"kill {f.read().strip()}")
        except: pass
        
        # 轮询确认停止
        for _ in range(30):
            if not self._is_active(target): 
                print(f"[*] Scheduler '{target}' stopped.")
                break
            time.sleep(0.1)
        else:
            print("[!] Warning: Scheduler did not stop gracefully.")
        
        self.update_prompt()

    def do_kill(self, arg):
        target = arg.strip() if arg else self.current_queue
        run_file = os.path.join(BASE_DIR, f"{target}.running")
        if os.path.exists(run_file):
            with open(run_file) as f: pid = f.readline().strip()
            os.killpg(int(pid), 15)
            print("[*] Killed.")

    def do_cat(self, arg):
        target = arg.strip() if arg else self.current_queue
        run_file = os.path.join(BASE_DIR, f"{target}.running")
        if os.path.exists(run_file):
            with open(run_file) as f:
                lines = f.read().splitlines()
                if len(lines) >= 6: os.system(f"tail -n 20 {lines[4]}")
                elif len(lines) >= 5: os.system(f"tail -n 20 {lines[3]}")

    def do_tail(self, arg):
        target = arg.strip() if arg else self.current_queue
        os.system(f"tail -f {LOG_DIR}/scheduler_{target}.log")

    def do_man(self, arg):
        print("""
\033[1mTask Queue (tq) v2.0 (Modal Edition)\033[0m
===========================================
\033[93m1. Queue Management (Waiting Tasks):\033[0m
  use <id>          : Switch queue context (e.g., 'use 1')
  q                 : \033[1mEnter Queue Mode\033[0m (List waiting tasks)
  rm <ids>          : [In Queue Mode] Remove tasks by ID
  purge             : [In Queue Mode] Remove ALL task

\033[93m2. Log Management (History & Results):\033[0m
  hist              : \033[1mEnter Logs Mode\033[0m (Browse directory tree)
  rm <ids>          : [In Logs Mode] Delete log files
  lcd <folder>      : [In Logs Mode] Change virtual directory
  catg <id> <dir>   : [In Logs Mode] Archive logs to folder
  view <id>         : [In Logs Mode] View log content
  note <id> <txt>   : [In Logs Mode] Add comment to log
  logs [dir]        : Quick Shell CD to logs directory

\033[93m3. Scheduler Control (Daemon):\033[0m
  st (status)       : Show Global System Status
  start / stop      : Start/Stop the background scheduler
  kill              : Immediately kill the current running task
  cat / tail        : Peek at running task stdout / scheduler log

\033[93m4. Environment & Submission:\033[0m
  env <name>        : Switch Conda env for session
  env list          : Show all valid environments
  <command>         : Submit task (e.g., 'python train.py')
                      \033[90m(Auto-captures WorkDir & Git state)\033[0m

\033[93m5. Navigation:\033[0m
  back (or ^C)      : Return to Dashboard
  exit (or ^D)      : Exit tq application
  ls / ll / cd      : Standard file system navigation
  pwd               : Print working directory
""")

    def default(self, line):
        raw = line.strip()
        if not raw: return
        if raw == "EOF": return True
        if raw == "..": self.do_back(""); return
        
        import json
        prio, grace, tag, target_env = 100, 180, "default", self.conda_env
        
        p_match = re.search(r'\s+(-p|--priority)\s+(\d+)', raw)
        if p_match: prio = int(p_match.group(2)); raw = raw.replace(p_match.group(0), "")
        g_match = re.search(r'\s+(-g|--grace)\s+(\d+)', raw)
        if g_match: grace = int(g_match.group(2)); raw = raw.replace(g_match.group(0), "")
        t_match = re.search(r'\s+(-t|--tag)\s+(\S+)', raw)
        if t_match: tag = t_match.group(2); raw = raw.replace(t_match.group(0), "")
        e_match = re.search(r'\s+(-e|--env)\s+(\S+)', raw)
        if e_match: target_env = e_match.group(2); raw = raw.replace(e_match.group(0), "")
        
        cmd_content = raw.strip()
        if not cmd_content: return
        
        # [FIX] 使用统一的封装逻辑
        final_cmd = self._wrap_with_conda(cmd_content, target_env)

        q_file = os.path.join(BASE_DIR, f"{self.current_queue}.queue")
        wd = os.getcwd()
        git_hash = self._get_git_state(wd)
        
        task_obj = {"p": prio, "g": grace, "t": tag, "c": final_cmd, "wd": wd, "git": git_hash}
        try:
            with open(q_file, 'a') as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                f.write(json.dumps(task_obj) + "\n")
                fcntl.flock(f, fcntl.LOCK_UN)
            print(f"[+] Submitted to '{self.current_queue}'")
            if self.mode == 'QUEUE': self._show_queue()
        except Exception as e: print(f"[!] Failed: {e}")

    # --- Completions ---
    def _complete_log_dirs(self, text):
        """Helper to complete directory paths inside log context."""
        base = Path(TASK_LOG_DIR).resolve()
        curr = (base / self.log_context).resolve()
        
        # 解析输入: text="sub/ne" -> dir="sub", name="ne"
        dir_part, name_part = os.path.split(text)
        
        try:
            target = (curr / dir_part).resolve()
        except: return []
        
        # 安全检查 (Jail)
        if not str(target).startswith(str(base)) or not target.is_dir():
            return []
            
        candidates = []
        try:
            for p in target.iterdir():
                if p.is_dir() and p.name.startswith(name_part):
                    # 补全结果必须包含 dir_part，否则 readline 会替换错误
                    val = os.path.join(dir_part, p.name) + "/"
                    candidates.append(val)
        except: pass
        return candidates
    

    def complete_lcd(self, text, line, begidx, endidx):
        if self.mode != 'LOGS': return []
        base = Path(TASK_LOG_DIR).resolve()
        curr = (base / self.log_context).resolve()
        dir_part, name_part = os.path.split(text)
        try: target = (curr / dir_part).resolve()
        except: return []
        if not str(target).startswith(str(base)) or not target.is_dir(): return []
        candidates = []
        try:
            for p in target.iterdir():
                if p.is_dir() and p.name.startswith(name_part):
                    val = os.path.join(dir_part, p.name) + "/"
                    candidates.append(val)
        except: pass
        return candidates
    
    def complete_hist(self, text, line, begidx, endidx):
        # hist 可以在任何模式下使用，无需检查 mode
        return self._complete_log_dirs(text)

    def _complete_path(self, text, line, begidx, endidx):
        if not text: completions = glob.glob('*')
        else: completions = glob.glob(os.path.expanduser(text) + '*')
        return [c + "/" if os.path.isdir(c) else c for c in completions]
    
    def _get_conda_envs(self):
        envs = []
        try:
            base = os.popen("conda info --base 2>/dev/null").read().strip()
            if base:
                edir = os.path.join(base, "envs")
                if os.path.exists(edir): envs = [d for d in os.listdir(edir) if os.path.isdir(os.path.join(edir, d))]
                envs.append("base")
        except: pass
        return envs
    
    def complete_env(self, text, line, begidx, endidx):
        # 解析已输入部分
        args = line.split()
        
        # 场景1: 输入第一个参数时（如 'env l...'）
        if len(args) == 1 or (len(args) == 2 and not line.endswith(' ')):
            # 同时补全子命令和环境名
            subcommands = ["list", "activate"]
            envs = self._get_conda_envs()
            return [s for s in (subcommands + envs) if s.startswith(text)]
        
        # 场景2: 输入activate后的环境名
        if len(args) >= 2 and args[1] == "activate":
            envs = self._get_conda_envs()
            return [e for e in envs if e.startswith(text)]
        
        return []

    def complete_cd(self, text, line, begidx, endidx): return self._complete_path(text, line, begidx, endidx)
    def completedefault(self, text, line, begidx, endidx): return self._complete_path(text, line, begidx, endidx)

if __name__ == '__main__':
    shell = TaskQueueShell()
    while True:
        try:
            shell.cmdloop()
            print("\nbye.\n")
            break # Normal exit
        except KeyboardInterrupt:
            print("^C")
            if shell.mode != 'HOME':
                shell.do_back(None) # 返回首页，现在 do_back 会刷新 prompt 了
                shell.intro = None  
            else:
                print("") 
                shell.intro = None