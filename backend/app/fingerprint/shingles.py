"""Word shingles for MinHash input."""

from app.fingerprint.version import SHINGLE_SIZE


def word_shingles(tokens: list[str], k: int = SHINGLE_SIZE) -> set[str]:
    """Build k-word shingles from a list of tokens.

    If tokens shorter than k, returns tokens joined as single shingle (if any).
    Returns a set (dedup automatically).
    """
    if not tokens:
        return set()
    if len(tokens) < k:
        return {" ".join(tokens)}
    return {" ".join(tokens[i:i + k]) for i in range(len(tokens) - k + 1)}
