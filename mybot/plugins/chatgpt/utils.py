def build_openai_request(context: str, max_tokens: int, model: str = "DD"):  ## 使用不同的模型，DD可以改为需要调用的模型，比如gpt-4
    return {
        "model": model,
        "messages": [{"role": "user", "content": context}],
        "max_tokens": max_tokens,  
        "temperature": 0.7  # 温度参数，可以调整
    }

