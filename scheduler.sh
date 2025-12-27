#!/bin/bash
# =========================================================
# 通用资源调度器 (v5.4 - External Python Utils)
# =========================================================

set -m # 开启作业控制

if [ -z "$1" ]; then 
    echo "[ERROR] Usage: $0 <queue_name>"
    exit 1
fi

QUEUE_NAME="$1"
# 确保这里路径正确
BASE_DIR="$HOME/task_queue" 
QUEUE_FILE="$BASE_DIR/${QUEUE_NAME}.queue"
RUNNING_FILE="$BASE_DIR/${QUEUE_NAME}.running"
LOG_FILE="$BASE_DIR/logs/scheduler_${QUEUE_NAME}.log"
LOCK_FILE="/tmp/scheduler_${QUEUE_NAME}.lock"
TASK_LOG_DIR="$BASE_DIR/logs/tasks"
UTILS_SCRIPT="$BASE_DIR/queue_utils.py"

if [[ "$QUEUE_NAME" =~ ^[0-9,]+$ ]]; then
    IS_GPU_MODE=true
    GPU_ID="$QUEUE_NAME"
else
    IS_GPU_MODE=false
fi

mkdir -p "$BASE_DIR/logs"
mkdir -p "$TASK_LOG_DIR"
touch "$QUEUE_FILE"

# --- 锁机制 ---
if [ -e "$LOCK_FILE" ]; then
    EXISTING_PID=$(cat "$LOCK_FILE")
    if kill -0 "$EXISTING_PID" 2>/dev/null; then
        echo "[ERROR] Scheduler for '$QUEUE_NAME' is already running (PID: $EXISTING_PID)."
        exit 1
    else
        echo "[WARN] Found stale lock file. Overwriting."
        rm -f "$LOCK_FILE"
    fi
fi
echo $$ > "$LOCK_FILE"

cleanup() {
    rm -f "$LOCK_FILE"
    echo "[INFO] Scheduler stopped."
    exit
}
trap cleanup INT TERM EXIT

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$QUEUE_NAME] $1" >> "$LOG_FILE"; }

if [ "$IS_GPU_MODE" = true ]; then
    log "INFO: Started in GPU MODE (Devices: $GPU_ID)."
else
    log "INFO: Started in GENERIC MODE."
fi

terminate_task() {
    local pid="$1"
    local grace="$2"
    
    if ! ps -p "$pid" > /dev/null; then return; fi

    log "AUDIT: Process tree for PGID -$pid:"
    ps -o pid,pgid,cmd -g "$pid" >> "$LOG_FILE" 2>&1

    log "KILL: Sending SIGTERM to process group -$pid (Grace: ${grace}s)"
    kill -SIGTERM -- -"$pid" 2>/dev/null

    local counter=0
    local check_interval=2
    local max_checks=$((grace / check_interval))

    while ps -p "$pid" > /dev/null; do
        sleep "$check_interval"
        ((counter++))
        if [ "$counter" -ge "$max_checks" ]; then
            log "KILL: Timeout (${grace}s). Sending SIGKILL."
            kill -SIGKILL -- -"$pid" 2>/dev/null
            break
        fi
    done
    log "KILL: Task $pid terminated."
}

while true; do
    managed_pid=""
    if [ -f "$RUNNING_FILE" ]; then
        managed_pid=$(head -n 1 "$RUNNING_FILE")
        if ! ps -p "$managed_pid" > /dev/null; then
            log "CLEANUP: Task $managed_pid finished naturally."
            rm -f "$RUNNING_FILE"
            managed_pid=""
        fi
    fi

    unmanaged_pid=""
    if [ "$IS_GPU_MODE" = true ]; then
        gpu_pids=$(nvidia-smi --query-compute-apps=pid --format=csv,noheader,nounits -i "$GPU_ID" 2>/dev/null)
        for pid in $gpu_pids; do
            if [ -n "$pid" ] && [ "$pid" != "$managed_pid" ]; then
                unmanaged_pid=$pid
                break
            fi
        done
    fi

    # --- 决策模块 ---

    # A: 避让 (Yield)
    if [ -n "$unmanaged_pid" ]; then
        if [ -n "$managed_pid" ]; then
            log "YIELD: Unmanaged PID $unmanaged_pid detected."
            curr_prio=$(sed -n '2p' "$RUNNING_FILE")
            curr_grace=$(sed -n '3p' "$RUNNING_FILE")
            curr_cmd=$(sed -n '5p' "$RUNNING_FILE")
            terminate_task "$managed_pid" "$curr_grace"
            echo "${curr_prio}:${curr_grace}:${curr_cmd}" >> "$QUEUE_FILE"
            rm -f "$RUNNING_FILE"
        fi
        sleep 10
        continue

    # B: 抢占 (Preempt)
    elif [ -f "$RUNNING_FILE" ] && [ -s "$QUEUE_FILE" ]; then
        curr_prio=$(sed -n '2p' "$RUNNING_FILE")
        
        # --- 变更点1：调用外部脚本检查优先级 ---
        best_prio=$(python3 "$UTILS_SCRIPT" peek_prio "$QUEUE_FILE")
        
        if [ "$best_prio" -lt "$curr_prio" ]; then
            log "PREEMPT: Queue($best_prio) > Current($curr_prio)."
            curr_grace=$(sed -n '3p' "$RUNNING_FILE")
            curr_cmd=$(sed -n '5p' "$RUNNING_FILE")
            terminate_task "$managed_pid" "$curr_grace"
            echo "${curr_prio}:${curr_grace}:${curr_cmd}" >> "$QUEUE_FILE"
            rm -f "$RUNNING_FILE"
            continue
        fi

    # C: 启动 (Start)
    elif [ ! -f "$RUNNING_FILE" ] && [ -s "$QUEUE_FILE" ]; then
        
        # --- 变更点2：调用外部脚本获取任务 ---
        best_line=$(python3 "$UTILS_SCRIPT" pop "$QUEUE_FILE")
        
        # 如果获取失败（空或出错），跳过
        if [ -z "$best_line" ]; then
            sleep 1
            continue
        fi

        best_prio=$(echo "$best_line" | cut -d: -f1)
        best_grace=$(echo "$best_line" | cut -d: -f2)
        best_cmd=$(echo "$best_line" | cut -d: -f3-)
        
        TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
        TASK_LOG_FILE="$TASK_LOG_DIR/${QUEUE_NAME}_${TIMESTAMP}.log"
        
        log "START: Task (Prio: $best_prio). Output -> $TASK_LOG_FILE"
        
        if [ "$IS_GPU_MODE" = true ]; then
            (export CUDA_VISIBLE_DEVICES=$GPU_ID; eval "$best_cmd") > "$TASK_LOG_FILE" 2>&1 &
        else
            (eval "$best_cmd") > "$TASK_LOG_FILE" 2>&1 &
        fi
        
        new_pid=$!
        echo -e "$new_pid\n$best_prio\n$best_grace\n$TASK_LOG_FILE\n$best_cmd" > "$RUNNING_FILE"
    fi

    sleep 3
done