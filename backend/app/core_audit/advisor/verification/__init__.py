"""Advice-card auto-verification (post-«Применил»).

Two-signal model:

  * **Technical fix** (this package, instant): did the underlying
    fact actually change on the live site/data? Deterministic only —
    NO LLM. The verifier re-runs the same Python checks that produced
    the card in the first place.

  * **SEO effect** (out of scope here, 14 days later): did rankings
    or clicks move? Already wired by `outcomes_followup_daily`.

Public surface (frontend agent reads this):

    from app.core_audit.advisor.verification import (
        verify_card, VerificationResult,
    )

`verify_card(...)` dispatches by `card_category` + `card_id` prefix to
the right deterministic verifier; if no verifier matches we return
``user_attested`` instead of silently lying with ``verified``.
"""

from app.core_audit.advisor.verification.dispatcher import (
    VerificationResult,
    verify_card,
)

__all__ = ["verify_card", "VerificationResult"]
