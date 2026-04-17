"""Intent enums — 10-category tourism taxonomy."""

from enum import Enum


class IntentCode(str, Enum):
    """10-category intent taxonomy for Russian tourism niche.

    From seo-content skill methodology:
    - TOFU (top-of-funnel): informational, user just exploring
    - MOFU (middle): commercial, user comparing
    - BOFU (bottom): transactional, user ready to book
    """
    # Top-of-funnel (informational)
    INFO_DEST = "info_dest"                # "что посмотреть в Сочи"
    INFO_LOGISTICS = "info_logistics"      # "как добраться до Красной Поляны"
    INFO_PREP = "info_prep"                # "что взять в экскурсию", "когда ехать"

    # Middle-of-funnel (commercial)
    COMM_COMPARE = "comm_compare"          # "лучшие", "топ", "или X или Y"
    COMM_CATEGORY = "comm_category"        # "экскурсии в Сочи" (без модификатора)
    COMM_MODIFIED = "comm_modified"        # "экскурсии из Сочи в Абхазию на 1 день"

    # Bottom-of-funnel (transactional)
    TRANS_BOOK = "trans_book"              # "забронировать", "купить тур"
    TRANS_BRAND = "trans_brand"            # брендовые запросы
    LOCAL_GEO = "local_geo"                # "экскурсии из Адлера" — высокогранулярная гео
    TRUST_LEGAL = "trust_legal"            # "отзывы о туроператоре", "лицензия"

    @property
    def funnel_stage(self) -> str:
        if self in (IntentCode.INFO_DEST, IntentCode.INFO_LOGISTICS, IntentCode.INFO_PREP):
            return "tofu"
        if self in (IntentCode.COMM_COMPARE, IntentCode.COMM_CATEGORY, IntentCode.COMM_MODIFIED):
            return "mofu"
        return "bofu"

    @property
    def commercial_score(self) -> float:
        scores = {
            IntentCode.INFO_DEST: 0.1,
            IntentCode.INFO_LOGISTICS: 0.2,
            IntentCode.INFO_PREP: 0.3,
            IntentCode.COMM_COMPARE: 0.6,
            IntentCode.COMM_CATEGORY: 0.7,
            IntentCode.COMM_MODIFIED: 0.85,
            IntentCode.TRANS_BOOK: 0.95,
            IntentCode.TRANS_BRAND: 0.9,
            IntentCode.LOCAL_GEO: 0.8,
            IntentCode.TRUST_LEGAL: 0.3,
        }
        return scores[self]


class CoverageStatus(str, Enum):
    """Coverage quality for intent × page pair."""
    strong = "strong"        # score 4-5 — well-covered
    weak = "weak"            # score 2-3 — partial
    missing = "missing"      # score 0-1 — no page serves intent
    over_covered = "over_covered"  # 2+ pages compete (cannibalization)


class CoverageAction(str, Enum):
    """Recommendation produced by decision tree."""
    strengthen = "strengthen"   # усилить существующую
    create = "create"           # создать новую (after standalone test pass)
    merge = "merge"             # объединить дубли
    split = "split"             # разделить страницу с конфликтом intents
    leave = "leave"             # ничего не делать (низкий объём / шум)
    block_create = "block_create"  # rejected by standalone value test
