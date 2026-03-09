#!/bin/bash
# 启动隧道，将 LiteLLM 代理暴露到公网，供 Cursor 使用
# Cursor 会阻止 localhost，必须通过公网隧道访问
#
# 用法: ./scripts/start_tunnel.sh [ngrok|cloudflared]
# 默认优先 ngrok（需先配置 authtoken）

set -e
cd "$(dirname "$0")/.."

TOOL="${1:-ngrok}"

# 确保 LiteLLM 在运行
echo "检查 LiteLLM..."
if ! curl -sS http://localhost:4000/v1/models -H "Authorization: Bearer sk-litellm-trinity" >/dev/null 2>&1; then
  echo "正在启动 LiteLLM..."
  docker-compose up -d litellm
  sleep 5
fi

echo ""
echo "=========================================="
echo "将下方出现的 URL 加上 /v1 填入 Cursor Base URL"
echo "API Key: sk-litellm-trinity"
echo "=========================================="
echo ""

case "$TOOL" in
  ngrok)
    if ! ngrok config check >/dev/null 2>&1; then
      echo "ngrok 未配置，请先执行:"
      echo "  1. 注册 https://dashboard.ngrok.com/signup"
      echo "  2. ngrok config add-authtoken <你的token>"
      echo ""
      echo "或使用 cloudflared: ./scripts/start_tunnel.sh cloudflared"
      exit 1
    fi
    ngrok http 4000
    ;;
  cloudflared)
    cloudflared tunnel --url http://localhost:4000
    ;;
  *)
    echo "用法: $0 [ngrok|cloudflared]"
    exit 1
    ;;
esac
