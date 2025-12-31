#!/usr/bin/env python3
import sys
import os
import json
import shlex

# ==============================================================================
# OUTPUT POLICY:
# STDOUT is STRICTLY reserved for Shell `eval` communication.
# Any debug information MUST go to STDERR.
# ==============================================================================

def parse_line(line):
    line = line.strip()
    if not line: return None
    
    # 1. JSON 解析
    if line.startswith('{'):
        try:
            task = json.loads(line)
            if 'p' not in task: task['p'] = 100
            if 'g' not in task: task['g'] = 180
            if 't' not in task: task['t'] = 'default'
            if 'c' not in task: return None
            return task
        except:
            pass 

    # 2. 旧格式兼容 (Prio:Grace:Tag:Cmd)
    try:
        parts = line.split(':', 3)
        if len(parts) < 3: return None
        p = int(parts[0])
        g = int(parts[1])
        t = parts[2]
        cmd = parts[3] if len(parts) > 3 else t
        return {'p': p, 'g': g, 't': t, 'wd': None, 'c': cmd}
    except:
        return None

def pop_best_task(queue_file):
    if not os.path.exists(queue_file): return
    
    try:
        with open(queue_file, 'r') as f:
            raw_lines = f.readlines()
            
        valid_tasks = []
        for line in raw_lines:
            t = parse_line(line)
            if t: valid_tasks.append(t)
        
        if not valid_tasks: return

        # 排序：Priority (小优) -> FIFO
        valid_tasks.sort(key=lambda x: x['p'])
        best = valid_tasks.pop(0)
        
        with open(queue_file, 'w') as f:
            for t in valid_tasks:
                f.write(json.dumps(t) + "\n")
                
        # --- 输出 Shell 变量 (STRICT: STDOUT ONLY) ---
        out = []
        out.append(f"TQ_PRIO={best['p']}")
        out.append(f"TQ_GRACE={best['g']}")
        out.append(f"TQ_TAG={shlex.quote(str(best['t']))}")
        out.append(f"TQ_WORKDIR={shlex.quote(str(best.get('wd') or ''))}")
        out.append(f"TQ_GIT_HASH={shlex.quote(best.get('git') or '')}")
        out.append(f"TQ_CMD={shlex.quote(best['c'])}")
        # Log Persistence Field
        out.append(f"TQ_LOG_PATH={shlex.quote(str(best.get('lp') or ''))}")
        out.append(f"TQ_JSON={shlex.quote(json.dumps(best))}")
        
        # 原子性写入 Stdout
        sys.stdout.write("\n".join(out) + "\n")
        sys.stdout.flush()
        
    except Exception as e:
        sys.stderr.write(f"Error in pop: {e}\n")

def get_min_priority(queue_file):
    if not os.path.exists(queue_file):
        sys.stdout.write("99999\n")
        return
    try:
        with open(queue_file, 'r') as f:
            min_p = 99999
            for line in f:
                t = parse_line(line)
                if t and t['p'] < min_p:
                    min_p = t['p']
            sys.stdout.write(f"{min_p}\n")
    except:
        sys.stdout.write("99999\n")

if __name__ == "__main__":
    if len(sys.argv) < 3: sys.exit(1)
    action = sys.argv[1]
    q_file = sys.argv[2]

    if action == "pop":
        pop_best_task(q_file)
    elif action == "peek_prio":
        get_min_priority(q_file)