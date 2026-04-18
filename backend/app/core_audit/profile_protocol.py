"""SiteProfile Protocol + data shapes that verticals populate.

A profile is a plain Python module that assembles these dataclasses and calls
`registry.register_profile(vertical, business_model, profile)` at import time.

Design rules:
  - Profile exposes DATA, not behavior (except URL/title proposers that need
    site state). The universal engines iterate this data.
  - Two-level taxonomy only: (vertical, business_model). No deeper inheritance.
  - Profiles must not import from each other. Cross-profile reuse = copy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.core_audit.intent_codes import IntentCode

if TYPE_CHECKING:
    from app.core_audit.demand_map.dto import SeedTemplate


@dataclass(frozen=True)
class IntentRule:
    """A regex rule that maps text to an intent with a confidence weight.

    `examples` is documentation, not used at runtime.
    """
    intent: IntentCode
    pattern: re.Pattern
    weight: float                       # 0.0 - 1.0, used as confidence score
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class PageRequirements:
    """What a well-formed page for a given intent must contain.

    Consumed by Module 3 (Page Review) to grade existing pages and by
    decision_tree.propose_title for URL/title shaping.

    H2 blocks are split into two tiers so the review pipeline can emit
    different severities:
      - critical_h2_blocks: missing them is a hard gap (commercial intent,
        blocks the user task). Missing → severity=critical/high.
      - recommended_h2_blocks: nice-to-have sections that improve coverage
        and E-E-A-T. Missing → severity=medium/low.
    """
    intent: IntentCode
    critical_h2_blocks: tuple[str, ...] = ()
    recommended_h2_blocks: tuple[str, ...] = ()
    required_affordances: tuple[str, ...] = ()
    minimum_word_count: int = 0

    @property
    def required_h2_blocks(self) -> tuple[str, ...]:
        """Back-compat alias — union of critical + recommended."""
        return self.critical_h2_blocks + self.recommended_h2_blocks


@dataclass(frozen=True)
class EEATSignal:
    """Trust signal detectable in page HTML/text.

    Tourism example: РТО number regex → weight 0.4.
    Finance example: лицензия ЦБ → weight 0.5.
    """
    name: str
    pattern: re.Pattern
    weight: float
    priority: str = "medium"            # critical | high | medium | low


@dataclass(frozen=True)
class CommercialFactor:
    """Yandex commercial ranking factor (industry-specific flavor).

    Some factors are boolean presence checks (e.g. phone in header); others
    are positional (price above-fold) — detection may require Module 3 LLM.
    """
    name: str
    detection_pattern: re.Pattern | None = None    # None = LLM-checked
    priority: str = "medium"
    description_ru: str = ""


@runtime_checkable
class SiteProfile(Protocol):
    """Contract every vertical profile must satisfy.

    Populated by `profiles/<vertical>/__init__.py` which assembles the
    dataclasses and registers the profile. Engines consume this via duck
    typing — runtime_checkable allows isinstance() if needed.
    """

    # Identity
    vertical: str
    business_model: str

    # Query → intent
    intent_rules: tuple[IntentRule, ...]
    brand_tokens: frozenset[str]                   # per-vertical fallback; site-level brands override at call-time

    # Standalone value test inputs
    unique_entity_patterns: tuple[re.Pattern, ...]
    generic_modifier_patterns: tuple[re.Pattern, ...]

    # Page scoring inputs
    url_patterns: dict[IntentCode, tuple[re.Pattern, ...]]
    content_signals: dict[IntentCode, tuple[re.Pattern, ...]]
    cta_patterns_booking: tuple[re.Pattern, ...]
    cta_patterns_info: tuple[re.Pattern, ...]

    # Review rubric (consumed by Module 3)
    page_requirements: dict[IntentCode, PageRequirements]
    schema_rules: dict[IntentCode, tuple[str, ...]]   # Schema.org types to recommend
    eeat_signals: tuple[EEATSignal, ...]
    commercial_factors: tuple[CommercialFactor, ...]

    # Target Demand Map (Phase A) — vertical-level seed templates for
    # deterministic Cartesian expansion. Default empty tuple keeps older
    # profiles working unchanged.
    seed_cluster_templates: "tuple[SeedTemplate, ...]"

    # Site-state-dependent helpers (functions, not data)
    def propose_url(self, intent: IntentCode, top_query: str) -> str: ...
    def propose_title(self, intent: IntentCode, top_query: str) -> str: ...


@dataclass(frozen=True)
class ProfileData:
    """Concrete container used by profiles that don't need custom methods.

    Vertical profiles can use this dataclass directly and attach proposer
    functions at instantiation time, or define their own class that
    satisfies the SiteProfile Protocol.
    """
    vertical: str
    business_model: str
    intent_rules: tuple[IntentRule, ...] = ()
    brand_tokens: frozenset[str] = field(default_factory=frozenset)
    unique_entity_patterns: tuple[re.Pattern, ...] = ()
    generic_modifier_patterns: tuple[re.Pattern, ...] = ()
    url_patterns: dict[IntentCode, tuple[re.Pattern, ...]] = field(default_factory=dict)
    content_signals: dict[IntentCode, tuple[re.Pattern, ...]] = field(default_factory=dict)
    cta_patterns_booking: tuple[re.Pattern, ...] = ()
    cta_patterns_info: tuple[re.Pattern, ...] = ()
    fallback_commercial_pattern: re.Pattern | None = None    # e.g. service+geo combo in tourism
    doorway_spam_url_patterns: tuple[re.Pattern, ...] = ()   # doorway triggers (year spam, cheap-words)
    page_requirements: dict[IntentCode, PageRequirements] = field(default_factory=dict)
    schema_rules: dict[IntentCode, tuple[str, ...]] = field(default_factory=dict)
    eeat_signals: tuple[EEATSignal, ...] = ()
    commercial_factors: tuple[CommercialFactor, ...] = ()
    # Target Demand Map seeds — populated by vertical's ./seed_templates.py.
    seed_cluster_templates: "tuple[SeedTemplate, ...]" = ()
