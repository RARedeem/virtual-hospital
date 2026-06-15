#!/usr/bin/env bash
# 拉取合规基座模型并构建自定义模型
# 在 docker compose up 之后执行
set -euo pipefail

OLLAMA="docker exec vh-ollama ollama"

echo "==> 拉取合规基座模型（约束 A：均非中国大陆机构开发）"
$OLLAMA pull gemma2:27b               # Google
$OLLAMA pull meditron:70b             # EPFL 瑞士
$OLLAMA pull nomic-embed-text:v1.5    # Nomic AI 美国

echo "==> 构建自定义模型（注入系统提示）"
docker cp ollama/modelfiles/translator-zh-en.Modelfile vh-ollama:/tmp/
docker cp ollama/modelfiles/translator-en-zh.Modelfile vh-ollama:/tmp/
docker cp ollama/modelfiles/reasoner-meditron.Modelfile vh-ollama:/tmp/

$OLLAMA create translator-zh-en -f /tmp/translator-zh-en.Modelfile
$OLLAMA create translator-en-zh -f /tmp/translator-en-zh.Modelfile
$OLLAMA create reasoner-meditron -f /tmp/reasoner-meditron.Modelfile

echo "==> 完成。已加载模型："
$OLLAMA list
