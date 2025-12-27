#!/bin/bash
# =========================================================
# 通用资源调度器 (v5.7 - Tag Support & Log Headers)
# =========================================================

set -m

if [ -z "$1" ]; then exit 1; fi

QUEUE_NAME="$1"
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

mkdir -p "$BASE_DIR/logs" "$TASK_LOG_DIR"
touch "$QUEUE_FILE"

if [ -e "$LOCK_FILE" ]; then
    EXISTING_PID=$(cat "$LOCK_FILE")
    if kill -0 "$EXISTING_PID" 2>/dev/null; then
        echo "[ERROR] Already running."
        exit 1
    else
        rm -f "$LOCK_FILE"
    fi
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"; exit' INT TERM EXIT

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$QUEUE_NAME] $1" >> "$LOG_FILE"; }

if [ "$IS_GPU_MODE" = true ]; then log "INFO: GPU MODE ($GPU_ID)."; else log "INFO: GENERIC MODE."; fi

terminate_task() {
    local pid="$1"
    local grace="$2"
    if ! ps -p "$pid" > /dev/null; then return; fi
    log "KILL: Sending SIGTERM to -$pid (Grace: ${grace}s)"
    kill -SIGTERM -- -"$pid" 2>/dev/null
    
    local counter=0
    local max_checks=$((grace / 2))
    while ps -p "$pid" > /dev/null; do
        sleep 2
        ((counter++))
        if [ "$counter" -ge "$max_checks" ]; then
            log "KILL: Timeout. SIGKILL."
            kill -SIGKILL -- -"$pid" 2>/dev/null
            break
        fi
    done
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

    # A: Yield
    if [ -n "$unmanaged_pid" ]; then
        if [ -n "$managed_pid" ]; then
            log "YIELD: Unmanaged PID $unmanaged_pid."
            curr_prio=$(sed -n '2p' "$RUNNING_FILE")
            curr_grace=$(sed -n '3p' "$RUNNING_FILE")
            curr_tag=$(sed -n '4p' "$RUNNING_FILE") # 从文件读取 Tag
            curr_cmd=$(sed -n '6p' "$RUNNING_FILE") # Cmd is now line 6
            
            terminate_task "$managed_pid" "$curr_grace"
            # 保留原始 Tag
            echo "${curr_prio}:${curr_grace}:${curr_tag}:${curr_cmd}" >> "$QUEUE_FILE"
            rm -f "$RUNNING_FILE"
        fi
        sleep 10
        continue

    # B: Preempt
    elif [ -f "$RUNNING_FILE" ] && [ -s "$QUEUE_FILE" ]; then
        curr_prio=$(sed -n '2p' "$RUNNING_FILE")
        best_prio=$(python3 "$UTILS_SCRIPT" peek_prio "$QUEUE_FILE")
        
        if [ "$best_prio" -lt "$curr_prio" ]; then
            log "PREEMPT: Queue($best_prio) > Current($curr_prio)."
            curr_grace=$(sed -n '3p' "$RUNNING_FILE")
            curr_tag=$(sed -n '4p' "$RUNNING_FILE")
            curr_cmd=$(sed -n '6p' "$RUNNING_FILE")
            
            terminate_task "$managed_pid" "$curr_grace"
            # 保留原始 Tag
            echo "${curr_prio}:${curr_grace}:${curr_tag}:${curr_cmd}" >> "$QUEUE_FILE"
            rm -f "$RUNNING_FILE"
            continue
        fi

    # C: Start
    elif [ ! -f "$RUNNING_FILE" ] && [ -s "$QUEUE_FILE" ]; then
        best_line=$(python3 "$UTILS_SCRIPT" pop "$QUEUE_FILE")
        if [ -z "$best_line" ]; then sleep 1; continue; fi

        best_prio=$(echo "$best_line" | cut -d: -f1)
        best_grace=$(echo "$best_line" | cut -d: -f2)
        best_tag=$(echo "$best_line" | cut -d: -f3)
        best_cmd=$(echo "$best_line" | cut -d: -f4-)
        
        if [ -z "$best_cmd" ]; then # 兼容旧数据
            best_cmd=$best_tag
            best_tag="default"
        fi

        # 安全过滤 Tag
        safe_tag=$(echo "$best_tag" | sed 's/[^a-zA-Z0-9._-]/_/g')

        TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
        TASK_LOG_FILE="$TASK_LOG_DIR/${QUEUE_NAME}_${TIMESTAMP}_${safe_tag}.log"
        
        log "START: Task (Prio: $best_prio, Tag: $best_tag). Output -> $TASK_LOG_FILE"
        
        # Write Header
        {
            echo "=========================================="
            echo " Task Metadata Log"
            echo "=========================================="
            echo " Start Time : $(date)"
            echo " Queue      : $QUEUE_NAME"
            echo " Tag        : $best_tag"
            echo " Priority   : $best_prio"
            echo " Grace      : ${best_grace}s"
            echo " Command    : $best_cmd"
            echo "=========================================="
            echo ""
            echo ">>> Task Output Follows >>>"
            echo ""
        } > "$TASK_LOG_FILE"

        if [ "$IS_GPU_MODE" = true ]; then
            (export CUDA_VISIBLE_DEVICES=$GPU_ID; eval "$best_cmd") >> "$TASK_LOG_FILE" 2>&1 &
        else
            (eval "$best_cmd") >> "$TASK_LOG_FILE" 2>&1 &
        fi
        
        new_pid=$!
        # 写入 6 行: PID, Prio, Grace, Tag, LogPath, Cmd
        echo -e "$new_pid\n$best_prio\n$best_grace\n$best_tag\n$TASK_LOG_FILE\n$best_cmd" > "$RUNNING_FILE"
    fi

    sleep 3
done