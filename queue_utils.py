#!/usr/bin/env python3
import sys
import os
import json
import shlex

def parse_line(line):
    """
    解析单行任务，支持 JSON 和 旧版冒号格式。
    返回 dict: {'p': int, 'g': int, 't': str, 'wd': str|None, 'c': str, 'raw': str}
    """
    line = line.strip()
    if not line: return None
    
    # 1. 尝试 JSON 解析
    if line.startswith('{'):
        try:
            task = json.loads(line)
            # 确保必要字段存在
            if 'p' not in task: task['p'] = 100
            if 'g' not in task: task['g'] = 180
            if 't' not in task: task['t'] = 'default'
            if 'c' not in task: return None # 无命令无效
            return task
        except:
            pass # 解析失败回退到旧格式逻辑

    # 2. 旧格式解析 (Prio:Grace:Tag:Cmd)
    # 兼容 v5.7 及更早版本
    try:
        parts = line.split(':', 3)
        if len(parts) < 3: return None
        
        p = int(parts[0])
        g = int(parts[1])
        t = parts[2]
        
        cmd = parts[3] if len(parts) > 3 else t # 极旧版本兼容
        
        # 构造成新标准对象，WorkDir 为空
        return {
            'p': p, 
            'g': g, 
            't': t, 
            'wd': None, 
            'c': cmd
        }
    except:
        return None

def pop_best_task(queue_file):
    """
    弹出优先级最高的任务，并以 Shell 变量赋值的形式打印到 stdout。
    """
    if not os.path.exists(queue_file):
        return
    
    tasks = []
    raw_lines = []
    
    try:
        with open(queue_file, 'r') as f:
            raw_lines = f.readlines()
            
        valid_tasks = []
        for line in raw_lines:
            t = parse_line(line)
            if t:
                # 存储元组 (TaskObj, OriginalLineString)
                # 如果是旧格式读取进来的，我们想保留它？不，统一转为 JSON 写回更好
                # 但为了最小化变更，我们这里只处理逻辑
                valid_tasks.append(t)
        
        if not valid_tasks:
            return

        # 排序：Priority (小优) -> 原始顺序 (FIFO)
        # Python sort 是稳定的，所以只需按 Priority 排序即可保持 FIFO
        valid_tasks.sort(key=lambda x: x['p'])
        
        best = valid_tasks.pop(0) # 取出第一个
        
        # 写回文件 (所有剩余任务重写为 JSONL 格式，借机清洗旧数据)
        with open(queue_file, 'w') as f:
            for t in valid_tasks:
                f.write(json.dumps(t) + "\n")
                
        # --- 输出 Shell 变量 ---
        # 使用 shlex.quote 确保 Shell 安全
        print(f"TQ_PRIO={best['p']}")
        print(f"TQ_GRACE={best['g']}")
        print(f"TQ_TAG={shlex.quote(str(best['t']))}")
        print(f"TQ_WORKDIR={shlex.quote(str(best.get('wd') or ''))}")
        print(f"TQ_GIT_HASH={shlex.quote(best.get('git') or '')}")
        print(f"TQ_CMD={shlex.quote(best['c'])}")
        # 输出原始 JSON 字符串，便于写入 .running 文件用于恢复
        print(f"TQ_JSON={shlex.quote(json.dumps(best))}")
        
    except Exception as e:
        # 出错不输出任何变量，Shell 端会检测到空
        sys.stderr.write(f"Error in pop: {e}\n")

def get_min_priority(queue_file):
    if not os.path.exists(queue_file):
        print(99999)
        return

    try:
        with open(queue_file, 'r') as f:
            min_p = 99999
            for line in f:
                t = parse_line(line)
                if t and t['p'] < min_p:
                    min_p = t['p']
            print(min_p)
    except:
        print(99999)

if __name__ == "__main__":
    if len(sys.argv) < 3: sys.exit(1)
    action = sys.argv[1]
    q_file = sys.argv[2]

    if action == "pop":
        pop_best_task(q_file)
    elif action == "peek_prio":
        get_min_priority(q_file)