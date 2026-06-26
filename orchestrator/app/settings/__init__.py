"""设置加载器 —— 读 repo 根的统一设置 config/（容器内挂载到 /config）。

治理令：写进代码的补丁 / 特定约束 / prompt / 模型 / 阈值 / 指南配置 一律外挂到 config/，
本模块只提供加载；改 config 即改行为，无需动代码。设置最大化、运行模块最小化。
config/ 同时被 ingestion(/config 挂载) 与 lab(宿主 ../config) 共用 —— 一处管全栈。
"""
import json
import os

_DIR = os.environ.get("CONFIG_DIR", "/config")   # docker-compose 挂载 ./config:/config:ro


def load(rel: str):
    """加载 config/ 下的 JSON。rel 形如 'clinical/redflags.json'。"""
    with open(os.path.join(_DIR, rel), encoding="utf-8") as f:
        return json.load(f)


def text(rel: str) -> str:
    """加载纯文本配置（如 prompt 模板）。"""
    with open(os.path.join(_DIR, rel), encoding="utf-8") as f:
        return f.read()
