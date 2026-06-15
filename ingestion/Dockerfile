FROM python:3.12-slim

WORKDIR /app

# Build-time deps for docling (libreoffice optional; only PDF needed here)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Docling downloads its layout model from HuggingFace on first use.
# Point to domestic mirror so the container can reach it without GFW issues.
ENV HF_ENDPOINT=https://hf-mirror.com

RUN pip install --no-cache-dir \
    httpx==0.27.* \
    "psycopg[binary]==3.2.*" \
    pgvector==0.3.* \
    docling==2.*

COPY app/ ./app/

# 摄取为按需执行的 CLI 任务，非常驻服务
ENTRYPOINT ["python", "-m", "app.cli"]
