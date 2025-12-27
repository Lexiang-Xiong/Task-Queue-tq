#!/bin/bash
# =========================================================
# Task Queue System Uninstall Script
# =========================================================

# --- 颜色定义 ---
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

CURRENT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
RC_FILE="$HOME/.bashrc"

# 检测 zsh (保持与 setup.sh 一致的逻辑)
if [ -n "$ZSH_VERSION" ]; then
    RC_FILE="$HOME/.zshrc"
elif [ -f "$HOME/.zshrc" ] && [ "$SHELL" == "/bin/zsh" ]; then
    RC_FILE="$HOME/.zshrc"
fi

echo -e "${RED}[!] Task Queue System Uninstaller${NC}"
echo -e "    Target Config: $RC_FILE"
echo -e "    Target Dir:    $CURRENT_DIR"

# --- 1. 确认提示 ---
read -p "Are you sure you want to uninstall? This will remove logs and config. (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 1
fi

# --- 2. 清理 Shell 配置 ---
echo -e "\n${YELLOW}[1/2] Removing configuration from $RC_FILE...${NC}"

if grep -q "Task Queue System" "$RC_FILE"; then
    # 1. 创建备份
    cp "$RC_FILE" "${RC_FILE}.bak_tq"
    echo "    Backup created at ${RC_FILE}.bak_tq"

    # 2. 使用 sed 删除两个标记之间的内容 (包括标记本身)
    # 标记是: # --- Task Queue System --- 到 # ---------------------------
    # 使用临时文件方式以兼容 Linux(GNU) 和 macOS(BSD) 的 sed 差异
    
    sed '/# --- Task Queue System ---/,/# ---------------------------/d' "$RC_FILE" > "${RC_FILE}.tmp" && mv "${RC_FILE}.tmp" "$RC_FILE"
    
    echo -e "    ${GREEN}Configuration block removed.${NC}"
else
    echo "    No Task Queue configuration found in $RC_FILE."
fi

# --- 3. 清理运行时文件 ---
echo -e "\n${YELLOW}[2/2] Cleaning up runtime files...${NC}"

# 删除日志目录
if [ -d "$CURRENT_DIR/logs" ]; then
    rm -rf "$CURRENT_DIR/logs"
    echo "    Removed logs/ directory."
fi

# 删除队列状态文件
rm -f "$CURRENT_DIR"/*.queue
rm -f "$CURRENT_DIR"/*.running
rm -f "$CURRENT_DIR"/*.tmp
rm -f /tmp/scheduler_*.lock  # 清理系统临时目录的锁

echo "    Removed *.queue, *.running, and lock files."

# --- 结束 ---
echo -e "\n${GREEN}=======================================${NC}"
echo -e "${GREEN}      Uninstallation Complete          ${NC}"
echo -e "${GREEN}=======================================${NC}"
echo -e "1. Please run: ${YELLOW}source $RC_FILE${NC} to update your shell."
echo -e "2. You can now safely remove this directory:"
echo -e "   rm -rf $CURRENT_DIR"
echo ""