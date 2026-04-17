"""MinHash signature build/load + Jaccard computation."""

from datasketch import LeanMinHash, MinHash

from app.fingerprint.version import MINHASH_NUM_PERM, MINHASH_SEED


def build_minhash(shingles: set[str], num_perm: int = MINHASH_NUM_PERM) -> bytes:
    """Build a MinHash from a set of shingles, return serialized bytes.

    Uses LeanMinHash for compact serialization (~512 bytes for 128 perms).
    Deterministic thanks to fixed seed.
    """
    mh = MinHash(num_perm=num_perm, seed=MINHASH_SEED)
    for s in shingles:
        mh.update(s.encode("utf-8"))
    lean = LeanMinHash(mh)
    return _lean_to_bytes(lean)


def load_minhash(blob: bytes) -> LeanMinHash:
    """Deserialize LeanMinHash from bytes produced by build_minhash."""
    return _bytes_to_lean(blob)


def jaccard(blob_a: bytes, blob_b: bytes) -> float:
    """Estimated Jaccard similarity between two MinHash signatures."""
    a = load_minhash(blob_a)
    b = load_minhash(blob_b)
    if a.seed != b.seed:
        raise ValueError(f"MinHash seed mismatch: {a.seed} vs {b.seed}")
    return a.jaccard(b)


# ── Serialization ──────────────────────────────────────────────────────

def _lean_to_bytes(lean: LeanMinHash) -> bytes:
    """Serialize LeanMinHash to bytes.

    datasketch doesn't expose a stable binary format directly; we pack
    num_perm, seed, and uint32 hashvalues into a simple binary layout.
    """
    import struct

    hashvalues = lean.hashvalues
    # Header: num_perm (I), seed (I)
    header = struct.pack("<II", len(hashvalues), lean.seed)
    # Body: num_perm uint32 hashvalues
    body = hashvalues.astype("<u4").tobytes()
    return header + body


def _bytes_to_lean(blob: bytes) -> LeanMinHash:
    import struct

    import numpy as np

    num_perm, seed = struct.unpack("<II", blob[:8])
    hashvalues = np.frombuffer(blob[8:8 + num_perm * 4], dtype="<u4")
    # LeanMinHash expects an iterable; construct via MinHash
    mh = MinHash(num_perm=num_perm, seed=seed, hashvalues=hashvalues.copy())
    return LeanMinHash(mh)
