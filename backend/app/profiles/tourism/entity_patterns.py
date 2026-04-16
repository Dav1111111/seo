"""Named entities vs generic modifiers for the Standalone Value Test (C1).

Unique entity = thing deserving its own page (named attraction, specific
pickup city, named route). Generic modifier = adjective/descriptor that does
NOT justify a new page.
"""

from __future__ import annotations

import re


TOURISM_UNIQUE_ENTITY_PATTERNS: tuple[re.Pattern, ...] = (
    # Named tourist attractions
    re.compile(
        r"\b(рица|гагра|пицунд|новый\s+афон|сухум|гегский\s+водопад|33\s+водопад|"
        r"ахштырская|воронцовские|красная\s+поляна|роза\s+хутор|газпром\s+лаур|"
        r"скайпарк|дендрарий|тисо-самшитов|имеретинская|мацеста)\b",
        re.I,
    ),
    # Specific pickup cities (real neighbourhoods)
    re.compile(
        r"\b(лоо|адлер|хоста|кудепста|лазаревск|дагомыс|эсто-садок)\b",
        re.I,
    ),
    # Specific routes with identifiable names
    re.compile(
        r"\b(золотое\s+кольцо|ведьмино\s+ущелье|мамедово\s+ущелье)\b",
        re.I,
    ),
)


TOURISM_GENERIC_MODIFIER_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(
        r"\b(недорого|дёшево|дешево|лучшие|топ|с\s+детьми|для\s+пенсионер|vip|недорогие)\b",
        re.I,
    ),
)
