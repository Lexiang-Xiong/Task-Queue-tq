#!/bin/bash
# =========================================================
# 通用资源调度器 (v2.2 - Log Persistence Fix)
# =========================================================

set -m  # 开启作业控制，确保子进程在同一个进程组

if [ -z "$1" ]; then exit 1; fi

QUEUE_NAME="$1"
BASE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
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
        # [Fix] 获取受管任务的进程组 ID (PGID)
        managed_pgid=""
        if [ -n "$managed_pid" ] && ps -p "$managed_pid" > /dev/null; then
            managed_pgid=$(ps -o pgid= -p "$managed_pid" 2>/dev/null | tr -d ' ')
        fi

        # 查询显卡上的所有进程 PID
        gpu_pids=$(nvidia-smi --query-compute-apps=pid --format=csv,noheader,nounits -i "$GPU_ID" 2>/dev/null)
        
        for pid in $gpu_pids; do
            # 过滤掉空行和 managed_pid 本身
            if [ -n "$pid" ] && [ "$pid" != "$managed_pid" ]; then
                # [Fix] 核心修复：比较进程组 ID
                # 如果显卡上的进程属于当前任务的进程组，则视为"自己人"，不视为入侵
                gpu_pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')
                
                if [ -n "$managed_pgid" ] && [ "$gpu_pgid" == "$managed_pgid" ]; then
                    continue # 是子进程，安全忽略
                fi
                
                # PGID 不同，或者是外部进程，或者是 Xorg 等系统进程 -> 判定为入侵
                unmanaged_pid=$pid
                break
            fi
        done
    fi

    # A: Yield (抢占检测)
    if [ -n "$unmanaged_pid" ]; then
        if [ -n "$managed_pid" ]; then
            log "YIELD: Unmanaged PID $unmanaged_pid."
            curr_json=$(sed -n '4p' "$RUNNING_FILE")
            curr_grace=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['g'])" "$curr_json" 2>/dev/null)
            if [ -z "$curr_grace" ]; then curr_grace=180; fi 

            terminate_task "$managed_pid" "$curr_grace"
            
            if [ -n "$curr_json" ]; then
                # [Fix] 注入 'lp' (Log Path) 到 JSON 中以实现持久化
                curr_log=$(sed -n '3p' "$RUNNING_FILE")
                updated_json=$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); d['lp']=sys.argv[2]; print(json.dumps(d))" "$curr_json" "$curr_log" 2>/dev/null)
                if [ -z "$updated_json" ]; then updated_json="$curr_json"; fi
                echo "$updated_json" >> "$QUEUE_FILE"
            fi
            rm -f "$RUNNING_FILE"
        fi
        sleep 10
        continue

    # B: Preempt (优先级抢占)
    elif [ -f "$RUNNING_FILE" ] && [ -s "$QUEUE_FILE" ]; then
        curr_prio=$(sed -n '2p' "$RUNNING_FILE")
        if [ -z "$curr_prio" ]; then curr_prio=0; fi 
        
        best_prio=$(python3 "$UTILS_SCRIPT" peek_prio "$QUEUE_FILE")
        
        if [ "$best_prio" -lt "$curr_prio" ]; then
            log "PREEMPT: Queue($best_prio) > Current($curr_prio)."
            curr_json=$(sed -n '4p' "$RUNNING_FILE")
            curr_grace=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['g'])" "$curr_json" 2>/dev/null)
            if [ -z "$curr_grace" ]; then curr_grace=180; fi

            terminate_task "$managed_pid" "$curr_grace"
            
            if [ -n "$curr_json" ]; then
                # [Fix] 注入 'lp' (Log Path) 到 JSON 中以实现持久化
                curr_log=$(sed -n '3p' "$RUNNING_FILE")
                updated_json=$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); d['lp']=sys.argv[2]; print(json.dumps(d))" "$curr_json" "$curr_log" 2>/dev/null)
                if [ -z "$updated_json" ]; then updated_json="$curr_json"; fi
                echo "$updated_json" >> "$QUEUE_FILE"
            fi
            rm -f "$RUNNING_FILE"
            continue
        fi

    # C: Start (启动任务)
    elif [ ! -f "$RUNNING_FILE" ] && [ -s "$QUEUE_FILE" ]; then
        eval $(python3 "$UTILS_SCRIPT" pop "$QUEUE_FILE")
        
        if [ -z "$TQ_CMD" ]; then sleep 1; continue; fi

        safe_tag=$(echo "$TQ_TAG" | sed 's/[^a-zA-Z0-9._-]/_/g')
        
        # [Fix] 日志持久化逻辑：如果有旧路径且文件存在，则复用
        if [ -n "$TQ_LOG_PATH" ] && [ -f "$TQ_LOG_PATH" ]; then
            TASK_LOG_FILE="$TQ_LOG_PATH"
            log "RESUME: Task (Prio: $TQ_PRIO, Tag: $TQ_TAG). Appending to -> $TASK_LOG_FILE"
            {
                echo ""
                echo "=========================================="
                echo " RESUMED BY TQ SCHEDULER"
                echo "=========================================="
                echo " Resume Time: $(date)"
                echo "=========================================="
                echo ""
            } >> "$TASK_LOG_FILE"
        else
            TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
            TASK_LOG_FILE="$TASK_LOG_DIR/${QUEUE_NAME}_${TIMESTAMP}_${safe_tag}.log"
            log "START: Task (Prio: $TQ_PRIO, Tag: $TQ_TAG). Output -> $TASK_LOG_FILE"
            
            {
                echo "=========================================="
                echo " Task Metadata Log (V2)"
                echo "=========================================="
                echo " Start Time : $(date)"
                echo " Queue      : $QUEUE_NAME"
                echo " Tag        : $TQ_TAG"
                echo " Priority   : $TQ_PRIO"
                echo " Grace      : ${TQ_GRACE}s"
                echo " WorkDir    : ${TQ_WORKDIR:-"(None)"}"
                if [ -n "$TQ_GIT_HASH" ]; then
                    echo " Git Hash   : $TQ_GIT_HASH"
                    echo " -> Restore : git checkout $TQ_GIT_HASH"
                fi
                echo " Command    : $TQ_CMD"
                echo "=========================================="
                echo ""
                echo ">>> Task Output Follows >>>"
                echo ""
            } > "$TASK_LOG_FILE"
        fi

        (
            if [ -n "$TQ_WORKDIR" ] && [ -d "$TQ_WORKDIR" ]; then
                cd "$TQ_WORKDIR" || echo "[Scheduler] Failed to cd to $TQ_WORKDIR"
            fi
            
            if [ "$IS_GPU_MODE" = true ]; then
                export CUDA_VISIBLE_DEVICES=$GPU_ID
            fi
            
            eval "$TQ_CMD"
        ) >> "$TASK_LOG_FILE" 2>&1 &
        
        new_pid=$!
        echo -e "$new_pid\n$TQ_PRIO\n$TASK_LOG_FILE\n$TQ_JSON" > "$RUNNING_FILE"
    fi

    sleep 3
done