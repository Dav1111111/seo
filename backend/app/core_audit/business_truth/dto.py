"""BusinessTruth — единая правда о бизнесе, собранная из трёх источников.

Три источника, которые платформа знает о сайте:
  1. Understanding  — что владелец сказал в онбординге (словами про себя)
  2. Content        — что реально лежит на страницах сайта (crawl)
  3. Traffic        — кто в итоге приходит на сайт через Яндекс (Webmaster)

Каждое «направление бизнеса» — пара (service, geo). Примеры:
  • (багги, Абхазия)
  • (экскурсии, Сочи)
  • (трансфер, Красная Поляна)

Для каждого направления мы считаем "силу" в каждом источнике 0..1 и
отмечаем расхождения — именно они становятся контентом для диагностики:
«сайт покрывает Сочи, но трафика туда нет — почему?».

Этот модуль — **чистый**: никаких I/O, LLM, HTTP. Дата-классы и помощники
композиции. I/O делают `reader_*` модули, оркестрация — `task.py`.
"""

from __future__ import annotations

import dataclasses
from typing import Iterable


# Источники свидетельств о направлении
SOURCE_UNDERSTANDING = "understanding"
SOURCE_CONTENT = "content"
SOURCE_TRAFFIC = "traffic"
ALL_SOURCES = (SOURCE_UNDERSTANDING, SOURCE_CONTENT, SOURCE_TRAFFIC)


@dataclasses.dataclass(frozen=True)
class DirectionKey:
    """Нормализованная пара service × geo. Равенство — по токенам (не по регистру)."""
    service: str           # "багги" | "экскурсии" | "трансфер"
    geo: str               # "абхазия" | "сочи" | "крым"

    @classmethod
    def of(cls, service: str, geo: str) -> "DirectionKey":
        return cls(
            service=(service or "").strip().lower(),
            geo=(geo or "").strip().lower(),
        )

    def label_ru(self) -> str:
        return f"{self.service} · {self.geo}"


@dataclasses.dataclass
class DirectionEvidence:
    """Одно направление × три источника + диагностика расхождений.

    strength_* ∈ [0, 1] — доля этого направления в источнике:
      • understanding: доля из весов владельца (например 0.5 для "50% Абхазия")
      • content: доля страниц сайта, относящихся к этому направлению
      • traffic: доля показов/кликов, пришедших по этому направлению

    pages — URL-ы, которые классифицированы в это направление.
    queries — запросы из Вебмастера, классифицированные в это направление.
    """
    key: DirectionKey

    strength_understanding: float = 0.0
    strength_content: float = 0.0
    strength_traffic: float = 0.0

    pages: tuple[str, ...] = ()
    queries: tuple[str, ...] = ()

    @property
    def mentioned_in(self) -> set[str]:
        """Какие источники вообще знают про это направление (non-zero)."""
        out: set[str] = set()
        if self.strength_understanding > 0:
            out.add(SOURCE_UNDERSTANDING)
        if self.strength_content > 0:
            out.add(SOURCE_CONTENT)
        if self.strength_traffic > 0:
            out.add(SOURCE_TRAFFIC)
        return out

    @property
    def is_confirmed(self) -> bool:
        """Направление подтверждено хотя бы двумя источниками."""
        return len(self.mentioned_in) >= 2

    @property
    def is_blind_spot(self) -> bool:
        """Владелец + контент есть, трафика нет → слепое пятно SEO."""
        return (
            self.strength_understanding > 0
            and self.strength_content > 0
            and self.strength_traffic == 0
        )

    @property
    def is_content_only(self) -> bool:
        """Страница есть, но ни владелец ни трафик не подтверждают — шум."""
        return (
            self.strength_content > 0
            and self.strength_understanding == 0
            and self.strength_traffic == 0
        )

    @property
    def is_traffic_only(self) -> bool:
        """Трафик идёт, но нет страницы — незакрытый спрос, нужна посадочная."""
        return (
            self.strength_traffic > 0
            and self.strength_content == 0
        )

    def divergence_ru(self) -> str | None:
        """Текстовое объяснение расхождения (или None, если всё ок)."""
        if self.is_blind_spot:
            return (
                f"У тебя есть страница про «{self.key.label_ru()}», но трафика "
                f"из Яндекса по ней нет. Скорее всего — страница не ранжируется: "
                f"надо усилить заголовки, цену, отзывы, внутренние ссылки."
            )
        if self.is_content_only:
            return (
                f"Страница про «{self.key.label_ru()}» есть, но ты в онбординге "
                f"это направление не указал и трафика по нему тоже нет. "
                f"Уточни в настройках: это направление актуально или страница устарела?"
            )
        if self.is_traffic_only:
            return (
                f"Трафик по «{self.key.label_ru()}» идёт, но отдельной страницы "
                f"нет — люди попадают на неспециализированные страницы и уходят. "
                f"Создай посадочную под это направление."
            )
        # Владелец говорит, но ни контента ни трафика — амбиция без исполнения
        if (
            self.strength_understanding > 0
            and self.strength_content == 0
            and self.strength_traffic == 0
        ):
            return (
                f"В онбординге ты указал «{self.key.label_ru()}», но на сайте "
                f"такой страницы нет и трафика тоже. Либо создай страницу под "
                f"это направление, либо убери его из описания бизнеса."
            )
        return None


@dataclasses.dataclass
class BusinessTruth:
    """Итоговая правда: список направлений с evidence из 3 источников.

    Порядок directions — по сумме strength_* по трём источникам, от самого
    сильного к самому слабому. Это и будет порядком приоритета при
    распределении discovery-бюджета.
    """
    directions: list[DirectionEvidence]

    # Для диагностики: сколько источников реально использовано. Если
    # crawled_pages=0, то content картина фиктивная.
    sources_used: dict[str, int] = dataclasses.field(default_factory=dict)

    # Когда построено
    built_at_iso: str = ""

    def confirmed(self) -> list[DirectionEvidence]:
        """Направления, подтверждённые хотя бы двумя источниками."""
        return [d for d in self.directions if d.is_confirmed]

    def blind_spots(self) -> list[DirectionEvidence]:
        return [d for d in self.directions if d.is_blind_spot]

    def traffic_only(self) -> list[DirectionEvidence]:
        return [d for d in self.directions if d.is_traffic_only]

    def divergences(self) -> list[tuple[DirectionEvidence, str]]:
        """Все направления с не-None divergence + сам текст."""
        out: list[tuple[DirectionEvidence, str]] = []
        for d in self.directions:
            msg = d.divergence_ru()
            if msg is not None:
                out.append((d, msg))
        return out

    def to_jsonb(self) -> dict:
        """Сериализация для хранения в sites.target_config.business_truth."""
        return {
            "directions": [
                {
                    "service": d.key.service,
                    "geo": d.key.geo,
                    "strength_understanding": round(d.strength_understanding, 3),
                    "strength_content": round(d.strength_content, 3),
                    "strength_traffic": round(d.strength_traffic, 3),
                    "pages": list(d.pages),
                    "queries_sample": list(d.queries[:10]),
                    "mentioned_in": sorted(d.mentioned_in),
                    "is_confirmed": d.is_confirmed,
                    "is_blind_spot": d.is_blind_spot,
                    "is_content_only": d.is_content_only,
                    "is_traffic_only": d.is_traffic_only,
                    "divergence_ru": d.divergence_ru(),
                }
                for d in self.directions
            ],
            "sources_used": dict(self.sources_used),
            "built_at": self.built_at_iso,
        }


__all__ = [
    "DirectionKey",
    "DirectionEvidence",
    "BusinessTruth",
    "SOURCE_UNDERSTANDING", "SOURCE_CONTENT", "SOURCE_TRAFFIC", "ALL_SOURCES",
]
