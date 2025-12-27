#!/bin/bash
# =========================================================
# Task Queue System Setup Script (Lite Version)
# =========================================================

set -e

# --- 颜色定义 ---
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# --- 路径定义 ---
CURRENT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
RC_FILE="$HOME/.bashrc"

# 检测是否使用 zsh
if [ -n "$ZSH_VERSION" ]; then
    RC_FILE="$HOME/.zshrc"
elif [ -f "$HOME/.zshrc" ] && [ "$SHELL" == "/bin/zsh" ]; then
    RC_FILE="$HOME/.zshrc"
fi

echo -e "${GREEN}[*] Setting up Task Queue System...${NC}"
echo -e "    Root: $CURRENT_DIR"

# --- 1. 环境检查 (Fail Fast) ---
echo -e "\n${YELLOW}[1/3] Checking Prerequisites...${NC}"

# 检查 python3 命令是否存在
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}[ERROR] Python 3 is not installed or not in PATH.${NC}"
    echo -e "Please install Python 3.6+ first (e.g., 'sudo apt install python3' or install Miniconda)."
    exit 1
fi

# 检查 Python 版本 (需要 >= 3.6 支持 f-string)
python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,6) else 1)" 2>/dev/null
if [ $? -ne 0 ]; then
    echo -e "${RED}[ERROR] Python version too old.$(python3 --version)${NC}"
    echo -e "Task Queue requires Python 3.6 or higher."
    exit 1
fi

echo -e "    Found: $(python3 --version) at $(which python3)"

# --- 2. 目录与权限 ---
echo -e "\n${YELLOW}[2/3] Initializing Directories & Permissions...${NC}"

chmod +x "$CURRENT_DIR/scheduler.sh"
chmod +x "$CURRENT_DIR/tq.py"
echo "    +x scheduler.sh & tq.py"

mkdir -p "$CURRENT_DIR/logs/tasks"
echo "    Created logs/tasks/"

# --- 3. 配置 Shell ---
echo -e "\n${YELLOW}[3/3] Configuring Shell ($RC_FILE)...${NC}"

# 构造配置块
TQ_CONFIG="# --- Task Queue System ---
export TQ_HOME=\"$CURRENT_DIR\"
alias tq=\"python3 \$TQ_HOME/tq.py\"
# ---------------------------"

# 检查是否已存在
if grep -q "Task Queue System" "$RC_FILE"; then
    echo -e "${YELLOW}    [WARN] Configuration already exists in $RC_FILE. Skipping.${NC}"
    echo -e "    Please check $RC_FILE manually if paths have changed."
else
    echo -e "$TQ_CONFIG" >> "$RC_FILE"
    echo "    Added 'tq' alias and variables to $RC_FILE."
fi

# --- 完成 ---
echo -e "\n${GREEN}=======================================${NC}"
echo -e "${GREEN}      Setup Completed Successfully!    ${NC}"
echo -e "${GREEN}=======================================${NC}"
echo -e "To activate changes, please run:"
echo -e "\n    ${YELLOW}source $RC_FILE${NC}\n"
echo -e "Then try: tq ls"