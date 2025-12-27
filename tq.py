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

BASE_DIR = os.path.expanduser("~/task_queue")
LOG_DIR = os.path.join(BASE_DIR, "logs")
TASK_LOG_DIR = os.path.join(LOG_DIR, "tasks")
SCHEDULER_SCRIPT = os.path.join(BASE_DIR, "scheduler.sh")

class TaskQueueShell(cmd.Cmd):
    intro = 'Welcome to Task Queue Console v5.6.\nType "man" for help.'
    
    def __init__(self):
        super().__init__()
        self.current_queue = "0"
        self.ensure_dirs()
        self.conda_env = os.environ.get("CONDA_DEFAULT_ENV", "base")
        self.history_cache = [] 
        self.update_prompt()
        
        # Tab 补全
        if 'libedit' in readline.__doc__:
            readline.parse_and_bind("bind ^I rl_complete")
        else:
            readline.parse_and_bind("tab: complete")

    def ensure_dirs(self):
        if not os.path.exists(BASE_DIR): os.makedirs(BASE_DIR)
        if not os.path.exists(LOG_DIR): os.makedirs(LOG_DIR)
        if not os.path.exists(TASK_LOG_DIR): os.makedirs(TASK_LOG_DIR)

    # --- 核心辅助函数：检查调度器是否存活 ---
    def _is_active(self, queue_name):
        """Check if scheduler process is actually running."""
        lock_file = f"/tmp/scheduler_{queue_name}.lock"
        if os.path.exists(lock_file):
            try:
                with open(lock_file, 'r') as f:
                    pid = int(f.read().strip())
                # 信号 0 不发送信号，仅检查进程是否存在
                os.kill(pid, 0)
                return True
            except (ProcessLookupError, ValueError, OSError):
                return False
        return False

    def update_prompt(self):
        cwd_name = os.path.basename(os.getcwd())
        env_str = f"({self.conda_env}) " if self.conda_env else ""
        
        # 动态状态指示器
        is_running = self._is_active(self.current_queue)
        
        if is_running:
            # 绿色 ON
            status_part = f"\033[92m(tq:{self.current_queue}|ON)\033[0m"
        else:
            # 红色 OFF
            status_part = f"\033[91m(tq:{self.current_queue}|OFF)\033[0m"

        self.prompt = f'\033[93m{env_str}\033[0m\033[96m{cwd_name} {status_part} > '

    # 每次命令执行后自动刷新 Prompt 状态
    def postcmd(self, stop, line):
        self.update_prompt()
        return stop

    # --- 1. 基础导航 ---
    def do_cd(self, arg):
        if not arg: target = os.path.expanduser("~")
        else: target = os.path.expanduser(arg)
        try:
            os.chdir(target)
        except Exception as e: print(f"[!] Error: {e}")

    def do_ls(self, arg): os.system("ls --color=auto " + arg)
    def do_ll(self, arg): os.system("ls -l --color=auto " + arg)

    # --- 2. 队列管理 ---
    def do_use(self, arg):
        """Switch queue."""
        if arg:
            self.current_queue = arg.strip()
            self.history_cache = [] 
            print(f"[*] Switched to queue: {self.current_queue}")
            # Prompt will update via postcmd

    def do_st(self, arg):
        """Show System Status."""
        print(f"\n=== System Status ({time.strftime('%H:%M:%S')}) ===")
        files = glob.glob(os.path.join(BASE_DIR, "*.queue"))
        runnings = glob.glob(os.path.join(BASE_DIR, "*.running"))
        queues = set(os.path.basename(f).split('.')[0] for f in files + runnings)
        
        # 确保当前队列一定显示，即使文件还没创建
        queues.add(self.current_queue)

        if not queues: print("[*] No queues found."); return

        for q in sorted(list(queues)):
            run_file = os.path.join(BASE_DIR, f"{q}.running")
            q_file = os.path.join(BASE_DIR, f"{q}.queue")
            
            # --- 状态判断逻辑 ---
            is_active = self._is_active(q)
            
            status_str = ""
            log_info = ""

            if not is_active:
                # 调度器没跑
                status_str = "\033[91m[STOPPED]\033[0m" # Red
                # 如果有 residue running file，提示一下
                if os.path.exists(run_file):
                    status_str += " (Found stale .running)"
            else:
                # 调度器在跑，检查有没有在运行任务
                if os.path.exists(run_file):
                    try:
                        with open(run_file) as f:
                            lines = f.read().splitlines()
                            if len(lines) >= 5:
                                cmd_short = (lines[4][:30] + '...') if len(lines[4]) > 30 else lines[4]
                                log_short = os.path.basename(lines[3])
                                status_str = f"\033[94m[RUN]\033[0m PID:{lines[0]} Prio:{lines[1]} | {cmd_short}"
                                log_info = f"\n         ├─ Log: \033[3m.../{log_short}\033[0m"
                            else:
                                status_str = "\033[94m[RUN]\033[0m (Old Format)"
                    except: 
                        status_str = "\033[94m[RUN]\033[0m (Read Error)"
                else:
                    # 调度器活着，但没任务
                    status_str = "\033[92m[IDLE]\033[0m" # Green
            
            count = sum(1 for _ in open(q_file)) if os.path.exists(q_file) else 0
            pointer = "->" if q == self.current_queue else "  "
            
            print(f"{pointer} {q:<6} : {status_str}{log_info}")
            if count > 0: print(f"         └─ {count} tasks waiting.")
        print("")
    
    do_status = do_st

    def do_q(self, arg):
        target = arg.strip() if arg else self.current_queue
        q_file = os.path.join(BASE_DIR, f"{target}.queue")
        if not os.path.exists(q_file) or os.path.getsize(q_file) == 0:
            print(f"[*] Queue '{target}' is empty."); return
        print(f"\n=== Queue Details: {target} ===")
        print(f"{'ID':<4} | {'Prio':<5} | {'Grace':<5} | {'Command'}")
        print("-" * 60)
        with open(q_file, 'r') as f:
            for idx, line in enumerate(f.readlines()):
                parts = line.strip().split(':', 2)
                if len(parts) == 3:
                    print(f"{idx+1:<4} | {parts[0]:<5} | {parts[1]:<5} | {parts[2].strip()}")
        print("")

    def do_rm(self, arg):
        try:
            target_idx = int(arg) - 1
            q_file = os.path.join(BASE_DIR, f"{self.current_queue}.queue")
            if not os.path.exists(q_file): return
            with open(q_file, 'r') as f: lines = f.readlines()
            if 0 <= target_idx < len(lines):
                removed = lines.pop(target_idx).strip()
                with open(q_file, 'w') as f: f.writelines(lines)
                print(f"[*] Removed: {removed}")
            else: print(f"[!] Invalid ID.")
        except: print("[!] Usage: rm <id>")

    def do_purge(self, arg):
        target = arg.strip() if arg else self.current_queue
        q_file = os.path.join(BASE_DIR, f"{target}.queue")
        if os.path.exists(q_file):
            if input(f"[?] Delete all in '{target}'? (y/N) ").lower() == 'y':
                os.remove(q_file)
                print("[*] Cleared.")
        else: print("[*] Already empty.")

    # --- 3. 日志与历史 ---
    def do_hist(self, arg):
        target = arg.strip() if arg else self.current_queue
        pattern = os.path.join(TASK_LOG_DIR, f"{target}_*.log")
        files = glob.glob(pattern)
        files.sort(key=os.path.getmtime, reverse=True)
        self.history_cache = files 

        if not files: print(f"[*] No history for '{target}'."); return

        print(f"\n=== Task History: {target} (Showing last 10) ===")
        print(f"{'ID':<4} | {'Time':<19} | {'Size':<10} | {'File'}")
        print("-" * 65)
        for idx, fpath in enumerate(files[:10]):
            fname = os.path.basename(fpath)
            mtime = os.path.getmtime(fpath)
            dt_str = datetime.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
            size_kb = os.path.getsize(fpath) / 1024
            print(f"{idx+1:<4} | {dt_str:<19} | {size_kb:.1f} KB    | {fname}")
        print(f"\n(Tip: 'view <id>')\n")

    def do_view(self, arg):
        if not arg: print("[!] Usage: view <id>"); return
        try:
            idx = int(arg) - 1
            if not self.history_cache: print("[!] Run 'hist' first."); return
            if 0 <= idx < len(self.history_cache):
                os.system(f"less -R {self.history_cache[idx]}")
            else: print(f"[!] Invalid ID.")
        except ValueError: print("[!] ID must be a number.")

    # 4. 调度控制
    def do_start(self, arg):
        target = arg.strip() if arg else self.current_queue
        # check active
        if self._is_active(target):
            print(f"[!] '{target}' is already running.")
            return
        
        # Cleanup stale lock if exists but inactive
        lock = f"/tmp/scheduler_{target}.lock"
        if os.path.exists(lock):
            print(f"[*] Cleaning stale lock for '{target}'...")
            os.remove(lock)

        print(f"[*] Launching scheduler for '{target}'...")
        os.system(f"nohup bash {SCHEDULER_SCRIPT} {target} > {LOG_DIR}/scheduler_{target}.log 2>&1 &")
        # Prompt updates automatically via postcmd

        for _ in range(20): # 20 * 0.1s = 2s
            if self._is_active(target):
                break # 检测到启动成功，立即退出循环
            time.sleep(0.1)

    def do_stop(self, arg):
        target = arg.strip() if arg else self.current_queue
        
        if self._is_active(target):
            lock = f"/tmp/scheduler_{target}.lock"
            # 读取 PID 并杀掉
            try:
                with open(lock) as f: 
                    pid = f.read().strip()
                os.system(f"kill {pid}")
                print(f"[*] Signal sent to {pid}...")
            except:
                pass # 文件可能已经被删了

            for _ in range(30):
                if not self._is_active(target):
                    print(f"[*] Scheduler stopped successfully.")
                    break
                time.sleep(0.1)
        else:
            print("[!] Not running.")

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

    # 5. 辅助
    def do_cat(self, arg):
        target = arg.strip() if arg else self.current_queue
        run_file = os.path.join(BASE_DIR, f"{target}.running")
        if os.path.exists(run_file):
            with open(run_file) as f:
                lines = f.read().splitlines()
                if len(lines) >= 4: os.system(f"tail -n 20 {lines[3]}")
    
    def do_tail(self, arg):
        target = arg.strip() if arg else self.current_queue
        os.system(f"tail -f {LOG_DIR}/scheduler_{target}.log")

    def do_man(self, arg):
        print("""
\033[1mTask Queue (tq) v5.5 Manual\033[0m
===========================================
\033[93mNavigation & System:\033[0m
st (status)   : Show summary of ALL queues
use <id>      : Switch current queue (e.g., 'use 0,1' or 'use cpu')
ls / ll / cd  : File system navigation

\033[93mQueue Management:\033[0m
<command>     : Submit task (e.g., 'python train.py -p 10')
q             : List detailed tasks in current queue (shows IDs)
rm <id>       : Remove task by ID
purge         : Remove ALL waiting tasks

\033[93mLogs & History:\033[0m
hist          : List execution logs for current queue
view <id>     : Open specific log with 'less' (supports scrolling)
cat           : Show tail of CURRENTLY RUNNING task log
tail          : Show SCHEDULER system log

\033[93mScheduler Control:\033[0m
start / stop  : Start/Stop scheduler background process
kill          : Immediately kill current running task
""")

    def default(self, line):
        raw = line.strip()
        if not raw: return
        if raw == "EOF": return True
        if len(raw) < 2: return 

        prio, grace = 100, 180
        p_match = re.search(r'\s+(-p|--priority)\s+(\d+)', raw)
        if p_match: prio = int(p_match.group(2)); raw = raw.replace(p_match.group(0), "")
        g_match = re.search(r'\s+(-g|--grace)\s+(\d+)', raw)
        if g_match: grace = int(g_match.group(2)); raw = raw.replace(g_match.group(0), "")
        
        cmd_content = raw.strip()
        final_cmd = cmd_content
        
        if self.conda_env and self.conda_env != "base":
            try:
                conda_base = os.popen("conda info --base 2>/dev/null").read().strip()
                if conda_base:
                    conda_sh = os.path.join(conda_base, "etc/profile.d/conda.sh")
                    if os.path.exists(conda_sh):
                        final_cmd = f"source {conda_sh} && conda activate {self.conda_env} && {cmd_content}"
            except: pass

        q_file = os.path.join(BASE_DIR, f"{self.current_queue}.queue")
        with open(q_file, 'a') as f: f.write(f"{prio}:{grace}:{final_cmd}\n")
        print(f"[+] Submitted to '{self.current_queue}' (Prio:{prio})")

    # Tab Completion
    def _complete_path(self, text, line, begidx, endidx):
        if not text: completions = glob.glob('*')
        else: completions = glob.glob(os.path.expanduser(text) + '*')
        final = []
        for c in completions:
            if os.path.isdir(c): c += "/"
            final.append(c)
        return final

    def complete_cd(self, text, line, begidx, endidx): return self._complete_path(text, line, begidx, endidx)
    def complete_ls(self, text, line, begidx, endidx): return self._complete_path(text, line, begidx, endidx)
    def complete_ll(self, text, line, begidx, endidx): return self._complete_path(text, line, begidx, endidx)
    def completedefault(self, text, line, begidx, endidx): return self._complete_path(text, line, begidx, endidx)

if __name__ == '__main__':
    try: TaskQueueShell().cmdloop()
    except KeyboardInterrupt: print("\nBye.")