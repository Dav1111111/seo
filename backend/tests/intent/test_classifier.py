"""Unit tests for intent classifier."""

import pytest

from app.intent.classifier import classify_query, detect_brand
from app.intent.enums import IntentCode


# ── TRANS_BOOK ──────────────────────────────────────────────────────────

def test_classify_trans_book():
    r = classify_query("забронировать экскурсию на 33 водопада")
    assert r.intent == IntentCode.TRANS_BOOK
    assert r.confidence >= 0.9


def test_classify_trans_book_price():
    r = classify_query("сколько стоит экскурсия в абхазию")
    assert r.intent == IntentCode.TRANS_BOOK
    assert r.confidence >= 0.7


# ── COMM_COMPARE ────────────────────────────────────────────────────────

def test_classify_comm_compare_best():
    r = classify_query("лучшие экскурсии в сочи")
    assert r.intent == IntentCode.COMM_COMPARE


def test_classify_comm_compare_top():
    r = classify_query("топ 10 туров абхазия")
    assert r.intent == IntentCode.COMM_COMPARE


def test_classify_comm_compare_or():
    r = classify_query("роза хутор или красная поляна")
    assert r.intent == IntentCode.COMM_COMPARE


# ── INFO_LOGISTICS ──────────────────────────────────────────────────────

def test_classify_info_logistics():
    r = classify_query("как добраться до красной поляны")
    assert r.intent == IntentCode.INFO_LOGISTICS
    assert r.confidence >= 0.9


def test_classify_info_logistics_duration():
    r = classify_query("сколько ехать из сочи в абхазию")
    assert r.intent == IntentCode.INFO_LOGISTICS


# ── INFO_PREP ───────────────────────────────────────────────────────────

def test_classify_info_prep_what_to_take():
    r = classify_query("что взять на экскурсию в горы")
    assert r.intent == IntentCode.INFO_PREP


def test_classify_info_prep_when():
    r = classify_query("когда лучше ехать в сочи")
    assert r.intent == IntentCode.INFO_PREP


# ── INFO_DEST ───────────────────────────────────────────────────────────

def test_classify_info_dest():
    r = classify_query("что посмотреть в сочи")
    assert r.intent == IntentCode.INFO_DEST
    assert r.confidence >= 0.8


def test_classify_info_dest_attractions():
    r = classify_query("достопримечательности абхазии")
    assert r.intent == IntentCode.INFO_DEST


# ── LOCAL_GEO (критично для ЮК) ─────────────────────────────────────────

def test_classify_local_geo_loo():
    r = classify_query("экскурсии из лоо")
    assert r.intent == IntentCode.LOCAL_GEO
    assert r.confidence >= 0.85


def test_classify_local_geo_adler():
    r = classify_query("туры из адлера")
    assert r.intent == IntentCode.LOCAL_GEO


def test_classify_local_geo_what_to_see_from():
    r = classify_query("куда съездить из хосты")
    assert r.intent == IntentCode.LOCAL_GEO


# ── COMM_MODIFIED ───────────────────────────────────────────────────────

def test_classify_comm_modified():
    r = classify_query("экскурсии из сочи в абхазию на 1 день")
    assert r.intent == IntentCode.COMM_MODIFIED
    assert r.confidence >= 0.8


def test_classify_comm_modified_cheap():
    r = classify_query("туры недорого с детьми")
    assert r.intent == IntentCode.COMM_MODIFIED


# ── COMM_CATEGORY ───────────────────────────────────────────────────────

def test_classify_comm_category_simple():
    r = classify_query("экскурсии в сочи")
    assert r.intent == IntentCode.COMM_CATEGORY


# ── TRUST_LEGAL ─────────────────────────────────────────────────────────

def test_classify_trust_reviews():
    r = classify_query("отзывы о туроператоре сочи")
    assert r.intent == IntentCode.TRUST_LEGAL


def test_classify_trust_legal_safety():
    r = classify_query("безопасно ли в абхазии")
    assert r.intent == IntentCode.TRUST_LEGAL


# ── Brand detection ─────────────────────────────────────────────────────

def test_detect_brand_yuzhny_kontinent():
    assert detect_brand("южный континент сочи") is True


def test_detect_brand_gts():
    assert detect_brand("grand tour spirit официальный сайт") is True


def test_classify_brand_query():
    r = classify_query("южный континент сочи")
    assert r.intent == IntentCode.TRANS_BRAND
    assert r.is_brand is True


def test_classify_non_brand_query():
    r = classify_query("экскурсии в сочи")
    assert r.is_brand is False


# ── Ambiguous ───────────────────────────────────────────────────────────

def test_classify_ambiguous_short():
    r = classify_query("сочи")
    assert r.is_ambiguous is True


def test_classify_empty():
    r = classify_query("")
    assert r.confidence == 0.0
    assert r.is_ambiguous is True


# ── Funnel properties ───────────────────────────────────────────────────

def test_intent_funnel_stages():
    assert IntentCode.INFO_DEST.funnel_stage == "tofu"
    assert IntentCode.COMM_CATEGORY.funnel_stage == "mofu"
    assert IntentCode.TRANS_BOOK.funnel_stage == "bofu"


def test_intent_commercial_scores():
    assert IntentCode.TRANS_BOOK.commercial_score > IntentCode.INFO_DEST.commercial_score
    assert IntentCode.COMM_MODIFIED.commercial_score > IntentCode.COMM_CATEGORY.commercial_score
