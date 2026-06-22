"""MinIO 对象存储封装 —— 会员上传的体检/化验/影像报告原件落此。

会员上传的实体医院报告属个人医疗事实，不受约束 B 限制；文件不出本机。
"""
import io
import os
from datetime import datetime

from minio import Minio

ENDPOINT = os.environ.get("MINIO_ENDPOINT", "minio:9000")
ACCESS_KEY = os.environ.get("MINIO_USER", "vhminio")
SECRET_KEY = os.environ.get("MINIO_PASSWORD", "")
BUCKET = os.environ.get("MINIO_REPORTS_BUCKET", "health-reports")

_client: Minio | None = None


def _get_client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(ENDPOINT, access_key=ACCESS_KEY, secret_key=SECRET_KEY, secure=False)
    return _client


def ensure_bucket() -> None:
    client = _get_client()
    if not client.bucket_exists(BUCKET):
        client.make_bucket(BUCKET)


def put_report(member_id: str, filename: str, data: bytes, content_type: str) -> str:
    """存入一份报告原件，返回对象键（raw_file_key）。

    键形如 {member_id}/{utc时间戳}_{原文件名}，便于按成员归档与回溯。
    """
    ensure_bucket()
    safe_name = os.path.basename(filename).replace("/", "_")
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    key = f"{member_id}/{ts}_{safe_name}"
    _get_client().put_object(
        BUCKET, key, io.BytesIO(data), length=len(data), content_type=content_type,
    )
    return key
