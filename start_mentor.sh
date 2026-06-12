#!/bin/bash
# 屏蔽 SIGTERM/SIGINT，让 mentor 真正后台运行
# 密钥配置: ~/.config/ai_mentor/.env
trap '' TERM INT

cd "$(dirname "$0")"

ENV_FILE="$HOME/.config/ai_mentor/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "错误: 未找到密钥文件 $ENV_FILE"
    echo "请先创建："
    echo "  mkdir -p ~/.config/ai_mentor"
    echo "  cp .env.example \$ENV_FILE"
    echo "  vim \$ENV_FILE"
    exit 1
fi

set -a
source "$ENV_FILE"
set +a

: "${CUSTOM_OPENAI_API_KEY:?请设置 CUSTOM_OPENAI_API_KEY}"
: "${S2_API_KEY:?请设置 S2_API_KEY}"
: "${XFYUN_API_KEY:?请设置 XFYUN_API_KEY}"

CUSTOM_OPENAI_BASE_URL="${CUSTOM_OPENAI_BASE_URL:-https://token-plan-cn.xiaomimimo.com/v1}"
XFYUN_BASE_URL="${XFYUN_BASE_URL:-https://maas-coding-api.cn-huabei-1.xf-yun.com/v2}"

export CUSTOM_OPENAI_API_KEY CUSTOM_OPENAI_BASE_URL
export S2_API_KEY
export XFYUN_API_KEY XFYUN_BASE_URL

pkill -f "run_mentor\|launch_scientist" 2>/dev/null
sleep 2
rm -f mentor/checkpoint.json 2>/dev/null

exec .venv/bin/python mentor/run_mentor.py </dev/null > /tmp/mentor.log 2>&1
