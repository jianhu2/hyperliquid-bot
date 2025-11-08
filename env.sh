#!/bin/bash
# env.sh - 创建虚拟环境并安装 Hyperliquid Python SDK 依赖

VENV_DIR="venv"

# 1️⃣ 安装 Python 虚拟环境支持模块
echo ">>> 安装 Python 虚拟环境支持模块..."
sudo apt update
sudo apt install -y python3-venv python3-full python3-pip

# 2️⃣ 删除旧虚拟环境（如果存在）
if [ -d "$VENV_DIR" ]; then
    echo ">>> 删除旧的虚拟环境 $VENV_DIR ..."
    rm -rf "$VENV_DIR"
fi

# 3️⃣ 创建新的虚拟环境
echo ">>> 创建新的虚拟环境 $VENV_DIR ..."
python3 -m venv "$VENV_DIR"

# 4️⃣ 激活虚拟环境
echo ">>> 激活虚拟环境 ..."
source "$VENV_DIR/bin/activate"

# 检查是否激活成功
if [[ "$VIRTUAL_ENV" == "" ]]; then
    echo "❌ 虚拟环境激活失败，请检查 Python 安装。"
    exit 1
fi
echo "✅ 虚拟环境激活成功: $VIRTUAL_ENV"

# 5️⃣ 升级 pip
echo ">>> 升级 pip ..."
pip install --upgrade pip

# 6️⃣ 安装 Python 依赖
echo ">>> 安装 eth-account ..."
pip install eth-account

echo ">>> 安装 hyperliquid-python-sdk"
pip install hyperliquid-python-sdk

# 7️⃣ 提示配置文件
echo "✅ 安装完成！"
echo ">>> 请复制示例配置文件并编辑："
echo "cp config.json.example config.json"
echo "vim config.json"
echo ">>> 然后可以运行示例脚本，例如："
echo "start.sh start"

echo ">>> 查看运行结果"
echo "tail -f /var/log/follow_bot_v3.log"

