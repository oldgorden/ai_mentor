#!/bin/bash
# 屏蔽 SIGTERM/SIGINT，让 mentor 真正后台运行
# 用法: 在 .env 文件中配置密钥，然后运行此脚本
trap '' TERM INT

cd "$(dirname "$0")"

if [ ! -f .env ]; then
    echo "错误: 未找到 .env 文件，请先复制模板并填入密钥："
    echo "  cp .env.example .env"
    echo "  vim .env"
    exit 1
fi

set -a
source .env
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
