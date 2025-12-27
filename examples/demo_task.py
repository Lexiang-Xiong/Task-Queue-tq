import time
import signal
import sys
import os

stop_requested = False

def signal_handler(signum, frame):
    global stop_requested
    print(f"\n[Task] Received signal {signum}. Stopping gracefully...", flush=True)
    stop_requested = True

# 注册 SIGTERM (调度器发出的信号)
signal.signal(signal.SIGTERM, signal_handler)

print(f"[Task] Started with PID: {os.getpid()}", flush=True)
total_epochs = 100

for epoch in range(total_epochs):
    if stop_requested:
        print(f"[Task] Saving checkpoint at epoch {epoch}...", flush=True)
        time.sleep(2) # 模拟保存耗时
        print("[Task] Save complete. Exiting.", flush=True)
        sys.exit(0)
    
    print(f"[Task] Training epoch {epoch}/{total_epochs}...", flush=True)
    time.sleep(2) # 模拟训练耗时
