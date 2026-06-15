FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi==0.115.* \
    uvicorn[standard]==0.32.* \
    httpx==0.27.* \
    psycopg[binary]==3.2.* \
    pgvector==0.3.* \
    minio==7.2.* \
    pydantic==2.9.* \
    "pyjwt[crypto]==2.9.*"

COPY app/ ./app/

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
