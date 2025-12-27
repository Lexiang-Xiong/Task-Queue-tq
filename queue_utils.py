#!/usr/bin/env python3
import sys
import os

def pop_best_task(queue_file):
    """
    读取队列文件，按优先级排序，移除并返回优先级最高的任务。
    """
    if not os.path.exists(queue_file):
        return None
    
    try:
        with open(queue_file, 'r') as f:
            # 过滤空行
            lines = [l.strip() for l in f.readlines() if l.strip()]
        
        if not lines:
            return None

        # 排序：按冒号分隔的第一个字段(优先级)排序
        # 格式: Prio:Grace:Cmd
        # lambda 保护：如果格式不对，默认优先级放最后
        lines.sort(key=lambda x: int(x.split(':')[0]) if ':' in x else 99999)

        # 弹出第一个
        best_task = lines.pop(0)

        # 写回文件
        with open(queue_file, 'w') as f:
            if lines:
                f.write('\n'.join(lines) + '\n')
            else:
                f.write('') # 清空
        
        return best_task

    except Exception as e:
        # 出错时不破坏文件，直接返回空
        return None

def get_min_priority(queue_file):
    """
    只获取当前队列中最高的优先级数值，不修改文件。
    用于抢占判断。
    """
    if not os.path.exists(queue_file):
        print(99999)
        return

    try:
        with open(queue_file, 'r') as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        
        if not lines:
            print(99999)
            return

        min_p = min(int(l.split(':')[0]) for l in lines if ':' in l)
        print(min_p)
    except:
        print(99999)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(1)
    
    action = sys.argv[1]
    q_file = sys.argv[2]

    if action == "pop":
        task = pop_best_task(q_file)
        if task:
            print(task)
            sys.exit(0)
        else:
            sys.exit(1)
            
    elif action == "peek_prio":
        get_min_priority(q_file)