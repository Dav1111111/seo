"""Unit tests for composite_hash — must be deterministic + case-fold + intent-aware."""

from __future__ import annotations

import re

import pytest

from app.core_audit.review.hash_utils import compute_composite_hash


H = "abc123def456"  # placeholder content_hash


def test_deterministic_same_inputs():
    a = compute_composite_hash(H, "Title", "Meta", "H1", "comm_modified")
    b = compute_composite_hash(H, "Title", "Meta", "H1", "comm_modified")
    assert a == b


def test_64_char_hex():
    h = compute_composite_hash(H, "t", "m", "h", "comm_modified")
    assert re.fullmatch(r"[0-9a-f]{64}", h)


def test_none_and_empty_equivalent():
    a = compute_composite_hash(H, None, None, None, "comm_modified")
    b = compute_composite_hash(H, "", "", "", "comm_modified")
    assert a == b


def test_case_and_whitespace_fold():
    variants = [
        ("Title", "Meta", "H1"),
        ("title", "meta", "h1"),
        ("TITLE", "META", "H1"),
        ("Title ", "Meta ", "H1"),
        ("Title\t", "Meta\n", "H1\u00a0"),      # NBSP after NFKC collapses
        ("  Title  ", "  Meta  ", "  H1  "),
    ]
    hashes = {compute_composite_hash(H, t, m, h, "i") for t, m, h in variants}
    assert len(hashes) == 1


def test_punctuation_preserved():
    a = compute_composite_hash(H, "Title!", "x", "x", "i")
    b = compute_composite_hash(H, "Title.", "x", "x", "i")
    assert a != b


def test_field_separator_prevents_ambiguity():
    # Adjacent field shifts must NOT collide
    a = compute_composite_hash(H, "a", "b", "", "i")
    b = compute_composite_hash(H, "", "a", "b", "i")
    assert a != b


def test_intent_is_part_of_hash():
    a = compute_composite_hash(H, "t", "m", "h", "comm_modified")
    b = compute_composite_hash(H, "t", "m", "h", "info_dest")
    assert a != b


def test_content_hash_affects_output():
    a = compute_composite_hash("hash_v1", "t", "m", "h", "i")
    b = compute_composite_hash("hash_v2", "t", "m", "h", "i")
    assert a != b
