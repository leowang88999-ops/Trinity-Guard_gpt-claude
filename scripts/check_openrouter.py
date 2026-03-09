import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=ENV_PATH)

# 从环境变量读取 OpenRouter Key，避免硬编码密钥
API_KEY = os.getenv("OPENROUTER_API_KEY", "")

# 创建客户端
client = OpenAI(
    api_key=API_KEY,
    base_url="https://openrouter.ai/api/v1"
)

try:
    # 测试调用一个简单聊天
    response = client.chat.completions.create(
        model="anthropic/claude-4.6-opus",
        messages=[{"role": "user", "content": "Ping"}]
    )

    print("✅ API 可用！Claude 回复：")
    print(response.choices[0].message.content)

except Exception as e:
    print("❌ API 测试失败，错误信息：")
    print(e)