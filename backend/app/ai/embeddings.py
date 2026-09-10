"""本地文本向量（embedding）：bge-small-zh-v1.5，自实现 ONNX 推理。

背景：本项目网络环境无法直连 HuggingFace（hf-mirror 现只做重定向），模型经
ModelScope 获取（onnx-community 转制的 ONNX 版，见 scripts/download_model.py）；
推理用 onnxruntime + tokenizers 直接跑，不引入 torch / fastembed 等重依赖。

池化按 BAAI 官方口径：CLS pooling（取 [CLS] 位置隐状态）+ L2 归一化。
测试通过 monkeypatch 注入假向量（tests/test_history.py），CI 不需要模型文件。
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

DIM = 512  # bge-small-zh-v1.5 隐藏维度
_DEFAULT_DIR = Path(__file__).resolve().parent.parent.parent / "models" / "bge-small-zh-v1.5-onnx"

_session = None
_tokenizer = None


def model_dir() -> Path:
    return Path(os.environ.get("EMBEDDING_MODEL_DIR", _DEFAULT_DIR))


def is_available() -> bool:
    return (model_dir() / "onnx" / "model.onnx").exists() and (model_dir() / "tokenizer.json").exists()


def _load():
    global _session, _tokenizer
    if _session is None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        d = model_dir()
        _session = ort.InferenceSession(str(d / "onnx" / "model.onnx"), providers=["CPUExecutionProvider"])
        _tokenizer = Tokenizer.from_file(str(d / "tokenizer.json"))
        _tokenizer.enable_truncation(max_length=512)
        _tokenizer.enable_padding()
    return _session, _tokenizer


def embed(texts: list[str]) -> list[list[float]]:
    """把文本编码为归一化向量（批量）。模型文件缺失时抛 RuntimeError（上层决定降级）。"""
    if not is_available():
        raise RuntimeError(f"embedding 模型缺失：{model_dir()}（用 scripts/download_model.py 获取）")
    session, tokenizer = _load()
    encs = tokenizer.encode_batch(texts)
    input_ids = np.array([e.ids for e in encs], dtype=np.int64)
    attention_mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
    feeds = {"input_ids": input_ids, "attention_mask": attention_mask}
    if any(i.name == "token_type_ids" for i in session.get_inputs()):
        feeds["token_type_ids"] = np.zeros_like(input_ids)
    out = session.run(None, feeds)[0]      # [batch, seq, 512]，个别导出可能已池化
    vecs = out[:, 0, :] if out.ndim == 3 else out   # CLS pooling
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (vecs / norms).astype(float).tolist()
