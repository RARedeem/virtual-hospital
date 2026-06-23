"""Ollama 推理客户端封装。"""
import os
import httpx

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://ollama:11434")


async def generate(
    model: str,
    prompt: str,
    system: str | None = None,
    keep_alive: int | None = None,
    images: list[str] | None = None,
    options: dict | None = None,
) -> str:
    """调用 Ollama 生成接口，返回完整文本。

    images: base64 编码的图片列表（视觉模型如 glm-ocr 用）。
    options: Ollama 推理参数（num_predict / temperature / stop 等）。
    """
    payload = {"model": model, "prompt": prompt, "stream": False}
    if system:
        payload["system"] = system
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive
    if images:
        payload["images"] = images
    if options:
        payload["options"] = options
    # 900s: meditron:70b (~40GB) may need to reload after gemma2 model swap
    async with httpx.AsyncClient(timeout=900.0) as client:
        resp = await client.post(f"{OLLAMA_HOST}/api/generate", json=payload)
        resp.raise_for_status()
        response = resp.json()["response"].strip()
        if not response:
            resp2 = await client.post(f"{OLLAMA_HOST}/api/generate", json=payload)
            resp2.raise_for_status()
            response = resp2.json()["response"].strip()
            if not response:
                raise RuntimeError(f"model {model!r} returned empty response after retry")
        return response


async def chat(model: str, messages: list[dict], options: dict | None = None,
               keep_alive: int | None = None) -> str:
    """调用 Ollama /api/chat（多轮消息）。

    借鉴已验证的 ebm-ai-pipeline：meditron 用 chat 角色消息(system/user/assistant)
    + few-shot 才以"助手应答"姿态输出结构化评估，而非把裸 prompt 当文本续写(回显)。
    """
    payload = {"model": model, "messages": messages, "stream": False}
    if options:
        payload["options"] = options
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive
    async with httpx.AsyncClient(timeout=900.0) as client:
        resp = await client.post(f"{OLLAMA_HOST}/api/chat", json=payload)
        resp.raise_for_status()
        content = resp.json().get("message", {}).get("content", "").strip()
        if not content:
            resp2 = await client.post(f"{OLLAMA_HOST}/api/chat", json=payload)
            resp2.raise_for_status()
            content = resp2.json().get("message", {}).get("content", "").strip()
            if not content:
                raise RuntimeError(f"model {model!r} returned empty chat response after retry")
        return content


async def embed(model: str, text: str) -> list[float]:
    """生成文本向量。"""
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{OLLAMA_HOST}/api/embeddings",
            json={"model": model, "prompt": text},
        )
        resp.raise_for_status()
        return resp.json()["embedding"]
