# Cursor 通过 LiteLLM 代理使用 OpenRouter

Cursor 会阻止对 localhost 的请求（SSRF 防护），需将 LiteLLM 暴露到公网。

## 方式一：ngrok（推荐，稳定）

1. 注册并获取 token：https://dashboard.ngrok.com/signup
2. 配置：`ngrok config add-authtoken <你的token>`
3. 启动隧道：`./scripts/start_tunnel.sh ngrok`
4. 将终端显示的 URL 加上 `/v1`，填入 Cursor：
   - Base URL: `https://xxx.ngrok-free.app/v1`
   - API Key: `sk-litellm-trinity`

## 方式二：cloudflared（无需注册）

1. 启动：`./scripts/start_tunnel.sh cloudflared`
2. 将显示的 URL 加上 `/v1` 填入 Cursor
3. 若出现 530 错误，多为网络限制，可尝试 ngrok

## Cursor 模型选择

| 模型名 | 说明 |
|--------|------|
| claude-openrouter | Claude 4.6 Sonnet |
| claude-opus-openrouter | Claude 4.6 Opus |
| claude-satori | Claude Sonnet 4.6 (Satori) |
