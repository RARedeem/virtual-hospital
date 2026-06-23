"""Ollama 推理客户端封装。"""
import os
import httpx

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://ollama:11434")


async def generate(model: str, prompt: str, system: str | None = None) -> str:
    """调用 Ollama 生成接口，返回完整文本。"""
    payload = {"model": model, "prompt": prompt, "stream": False}
    if system:
        payload["system"] = system
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(f"{OLLAMA_HOST}/api/generate", json=payload)
        resp.raise_for_status()
        return resp.json()["response"].strip()


# nomic-embed-text:v1.5 上下文约 2048 token；超长切片会让 /api/embeddings 报 500。
# 保守按字符截断（嵌入仅用于检索，前段内容已足够代表相关性）。
_EMBED_MAX_CHARS = 6000


async def embed(model: str, text: str) -> list[float]:
    """生成文本向量。超长截断 + 一次重试，提升大批量摄取的鲁棒性。"""
    text = (text or "").strip()
    if not text:
        return []                       # 空白块由调用方跳过
    if len(text) > _EMBED_MAX_CHARS:
        text = text[:_EMBED_MAX_CHARS]
    last_exc = None
    async with httpx.AsyncClient(timeout=120.0) as client:
        for attempt in range(2):
            try:
                resp = await client.post(
                    f"{OLLAMA_HOST}/api/embeddings",
                    json={"model": model, "prompt": text},
                )
                resp.raise_for_status()
                emb = resp.json().get("embedding") or []
                # 维度不在此硬编码（nomic=768 / bge-m3=1024）；只判非空，
                # 具体维度由调用方 ingest.py 按 scope（embed_dim）校验。
                if emb:
                    return emb
                last_exc = RuntimeError("embedding 空响应")
            except Exception as e:
                last_exc = e
    raise last_exc
