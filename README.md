# 🚦 Task Queue (tq)

> **A robust, preemptive GPU task scheduler with Git context snapshots.**
> 专为单机科研环境打造的抢占式任务调度系统。

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Platform](https://img.shields.io/badge/platform-Linux-lightgrey)
![Language](https://img.shields.io/badge/language-Bash%20%7C%20Python3-yellow)
![Version](https://img.shields.io/badge/version-2.0-green)

**tq 是一个为单机科研环境设计的 GPU 任务调度工具，用来解决三件事：**

- GPU 空闲时自动跑长任务，需要时可立即抢占，抢占结束恢复中断的任务
- 每个实验结果都绑定提交时的 Git 代码状态（无需手动 add / commit）
- 所有实验日志集中管理，随时能找到“那个结果”，并可以给日志加comments


## 🧠 为什么要有 tq？

在日常科研中，我经常遇到这样的问题:

有时候，我会在 GPU 空闲时提交一个**很长的训练任务**；
但在训练跑着的时候，又突然需要 **马上验证一个小想法**。

我不想杀掉已经跑了很久的训练，
也不想为了一个调试脚本，把 GPU 一直空着。

**我真正需要的，只是：**
在我不用 GPU 的时候把它跑满；需要时能立刻用上，用完自动恢复之前的任务。

---

后来我发现，调度之外，还有两个反复出现的问题。

**第一，是代码状态。**
几周后翻到一个不错的结果，却完全不记得：
* 当时用的是哪一版代码
* 是不是还没来得及 commit

我需要的是：
**这个结果，能回到它当时的代码现场。**

**第二，是日志。**
实验一多，日志散落在各个目录里；
有时提交得很急，甚至忘了把输出重定向到文件。

我需要的是：
**所有实验日志有一个统一、可回溯的地方，以后能快速回答“那个结果是从哪来的”。**

---

**`tq` 就是在解决这三件事的过程中写出来的：**

*   **调度**：空闲时跑满 GPU，需要时立即让出
*   **Git 快照**：记录每次任务的代码状态，结果可复现
*   **日志管理**：集中、标记、归档，不再丢上下文

它不是一个复杂的系统，只是让我在单机科研环境里，**少踩一些每天都会踩的坑。**

## ✨ 核心特性 (Key Features)

对应上述需求，Task Queue 提供了以下核心能力：

*   **🛡️ 抢占式调度 (Preemptive Scheduling)**
    高优任务（如 debug）提交后，系统会自动向正在运行的低优任务发送 `SIGTERM`，给予其宽限期（Grace Period）保存 Checkpoint，然后释放 GPU。待高优任务结束后，自动恢复低优任务。

*   **📸 Git 现场快照 (Git Context Snapshots)**
    提交任务时自动捕获当前代码状态（Git Commit 或 Stash Hash）。**即使你随后修改了代码**，后台任务执行时对应的仍是提交那一刻的代码版本，彻底解决“结果无法复现”的问题。

*   **📂 集中化日志管理 (Centralized Logging)**
    所有任务的标准输出（stdout/stderr）自动捕获归档。配合 `tq` 的交互式终端，支持：
    *   **Real-time Monitoring**: 使用 `view <id> -f` 像 `tail -f` 一样实时追踪正在运行的任务日志。
    *   **Note**: 给日志打备注（如 `note 1 best_model`）。
    *   **Category**: 将日志归档到子目录（如 `catg 1 archived/failed`）。

*   **🕹️ 交互式多模式终端 (Modal Interface)**
    *   **Dashboard**: 全局状态监控。
    *   **Queue Mode**: 可视化的等待队列管理。
    *   **Logs Mode**: 树状日志浏览器，无需 `ls` 和 `grep` 就能找到你的实验记录。

*   **🐍 环境无缝切换**
    完美集成 Conda，支持 `env activate` 切换会话环境，或使用 `-e` 参数为单个任务指定特定环境。

> tq 主要面向单机或少量 GPU 的科研服务器，不试图替代 Slurm / Kubernetes 等集群调度系统，旨在提供一个干净，舒服，优雅的单机调度系统。


---

## 🚀 快速开始 (Quick Start)

### 1. 安装 (Installation)

确保系统已安装 Python 3.6+。

```bash
# 1. Clone 仓库
git clone https://github.com/Lexiang-Xiong/Task-Queue-tq.git
cd task_queue

# 2. 运行安装脚本 (自动配置 PATH 和 Alias)
bash setup.sh

# 3. 生效配置
source ~/.bashrc  # 或者 source ~/.zshrc
```

### 2. 基本使用流

```bash
# 进入交互式控制台
$ tq
(base) ~ (tq:0|OFF) > 
```

**场景 A：启动调度器**
```bash
(base) ~ (tq:0|OFF) > start
[*] Launching scheduler for '0'...
(base) ~ (tq:0|ON) >        # 状态变为 ON (绿色)
```

**场景 B：提交任务 (自动捕获 Git 状态)**
```bash
# 提交一个训练任务，优先级 100，标签 exp_v1
(base) ~ (tq:0|ON) > python train.py -p 100 -t exp_v1
[+] Submitted to '0' (Tag: exp_v1)
```

**场景 C：查看状态**
```bash
(base) ~ (tq:0|ON) > st
-> 0      : [RUN] PID:12345 Prio:100 [exp_v1] @ my_project | python train.py
         ├─ Log: .../0_20251229_exp_v1.log
```

---

## 🎮 交互式模式指南 (Modal Interface)

`tq` 引入了三种操作模式。观察提示符的变化来判断当前模式。

### 1. 🏠 主面板 (Dashboard / Home)
*提示符:* `(env) path (tq:0|ON) >`

这是默认模式，用于提交任务和监控全局状态。

*   **`use <id>`**: 切换当前操作的 GPU 队列（如 `use 0`, `use 0,1`）。
*   **`start` / `stop`**: 启停当前队列的调度器。
*   **`st`**: 查看所有队列的运行状态 (Status)。
*   **`env <name>`**: 切换当前会话的 Conda 环境。
*   **提交命令**: 直接输入 Python 命令即可提交。
*   **`man`**: 查询所有的指令和其使用方式(🚁Mayday!)。

### 2. ⏳ 队列模式 (Queue Mode)
*进入方式:* 输入 **`q`**
*提示符:* `... [QUEUE] >`

用于管理等待中的任务。

*   **`ls` / (自动显示)**: 列出等待中的任务，显示 ID、优先级、Tag。
*   **`rm <id>`**: 删除指定 ID 的等待任务（支持多选，如 `rm 1 3`）。
*   **`purge`**: 清空当前队列所有任务。
*   **`back`**: 返回主面板。

### 3. 📂 日志模式 (Logs Mode)
*进入方式:* 输入 **`hist`** 或 **`hist <sub_folder>`**
*提示符:* `... [LOGS:path] >`

一个功能强大的日志文件管理器。

*   **树状视图**: 自动显示当前目录的日志文件树，高亮显示 "YOU" (当前位置) 和 "EYE" (查看位置)。
*   **`view <id> [-f]`**: 查看日志内容。
    *   默认使用 `less` 分页查看。
    *   加上 **`-f`** 参数（如 `view 1 -f`）可进行实时追踪（Tail Follow）。
    *   在追踪模式下按 **`Ctrl+C`** 可停止追踪并停留在日志模式。
*   **`note <id> <text>`**: **[新功能]** 给日志文件添加备注。
    *   *示例:* `note 1 Best result so far` -> 文件列表中会显示黄色高亮的备注。
*   **`catg <id> <folder>`**: **[新功能]** 归档/分类。将指定日志移动到子文件夹。
    *   *示例:* `catg 1 archived/failed_runs`
*   **`lcd <folder>`**: 进入子目录（Local Change Directory）。
*   **`rm <id>`**: 删除日志文件。
*   **`back`**: 返回主面板。

---

## ⚙️ 进阶功能详解

### 1. Git 现场快照 (Snapshot)
当您提交任务时，`tq` 会检测当前目录是否为 Git 仓库：
1.  **检测**: 自动捕获当前的 `Git Commit Hash`。
2.  **脏状态处理**: 如果您有未提交的代码 (Dirty State)，系统会尝试创建一个隐形的 `Stash Commit` (不会修改您的 stash 列表)。
3.  **恢复**: 调度器在执行任务前，会在日志头部打印 Hash。如果需要复现，可以通过日志找到该 Hash 并 `git checkout`。

### 2. 任务提交参数
支持在命令后追加参数来控制调度行为：

*   **`-p <int>` (Priority)**: 优先级 (越小越高，默认 100)。
*   **`-g <int>` (Grace)**: 抢占宽限期 (秒，默认 180s)。
*   **`-t <str>` (Tag)**: 任务标签。
*   **`-e <str>` (Env)**: 覆盖当前 Conda 环境。

```bash
# 高优任务，抢占时给 10 分钟保存模型，指定 pytorch 环境
> python train.py -p 10 -g 600 -e pytorch_v2 -t urgent_fix
```

### 3. 任务脚本规范 (实现断点续训)
为了配合抢占机制，Python 脚本应捕获 `SIGTERM` 信号：

```python
import signal, sys

def save_and_exit(signum, frame):
    print("Saving checkpoint...")
    # model.save_checkpoint()
    sys.exit(0)

signal.signal(signal.SIGTERM, save_and_exit)
```

---

## 📂 目录结构

所有数据存储在 `~/task_queue/`（或 `TQ_HOME` 指定的目录）：

*   `*.queue`: 等待队列数据 (JSONL 格式)
*   `*.running`: 运行时元数据 (PID, Priority, LogPath, JSON Payload)
*   `logs/tasks/`: 任务日志归档
    *   `.tq_notes.json`: 存储日志备注的索引文件

---

## ❓ 常见问题 (FAQ)

**Q: `rm` 命令怎么用？**
A: `rm` 是上下文敏感的：
*   在 **Queue Mode** (`q`) 下：删除等待中的任务。
*   在 **Logs Mode** (`hist`) 下：删除日志文件。
*   在 **Home** 下：为了安全，禁用 `rm`。

**Q: Git 快照会弄乱我的仓库吗？**
A: 不会。它使用 `git stash create` 仅生成一个悬空的对象 Hash，不会修改您的工作区，也不会向 `refs/stash` 添加条目。

**Q: 如何卸载？**
A: 运行仓库根目录下的卸载脚本：
```bash
bash uninstall.sh
```

---

## License

MIT License. Copyright (c) 2025 Lexiang-Xiong.