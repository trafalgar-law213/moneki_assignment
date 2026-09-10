"""下载本地 embedding 模型（bge-small-zh-v1.5 的 ONNX 版）。

背景：本项目网络环境无法直连 HuggingFace（hf-mirror 现只做重定向），
改用 ModelScope（onnx-community 转制版，国内直连可用）。部署/首次运行前执行一次：

    cd backend && python scripts/download_model.py

模型约 95MB，落在 backend/models/bge-small-zh-v1.5-onnx/（已 gitignore，构建时打包进镜像）。
缺失时应用正常启动，仅「历史问答 · 语义检索」功能自动降级不可用。
"""

import sys
from pathlib import Path

REPO_BACKEND = Path(__file__).resolve().parent.parent
LOCAL_DIR = REPO_BACKEND / "models" / "bge-small-zh-v1.5-onnx"

# 只取推理需要的文件（fp32 ONNX 图 + 权重 + 分词器）
ALLOW = [
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "onnx/model.onnx",
    "onnx/model.onnx_data",
]


def main() -> None:
    try:
        from modelscope import snapshot_download
    except ImportError:
        sys.exit("缺少 modelscope：pip install -r requirements.txt")
    path = snapshot_download(
        "onnx-community/bge-small-zh-v1.5-ONNX",
        allow_patterns=ALLOW,
        local_dir=str(LOCAL_DIR),
    )
    print(f"模型已就绪：{path}")


if __name__ == "__main__":
    main()
