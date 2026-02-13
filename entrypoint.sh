#!/bin/bash

set -e

APP_DIR="/app/news_reporter"
GIT_REPO_SSH="git@github.com:wanxxxx/news_reporter.git"
GIT_REPO_HTTPS="https://github.com/wanxxxx/news_reporter.git"

echo "=========================================="
echo "🚀 News Reporter 容器启动脚本"
echo "=========================================="

# 配置 SSH 密钥权限
if [ -f "/root/.ssh/id_ed25519" ]; then
    echo "🔑 配置 SSH 密钥..."
    chmod 600 /root/.ssh/id_ed25519
    
    # 创建 .ssh 目录并设置权限
    mkdir -p /root/.ssh
    chmod 700 /root/.ssh
    
    # 添加 GitHub 到 known_hosts
    mkdir -p /root/.ssh
    ssh-keyscan github.com >> /root/.ssh/known_hosts 2>/dev/null
    chmod 644 /root/.ssh/known_hosts
    
    echo "✅ SSH 密钥配置完成"
    USE_SSH=true
else
    echo "⚠️ 未找到 SSH 密钥，将使用 HTTPS 方式克隆"
    USE_SSH=false
fi

# 检查是否已经克隆过仓库
if [ -d "$APP_DIR/.git" ]; then
    echo "📦 仓库已存在，正在拉取最新代码..."
    cd "$APP_DIR"
    git pull origin main || echo "⚠️ Git pull 失败，使用现有代码"
else
    echo "📦 克隆仓库..."
    
    # 优先使用 SSH，失败则回退到 HTTPS
    if [ "$USE_SSH" = true ]; then
        echo "🔗 使用 SSH 方式克隆..."
        git clone "$GIT_REPO_SSH" "$APP_DIR" || {
            echo "⚠️ SSH 克隆失败，尝试 HTTPS..."
            git clone "$GIT_REPO_HTTPS" "$APP_DIR" || {
                echo "❌ Git clone 失败，请检查网络或认证"
                exit 1
            }
        }
    else
        echo "🔗 使用 HTTPS 方式克隆..."
        git clone "$GIT_REPO_HTTPS" "$APP_DIR" || {
            echo "❌ Git clone 失败，请检查网络"
            exit 1
        }
    fi
fi

cd "$APP_DIR"

# 安装 Python 依赖
echo "📦 安装 Python 依赖..."
python3 -m pip install --quiet --no-cache-dir \
    feedparser \
    requests \
    beautifulsoup4 \
    trafilatura \
    lark-oapi \
    openai \
    python-dotenv

echo "✅ 依赖安装完成"

# 启动 openclaw gateway
echo "🚀 启动 openclaw gateway..."
exec openclaw gateway --bind lan --verbose
