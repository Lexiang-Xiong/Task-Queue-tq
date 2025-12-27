# 🚦 Task Queue (tq)

> **A robust, preemptive GPU task scheduler for researchers.**  
> 专为单机科研环境设计的抢占式任务调度系统。

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Platform](https://img.shields.io/badge/platform-Linux-lightgrey)
![Language](https://img.shields.io/badge/language-Bash%20%7C%20Python3-yellow)

## 📖 简介 (Introduction)

**Task Queue (tq)** 解决了实验室服务器资源分配的核心痛点：**如何在闲时充分利用 GPU 跑长任务，同时在需要调试时立即获得 GPU 使用权？**

它允许你将大量低优先级的训练任务推入队列，一旦检测到高优先级任务（如 Jupyter 调试或手动执行的脚本），`tq` 会自动暂停或终止后台任务，待资源空闲后自动恢复。

### ✨ 核心特性 (Key Features)

*   **🛡️ 抢占式调度**：高优任务自动抢占，支持 `Grace Period`（宽限期）供任务保存 Checkpoint。
*   **🐍 多环境支持**：完美集成 Conda，支持会话级环境切换 (`env activate`) 和单任务环境覆盖 (`-e`)。
*   **🔒 数据安全**：基于 `fcntl` 文件锁的原子操作，支持高并发提交。
*   **📜 审计与日志**：自动捕获 stdout/stderr，支持历史记录查询 (`hist`) 和在线查看 (`view`)。
*   **🏷️ 任务标签**：支持自定义 Tag，方便管理实验版本。
*   **🕵️ 零干扰**：自动避让非调度器启动的外部 GPU 进程。

---

## 🚀 快速开始 (Quick Start)

### 1. 安装
```bash
# 1. Clone 仓库
git clone https://github.com/your-username/task_queue.git
cd task_queue

# 2. 运行安装脚本 (自动配置 PATH 和 Alias)
bash setup.sh

# 3. 生效配置
source ~/.bashrc
```

### 2. 基本使用流
```bash
# 进入交互式控制台
$ tq

# 1. 切换到 GPU 0 队列
(base) (tq:0|OFF) > start       # 启动后台调度器
(base) (tq:0|ON) >              # 状态变为 ON (绿色)

# 2. 提交任务
(base) (tq:0|ON) > python train_gpt.py -p 100 -t exp_v1
[+] Submitted to '0' (Tag: exp_v1)

# 3. 查看状态
(base) (tq:0|ON) > st
-> 0      : [RUN] PID:12345 Prio:100 [exp_v1] | python train_gpt.py
         ├─ Log: .../0_20251228_exp_v1.log
```

---

## 🎮 详细使用指南 (User Guide)

`tq` 提供了一个强大的交互式 Shell。

### 0. 指令手册
如果不清楚某个功能，请随时使用`man`指令(🚁)查看详细的指令说明和用法。

### 1. 队列管理
*   **`use <id>`**: 切换当前操作的队列。
    *   `use 0` (单卡 GPU 0)
    *   `use 0,1` (双卡并行)
    *   `use cpu` (纯 CPU 任务)
*   **`st` (status)**: 查看所有队列的运行状态。
*   **`start` / `stop`**: 启动或停止当前队列的后台调度器。

### 2. 提交任务 (Submission)
直接输入命令即可提交。支持以下参数：

*   **`-p <int>` (Priority)**: 优先级，**数值越小优先级越高**。默认 100。
*   **`-g <int>` (Grace)**: 宽限期(秒)。被抢占时，系统会发送 `SIGTERM` 并等待这么多秒让任务保存进度。默认 180s。
*   **`-t <str>` (Tag)**: 任务标签，用于日志分类。
*   **`-e <str>` (Env)**: 指定该任务运行的 Conda 环境（覆盖当前会话设置）。

**示例：**
```bash
# 提交高优任务，给 10 分钟保存时间
> python debug.py -p 10 -g 600 -t debug_run

# 使用特定的 pytorch 环境运行
> python train.py -e pytorch_v2
```

### 3. 环境管理 (Conda Integration)
类似于原生 Conda 的体验：

*   **`env list`**: 列出系统所有 Conda 环境。
*   **`env activate <name>`**: 切换当前会话的默认提交环境。
*   **`env <name>`**: 快捷切换。

```bash
(base) (tq:0|ON) > env activate my_env
[*] Switched session env to: my_env
(my_env) (tq:0|ON) > python train.py  # 自动在该环境下运行
```

### 4. 日志与历史
*   **`hist`**: 列出当前队列最近执行的任务历史。
*   **`view <id>`**: 使用 `less` 查看指定 ID 的日志内容（支持滚动）。
*   **`cat`**: 实时查看当前**正在运行**任务的日志末尾。

```bash
(tq:0|ON) > hist
ID   | Time                | Size       | File
1    | 2025-12-28 12:00:00 | 5.2 KB     | 0_...exp1.log

(tq:0|ON) > view 1
```

---

## ⚙️ 进阶配置

### 任务脚本规范
为了利用 **Grace Period** 实现断点续训，您的 Python 脚本应捕获 `SIGTERM` 信号：

```python
import signal, sys

def save_and_exit(signum, frame):
    print("Saving checkpoint...")
    # save_model()
    sys.exit(0)

signal.signal(signal.SIGTERM, save_and_exit)
```

### 目录结构
所有数据存储在 `~/task_queue/`：
*   `*.queue`: 等待队列数据 (文本文件，可手动编辑)
*   `*.running`: 当前运行任务元数据
*   `logs/tasks/`: 归档的任务日志

---

## ❓ 常见问题 (FAQ)

**Q: 如何删除一个队列？**
A: 切换到该队列，先 `stop` 停止调度器，然后执行 `purge` 清空任务。如果需要彻底移除显示，请在终端删除对应的 `.queue` 文件。

**Q: 多个任务如何批量提交？**
A: 您可以直接将多行命令粘贴到 `tq` 控制台中。配合 `-e` 参数，甚至可以一次性粘贴属于不同环境的任务列表。

**Q: 图形界面 (Xorg) 会影响调度吗？**
A: 不会。调度器会自动过滤 Graphics 类型的进程，只针对 Compute 类型的进程进行避让。

---

## 卸载 (Uninstall)
运行仓库根目录下的卸载脚本：
```bash
bash uninstall.sh
```

## License
MIT License.