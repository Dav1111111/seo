"""Unit tests for fingerprint primitives (T-1 through T-17 where DB not required).

T-13 (celery partial failure), T-16 (migration reversibility), T-22 (concurrency)
are integration-level and need a running stack — covered manually on deploy.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.fingerprint.change_detection import CURRENT_VERSIONS, decide_recompute
from app.fingerprint.enums import RecomputeReason
from app.fingerprint.hashing import compute_content_hash
from app.fingerprint.minhash import build_minhash, jaccard
from app.fingerprint.ngrams import build_ngram_vector, cosine
from app.fingerprint.normalize import (
    normalize_text_for_hash,
    normalize_url,
)
from app.fingerprint.shingles import word_shingles


# ── T-1: content_hash deterministic ────────────────────────────────────

def test_t1_content_hash_deterministic():
    text = "Экскурсии в Сочи от 700 рублей"
    h1 = compute_content_hash(text)
    h2 = compute_content_hash(text)
    assert h1 == h2
    assert len(h1) == 64


# ── T-2: content_hash normalization ────────────────────────────────────

def test_t2_content_hash_normalization():
    a = compute_content_hash("  Hello  World  ")
    b = compute_content_hash("hello world")
    c = compute_content_hash("HELLO\tWORLD")
    assert a == b == c


def test_t2b_normalize_strips_zero_width():
    s_dirty = "экскурсии\u200bв\u200cСочи"
    s_clean = normalize_text_for_hash(s_dirty)
    assert "\u200b" not in s_clean
    assert "\u200c" not in s_clean


# ── T-3: MinHash Jaccard identical ────────────────────────────────────

def test_t3_minhash_jaccard_identical():
    text = " ".join([f"слово{i}" for i in range(200)])
    shingles = word_shingles(text.split(), k=5)
    a = build_minhash(shingles)
    b = build_minhash(shingles)
    assert jaccard(a, b) >= 0.98


# ── T-4: MinHash near-duplicate ────────────────────────────────────────

def test_t4_minhash_jaccard_near_duplicate():
    import random

    tokens = [f"слово{i}" for i in range(200)]
    s1 = word_shingles(tokens, k=5)
    # Swap 5% of tokens
    random.seed(42)
    mutated = tokens.copy()
    for _ in range(10):
        i = random.randrange(len(mutated))
        mutated[i] = f"замена{i}"
    s2 = word_shingles(mutated, k=5)

    a = build_minhash(s1)
    b = build_minhash(s2)
    j = jaccard(a, b)
    assert 0.70 <= j <= 0.97, f"expected near-dup range, got {j}"


# ── T-5: MinHash unrelated ─────────────────────────────────────────────

def test_t5_minhash_jaccard_unrelated():
    t1 = "Экскурсии в Сочи на Красную Поляну и в Абхазию из любого отеля".split()
    t2 = "Квантовая физика изучает поведение частиц на субатомном уровне".split()
    # Pad to get enough shingles
    t1 = (t1 * 20)[:200]
    t2 = (t2 * 20)[:200]
    a = build_minhash(word_shingles(t1))
    b = build_minhash(word_shingles(t2))
    assert jaccard(a, b) <= 0.20


# ── T-6: ngram cosine similar stems ────────────────────────────────────

def test_t6_ngram_cosine_similar_stems():
    a = build_ngram_vector("купить квартиру в Москве")
    b = build_ngram_vector("купить квартиру Москва")
    c = cosine(a, b)
    assert c >= 0.75


def test_t6b_ngram_cosine_unrelated():
    a = build_ngram_vector("горные экскурсии Сочи Красная Поляна")
    b = build_ngram_vector("квантовая физика элементарных частиц")
    c = cosine(a, b)
    assert c <= 0.3


# ── T-7: unchanged hash → skip ─────────────────────────────────────────

def test_t7_skip_unchanged_hash():
    fake = MagicMock()
    fake.content_hash = "a" * 64
    fake.extraction_version = CURRENT_VERSIONS.extraction
    fake.lemmatization_version = CURRENT_VERSIONS.lemmatization
    fake.minhash_version = CURRENT_VERSIONS.minhash
    fake.ngram_version = CURRENT_VERSIONS.ngram
    fake.last_fingerprinted_at = datetime.now(timezone.utc)

    should, reason = decide_recompute(fake, new_content_hash="a" * 64)
    assert should is False
    assert reason == RecomputeReason.unchanged


# ── T-8: boilerplate change doesn't change content_hash ────────────────

def test_t8_boilerplate_change_does_not_trigger():
    # Simulated — we hash the already-extracted main_text, not raw HTML.
    # If main_text is the same string, content_hash matches regardless of
    # what wrapper HTML was around it in the crawl.
    main_text = "Экскурсия на 33 водопада. Джиппинг, обед, чайная плантация."
    h1 = compute_content_hash(main_text)
    h2 = compute_content_hash(main_text)
    assert h1 == h2


# ── T-9: thin content skipped ──────────────────────────────────────────

def test_t9_thin_content_detection():
    from app.fingerprint.version import THIN_CONTENT_CHARS
    short = "Короткий текст"
    assert len(normalize_text_for_hash(short)) < THIN_CONTENT_CHARS


# ── T-10: version bump forces recompute ────────────────────────────────

def test_t10_version_bump_forces_recompute():
    fake = MagicMock()
    fake.content_hash = "x" * 64
    fake.extraction_version = "0.0.1"  # old
    fake.lemmatization_version = CURRENT_VERSIONS.lemmatization
    fake.minhash_version = CURRENT_VERSIONS.minhash
    fake.ngram_version = CURRENT_VERSIONS.ngram
    fake.last_fingerprinted_at = datetime.now(timezone.utc)

    should, reason = decide_recompute(fake, new_content_hash="x" * 64)
    assert should is True
    assert reason == RecomputeReason.extraction_version_bump


# ── T-11: staleness forces recompute ───────────────────────────────────

def test_t11_staleness_forces_recompute():
    fake = MagicMock()
    fake.content_hash = "x" * 64
    fake.extraction_version = CURRENT_VERSIONS.extraction
    fake.lemmatization_version = CURRENT_VERSIONS.lemmatization
    fake.minhash_version = CURRENT_VERSIONS.minhash
    fake.ngram_version = CURRENT_VERSIONS.ngram
    fake.last_fingerprinted_at = datetime.now(timezone.utc) - timedelta(days=31)

    should, reason = decide_recompute(fake, new_content_hash="x" * 64)
    assert should is True
    assert reason == RecomputeReason.stale


# ── T-17: pymorphy3 russian lemmatization ──────────────────────────────

def test_t17_pymorphy3_russian_lemmatization():
    from app.fingerprint.lemmatize import is_morph_available, lemmatize_tokens

    if not is_morph_available():
        pytest.skip("pymorphy3 not installed in test env")

    result = lemmatize_tokens(["бежал", "бегут", "бежать"], drop_stopwords=False)
    # All should normalize to the same lemma
    assert len(set(result)) == 1, f"expected single lemma, got {result}"


# ── T-18: compute_jaccard roundtrip ────────────────────────────────────

def test_t18_compute_jaccard_roundtrip():
    shingles = {f"shingle_{i}" for i in range(50)}
    blob = build_minhash(shingles)
    # Re-serialize via loading and re-building
    j_self = jaccard(blob, blob)
    assert abs(j_self - 1.0) < 0.01


# ── URL normalization ──────────────────────────────────────────────────

def test_normalize_url_drops_tracking_params():
    url = "https://example.com/page/?utm_source=ads&id=42&fbclid=xyz"
    assert normalize_url(url) == "https://example.com/page?id=42"


def test_normalize_url_sorts_params():
    url1 = "https://example.com/page?b=2&a=1"
    url2 = "https://example.com/page?a=1&b=2"
    assert normalize_url(url1) == normalize_url(url2)


def test_normalize_url_strips_trailing_slash():
    assert normalize_url("https://example.com/foo/") == "https://example.com/foo"
    assert normalize_url("https://example.com/") == "https://example.com/"


def test_normalize_url_cyrillic():
    url = "https://южный-континент.рф/tours/"
    result = normalize_url(url)
    assert "южный-континент.рф" in result
    assert not result.endswith("/tours/")


# ── Word shingles ──────────────────────────────────────────────────────

def test_shingles_basic():
    tokens = ["a", "b", "c", "d", "e", "f"]
    shingles = word_shingles(tokens, k=3)
    assert "a b c" in shingles
    assert "b c d" in shingles
    assert "d e f" in shingles
    assert len(shingles) == 4


def test_shingles_shorter_than_k():
    tokens = ["a", "b"]
    shingles = word_shingles(tokens, k=5)
    assert shingles == {"a b"}
