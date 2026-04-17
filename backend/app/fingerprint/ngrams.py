"""Char n-gram TF-IDF-hashed vector build/load + cosine similarity.

Serialization format (ngram_format_version = "v1"):
  Header: 4 ASCII bytes "v1  " (space-padded) to identify format version
  Body:   np.savez_compressed with:
            indptr  (int32)
            indices (int32)
            data    (float32)
            shape   (int32[2])
"""

import io

import numpy as np
from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import HashingVectorizer

from app.fingerprint.version import (
    NGRAM_FORMAT_VERSION,
    NGRAM_N_FEATURES,
    NGRAM_RANGE,
)

# Cached vectorizer (deterministic, no fit)
_VECTORIZER = HashingVectorizer(
    n_features=NGRAM_N_FEATURES,
    analyzer="char_wb",
    ngram_range=NGRAM_RANGE,
    norm="l2",
    alternate_sign=False,  # keep positive for cosine stability
)


def build_ngram_vector(text: str) -> bytes:
    """Build a hashed-char-n-gram sparse vector from text, serialize to bytes."""
    if not text:
        return _serialize_sparse(csr_matrix((1, NGRAM_N_FEATURES), dtype="float32"))
    vec = _VECTORIZER.transform([text]).astype("float32")
    return _serialize_sparse(vec)


def load_ngram_vector(blob: bytes) -> csr_matrix:
    """Deserialize bytes back to scipy.sparse.csr_matrix."""
    return _deserialize_sparse(blob)


def cosine(blob_a: bytes, blob_b: bytes) -> float:
    """Cosine similarity between two ngram vectors. Both are L2-normalized, so dot = cosine."""
    a = load_ngram_vector(blob_a)
    b = load_ngram_vector(blob_b)
    if a.shape != b.shape:
        raise ValueError(f"ngram shape mismatch: {a.shape} vs {b.shape}")
    # Dot product of two l2-normalized sparse vectors
    dot = a.multiply(b).sum()
    return max(0.0, min(1.0, float(dot)))


# ── Serialization with format version header ───────────────────────────

def _serialize_sparse(mat: csr_matrix) -> bytes:
    buf = io.BytesIO()
    # Header: 4 ASCII bytes for format version (space-padded to 4 chars)
    header = NGRAM_FORMAT_VERSION.ljust(4)[:4].encode("ascii")
    buf.write(header)
    np.savez_compressed(
        buf,
        indptr=mat.indptr.astype("int32"),
        indices=mat.indices.astype("int32"),
        data=mat.data.astype("float32"),
        shape=np.array(mat.shape, dtype="int32"),
    )
    return buf.getvalue()


def _deserialize_sparse(blob: bytes) -> csr_matrix:
    if len(blob) < 4:
        raise ValueError("ngram blob too short")
    version = blob[:4].decode("ascii").strip()
    if version != NGRAM_FORMAT_VERSION.strip():
        raise ValueError(f"ngram format version mismatch: blob={version!r} current={NGRAM_FORMAT_VERSION!r}")
    with np.load(io.BytesIO(blob[4:])) as data:
        indptr = data["indptr"]
        indices = data["indices"]
        values = data["data"]
        shape = tuple(int(x) for x in data["shape"])
    return csr_matrix((values, indices, indptr), shape=shape, dtype="float32")


def get_format_version() -> str:
    return NGRAM_FORMAT_VERSION
