"""Pure-logic tests for CTR benchmarks (no DB)."""

from app.core_audit.behavioral.benchmarks import (
    CTR_BY_POSITION,
    CTR_BY_POSITION_COMMERCIAL,
    CTR_BY_POSITION_INFO,
    POSITION_FLOOR,
    ctr_gap_severity,
    expected_ctr,
    expected_ctr_for_position,
)


class TestExpectedCtrForPosition:
    def test_integer_positions_match_table(self):
        for pos, ctr in CTR_BY_POSITION.items():
            assert expected_ctr_for_position(float(pos)) == ctr

    def test_fractional_position_interpolates(self):
        # Between pos 1 (0.27) and pos 2 (0.16) — midpoint is ~0.215
        result = expected_ctr_for_position(1.5)
        assert 0.21 < result < 0.22

    def test_position_below_one_returns_none(self):
        assert expected_ctr_for_position(0.5) is None

    def test_position_beyond_floor_returns_none(self):
        assert expected_ctr_for_position(POSITION_FLOOR + 1) is None

    def test_none_input_returns_none(self):
        assert expected_ctr_for_position(None) is None


class TestCtrGapSeverity:
    def test_no_gap_when_actual_meets_expected(self):
        assert ctr_gap_severity(actual_ctr=0.10, expected_ctr=0.10, impressions=500) == ""

    def test_no_gap_above_threshold(self):
        # actual / expected = 0.7, above 0.6 threshold
        assert ctr_gap_severity(actual_ctr=0.07, expected_ctr=0.10, impressions=500) == ""

    def test_mild_gap_high_traffic(self):
        # ratio = 0.5 (mild), 1500 impressions → high severity
        assert ctr_gap_severity(actual_ctr=0.05, expected_ctr=0.10, impressions=1500) == "high"

    def test_mild_gap_medium_traffic(self):
        # ratio = 0.5, 500 impressions → medium
        assert ctr_gap_severity(actual_ctr=0.05, expected_ctr=0.10, impressions=500) == "medium"

    def test_mild_gap_low_traffic(self):
        # ratio = 0.5, 150 impressions → low
        assert ctr_gap_severity(actual_ctr=0.05, expected_ctr=0.10, impressions=150) == "low"

    def test_severe_gap_high_traffic_is_critical(self):
        # ratio = 0.2, 1500 impressions → critical (snippet broken on hot page)
        assert ctr_gap_severity(actual_ctr=0.02, expected_ctr=0.10, impressions=1500) == "critical"

    def test_severe_gap_medium_traffic_is_high(self):
        assert ctr_gap_severity(actual_ctr=0.02, expected_ctr=0.10, impressions=500) == "high"

    def test_zero_expected_returns_empty(self):
        # Defensive: if benchmarks ever returns 0 we don't divide
        assert ctr_gap_severity(actual_ctr=0.05, expected_ctr=0.0, impressions=500) == ""


class TestIntentStratifiedCtr:
    """CTR curves are intent-aware — informational pages get a lower
    expectations curve than commercial / transactional ones, so we
    don't flag healthy info pages as «недоклик» for matching what's
    actually normal CTR for their intent."""

    def test_commercial_curve_is_strictly_above_info(self):
        # At every integer position commercial > info — that's the
        # whole point of the stratification.
        for pos in CTR_BY_POSITION_COMMERCIAL:
            assert CTR_BY_POSITION_COMMERCIAL[pos] > CTR_BY_POSITION_INFO[pos], pos

    def test_expected_ctr_commercial_vs_info_at_position_1(self):
        assert expected_ctr(1, "commercial") > expected_ctr(1, "info")

    def test_expected_ctr_for_position_uses_info_curve(self):
        # At position 1 the info curve is ~0.12, well below the
        # commercial 0.27 — must propagate through to the fractional
        # interpolation helper too.
        info = expected_ctr_for_position(1.0, intent_code="info")
        commercial = expected_ctr_for_position(1.0, intent_code="commercial")
        assert info is not None and commercial is not None
        assert info < commercial

    def test_default_intent_is_commercial(self):
        # Conservative default: when the caller can't determine the
        # intent, we use the commercial curve (higher expectations →
        # more false positives, never false negatives).
        assert expected_ctr_for_position(3.0) == expected_ctr_for_position(
            3.0, intent_code="commercial",
        )

    def test_expected_ctr_out_of_range_returns_zero(self):
        assert expected_ctr(0) == 0.0
        assert expected_ctr(POSITION_FLOOR + 1) == 0.0

    def test_back_compat_alias(self):
        # CTR_BY_POSITION must remain the commercial curve so legacy
        # callers that import it directly keep getting the same values.
        assert CTR_BY_POSITION is CTR_BY_POSITION_COMMERCIAL
