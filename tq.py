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
        self.update_prompt()
        if 'libedit' in readline.__doc__: readline.parse_and_bind("bind ^I rl_complete")
        else: readline.parse_and_bind("tab: complete")

    def ensure_dirs(self):
        for d in [BASE_DIR, LOG_DIR, TASK_LOG_DIR]:
            if not os.path.exists(d): os.makedirs(d)

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
        cwd_name = os.path.basename(os.getcwd())
        env_str = f"({self.conda_env}) " if self.conda_env else ""
        is_running = self._is_active(self.current_queue)
        status_part = f"\033[92m(tq:{self.current_queue}|ON)\033[0m" if is_running else f"\033[91m(tq:{self.current_queue}|OFF)\033[0m"
        self.prompt = f'\033[93m{env_str}\033[0m\033[96m{cwd_name} {status_part} > '

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
                        # 新格式: PID, Prio, Grace, Tag, Log, Cmd (6行)
                        if len(lines) >= 6:
                            pid, prio, _, tag, log_path, cmd = lines[0], lines[1], lines[2], lines[3], lines[4], lines[5]
                            cmd_short = (cmd[:30] + '...') if len(cmd) > 30 else cmd
                            log_short = os.path.basename(log_path)
                            tag_display = f" [{tag}]" if tag != "default" else ""
                            status_str = f"\033[94m[RUN]\033[0m PID:{pid} Prio:{prio}{tag_display} | {cmd_short}"
                            log_info = f"\n         ├─ Log: \033[3m.../{log_short}\033[0m"
                        elif len(lines) >= 5: # 兼容旧版 (无 Tag)
                            status_str = f"\033[94m[RUN]\033[0m PID:{lines[0]} (Old Format)"
                except: pass
            
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
        print(f"{'ID':<4} | {'Prio':<5} | {'Grace':<5} | {'Tag':<10} | {'Command'}")
        print("-" * 75)
        with open(q_file, 'r') as f:
            for idx, line in enumerate(f.readlines()):
                parts = line.strip().split(':', 3)
                if len(parts) == 4:
                    print(f"{idx+1:<4} | {parts[0]:<5} | {parts[1]:<5} | {parts[2]:<10} | {parts[3]}")
                elif len(parts) == 3:
                    print(f"{idx+1:<4} | {parts[0]:<5} | {parts[1]:<5} | {'-':<10} | {parts[2]}")
        print("")

    def do_rm(self, arg):
        try:
            target_idx = int(arg) - 1
            q_file = os.path.join(BASE_DIR, f"{self.current_queue}.queue")
            if not os.path.exists(q_file): return
            with open(q_file, 'r+') as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    lines = f.readlines()
                    if 0 <= target_idx < len(lines):
                        removed = lines.pop(target_idx).strip()
                        f.seek(0); f.truncate()
                        f.writelines(lines)
                        print(f"[*] Removed: {removed}")
                    else: print(f"[!] Invalid ID.")
                finally: fcntl.flock(f, fcntl.LOCK_UN)
        except Exception as e: print(f"[!] Error: {e}")

    def do_purge(self, arg):
        target = arg.strip() if arg else self.current_queue
        q_file = os.path.join(BASE_DIR, f"{target}.queue")
        if os.path.exists(q_file):
            if input(f"[?] Delete all in '{target}'? (y/N) ").lower() == 'y':
                os.remove(q_file); print("[*] Cleared.")

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
            dt_str = datetime.datetime.fromtimestamp(os.path.getmtime(fpath)).strftime('%Y-%m-%d %H:%M:%S')
            size_kb = os.path.getsize(fpath) / 1024
            print(f"{idx+1:<4} | {dt_str:<19} | {size_kb:.1f} KB    | {fname}")
        print(f"\n(Tip: 'view <id>')\n")

    def do_view(self, arg):
        if not arg: print("[!] Usage: view <id>"); return
        try:
            idx = int(arg) - 1
            if not self.history_cache: print("[!] Run 'hist' first."); return
            if 0 <= idx < len(self.history_cache): os.system(f"less -R {self.history_cache[idx]}")
            else: print(f"[!] Invalid ID.")
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
\033[1mTask Queue (tq) v5.9 Manual\033[0m
===========================================
\033[93mNavigation & System:\033[0m
  st (status)   : Show summary of ALL queues
  ls / ll / cd  : File system navigation

\033[93mEnvironment Management (Conda-like):\033[0m
  env list             : List all Conda environments
  env activate <name>  : Switch session environment
  env <name>           : Shortcut to switch environment

\033[93mQueue Management:\033[0m
  <command>     : Submit task (e.g., 'python train.py -p 10')
  q             : List detailed tasks in current queue
  rm <id>       : Remove task by ID
  purge         : Remove ALL waiting tasks

\033[93mLogs & History:\033[0m
  hist          : List execution logs
  view <id>     : Open log with 'less'
  cat           : Show tail of CURRENTLY RUNNING task log
  tail          : Show SCHEDULER system log

\033[93mScheduler Control:\033[0m
  start / stop  : Start/Stop scheduler
  kill          : Immediately kill current running task

\033[93mSubmission Options:\033[0m
  -p <int>      : Priority (default 100)
  -g <int>      : Grace period (sec, default 180)
  -t <str>      : Tag for log (default 'default')
  -e <str>      : Conda Env (overrides session default)
""")

    def default(self, line):
        raw = line.strip()
        if not raw: return
        if raw == "EOF": return True
        if len(raw) < 2: return 

        prio, grace, tag = 100, 180, "default"
        # 默认使用当前 session 的环境
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

        # 4. 解析 Env (新增!)
        e_match = re.search(r'\s+(-e|--env)\s+(\S+)', raw)
        if e_match: 
            target_env = e_match.group(2)
            raw = raw.replace(e_match.group(0), "")
        
        cmd_content = raw.strip()
        final_cmd = cmd_content
        
        # 使用 target_env 进行封装，而不是 self.conda_env
        if target_env and target_env != "base":
            try:
                conda_base = os.popen("conda info --base 2>/dev/null").read().strip()
                if conda_base:
                    conda_sh = os.path.join(conda_base, "etc/profile.d/conda.sh")
                    if os.path.exists(conda_sh):
                        final_cmd = f"source {conda_sh} && conda activate {target_env} && {cmd_content}"
            except: pass

        q_file = os.path.join(BASE_DIR, f"{self.current_queue}.queue")
        
        try:
            with open(q_file, 'a') as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    f.write(f"{prio}:{grace}:{tag}:{final_cmd}\n")
                finally: fcntl.flock(f, fcntl.LOCK_UN)
            
            # 提示信息中增加 Env 显示
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

    def complete_cd(self, text, line, begidx, endidx): return self._complete_path(text, line, begidx, endidx)
    def complete_ls(self, text, line, begidx, endidx): return self._complete_path(text, line, begidx, endidx)
    def complete_ll(self, text, line, begidx, endidx): return self._complete_path(text, line, begidx, endidx)
    def completedefault(self, text, line, begidx, endidx): return self._complete_path(text, line, begidx, endidx)

if __name__ == '__main__':
    try: TaskQueueShell().cmdloop()
    except KeyboardInterrupt: print("\nBye.")