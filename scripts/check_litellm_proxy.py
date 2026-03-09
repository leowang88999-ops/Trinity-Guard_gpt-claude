#!/usr/bin/env python3
"""测试 LiteLLM 代理（Satori + OpenRouter）"""
import os
from openai import OpenAI

# 从 .env 或环境变量读取
from dotenv import load_dotenv
load_dotenv()

PROXY_URL = os.getenv("LITELLM_PROXY_URL", "http://localhost:4000/v1")
MASTER_KEY = os.getenv("LITELLM_MASTER_KEY", "sk-litellm-trinity")

client = OpenAI(
    api_key=MASTER_KEY,
    base_url=PROXY_URL,
)

def test_model(model_name: str) -> bool:
    """测试指定模型"""
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": "Ping"}],
        )
        content = response.choices[0].message.content or ""
        print(f"✅ {model_name}: {content[:80]}...")
        return True
    except Exception as e:
        print(f"❌ {model_name}: {e}")
        return False

if __name__ == "__main__":
    print(f"代理地址: {PROXY_URL}\n")
    ok = 0
    ok += test_model("claude-satori")
    ok += test_model("claude-openrouter")
    ok += test_model("claude-opus-openrouter")
    print(f"\n通过 {ok}/3 个模型")
