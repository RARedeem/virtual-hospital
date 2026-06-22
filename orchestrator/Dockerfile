FROM python:3.12-slim

WORKDIR /app

# poppler-utils: pdf2image 栅格化 PDF 上传件所需（需求2 OCR 链路）
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    fastapi==0.115.* \
    uvicorn[standard]==0.32.* \
    httpx==0.27.* \
    psycopg[binary]==3.2.* \
    pgvector==0.3.* \
    minio==7.2.* \
    pydantic==2.9.* \
    "pyjwt[crypto]==2.9.*" \
    python-multipart==0.0.* \
    pdf2image==1.17.* \
    Pillow==11.*

COPY app/ ./app/

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
