"""Ollama 推理客户端封装。"""
import os
import httpx

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://ollama:11434")


async def generate(model: str, prompt: str, system: str | None = None, keep_alive: int | None = None) -> str:
    """调用 Ollama 生成接口，返回完整文本。"""
    payload = {"model": model, "prompt": prompt, "stream": False}
    if system:
        payload["system"] = system
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive
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


async def embed(model: str, text: str) -> list[float]:
    """生成文本向量。"""
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{OLLAMA_HOST}/api/embeddings",
            json={"model": model, "prompt": text},
        )
        resp.raise_for_status()
        return resp.json()["embedding"]
