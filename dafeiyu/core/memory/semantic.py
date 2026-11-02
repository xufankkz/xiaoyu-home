"""Local sentence embeddings with an explicit lexical fallback in embed.py.

Assets are revision-pinned by scripts/install_local_retrieval.py. Loading this
module never downloads a model or sends text to a service.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import threading

from .. import settings

_lock = threading.RLock()
_encoder = None
_error = ""
MODEL_ID = "multilingual-minilm-l12-v2-quint8-e8f8c211"


def model_dir() -> Path:
    return settings.ROOT / "models/retrieval"


def available() -> bool:
    return (model_dir() / "model.onnx").is_file() and (model_dir() / "tokenizer.json").is_file()


def note_error(exc: Exception) -> None:
    global _error
    _error = str(exc)[:240]


def encoder():
    global _encoder, _error
    with _lock:
        root = model_dir()
        if _encoder is not None and _encoder[0] == root:
            return _encoder[1:]
        if not available():
            raise RuntimeError("本地语义模型未安装")
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            session = ort.InferenceSession(str(root / "model.onnx"), sess_options=options,
                                           providers=["CPUExecutionProvider"])
            tokenizer = Tokenizer.from_file(str(root / "tokenizer.json"))
            tokenizer.enable_truncation(max_length=128)
            tokenizer.enable_padding()
            _encoder = (root, session, tokenizer)
            _error = ""
            return session, tokenizer
        except Exception as exc:
            _error = str(exc)
            raise


def encode_batch(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    import numpy as np
    with _lock:
        session, tokenizer = encoder()
        encoded = tokenizer.encode_batch([str(t or "") for t in texts])
        values = {
            "input_ids": np.asarray([e.ids for e in encoded], dtype=np.int64),
            "attention_mask": np.asarray([e.attention_mask for e in encoded], dtype=np.int64),
            "token_type_ids": np.asarray([e.type_ids for e in encoded], dtype=np.int64),
        }
        output = session.run(None, {i.name: values[i.name] for i in session.get_inputs()})[0]
        # The published model uses mean token pooling and cosine similarity.
        if output.ndim == 3:
            mask = values["attention_mask"][..., None]
            output = (output * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1)
        norms = np.linalg.norm(output, axis=1, keepdims=True)
        return (output / np.maximum(norms, 1e-12)).tolist()


@lru_cache(maxsize=4096)
def encode(text: str) -> tuple[float, ...]:
    return tuple(encode_batch([text])[0])


def status() -> dict:
    return {"available": available(), "loaded": _encoder is not None,
            "backend": MODEL_ID if available() and not _error else "lexical-bigram-fallback", "error": _error}
