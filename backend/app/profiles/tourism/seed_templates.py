"""Tourism vertical — seed templates for the Target Demand Map expander.

Data-only. These templates are slot-level patterns that the expander
Cartesian-crosses with a site's `target_config.services`, `geo_primary`,
`geo_secondary`, etc. No LLM, no network, no site state.

Slot vocabulary (lowercase Russian strings as values):
  - {service}  / {activity}    : commercial service or activity noun
                                  ("экскурсии", "туры", "трансфер", ...)
  - {city}                      : source or urban-level geo
  - {destination}               : destination geo
  - {region}                    : regional geo
  - {pickup_city}               : pickup geo (trips from X)
  - {month}                     : Russian month inflected for "в {месяце}"
  - {n}                         : small integer (days)

Dedup note
----------
The expander's `cluster_key = {cluster_type}:{hash(slots)}`. That means two
templates sharing a `cluster_type` AND a `slot_shape` collapse into one
cluster. Multiple templates with the same shape coexist intentionally:
Phase B proposers can pick the best-sounding pattern per cluster.

The library is sized so that a typical 5-service × 4-primary-geo ×
2-secondary-geo config expands to roughly 300-400 clusters after the
dedup + per-template cap (<= MAX_PER_TEMPLATE = 30), well inside the
500-cluster hard cap.

Volume tiers are conservative first-order guesses — Phase A does not call
Wordstat, so xs/s/m/l/xl reflect intuition, not data.
"""

from __future__ import annotations

from app.core_audit.demand_map.dto import (
    ClusterType,
    SeedTemplate,
    VolumeTier,
)
from app.core_audit.intent_codes import IntentCode


# ---------- commercial_core (10 templates) --------------------------------
# Unique slot-shapes within commercial_core: {city}, {destination},
# {activity,city}, {activity,destination}, {activity,region}.
_COMMERCIAL_CORE: tuple[SeedTemplate, ...] = (
    SeedTemplate(
        pattern="экскурсии {city}",
        cluster_type=ClusterType.commercial_core,
        intent_code=IntentCode.COMM_CATEGORY,
        default_volume_tier=VolumeTier.l,
        required_slots=("city",),
    ),
    SeedTemplate(
        pattern="туры {destination}",
        cluster_type=ClusterType.commercial_core,
        intent_code=IntentCode.COMM_CATEGORY,
        default_volume_tier=VolumeTier.l,
        required_slots=("destination",),
    ),
    SeedTemplate(
        pattern="{activity} {city}",
        cluster_type=ClusterType.commercial_core,
        intent_code=IntentCode.COMM_CATEGORY,
        default_volume_tier=VolumeTier.m,
        required_slots=("activity", "city"),
    ),
    SeedTemplate(
        pattern="{activity} туры {region}",
        cluster_type=ClusterType.commercial_core,
        intent_code=IntentCode.COMM_CATEGORY,
        default_volume_tier=VolumeTier.m,
        required_slots=("activity", "region"),
    ),
    SeedTemplate(
        pattern="экскурсии в {destination}",
        cluster_type=ClusterType.commercial_core,
        intent_code=IntentCode.COMM_CATEGORY,
        default_volume_tier=VolumeTier.l,
        required_slots=("destination",),
    ),
    SeedTemplate(
        pattern="{activity} в {destination}",
        cluster_type=ClusterType.commercial_core,
        intent_code=IntentCode.COMM_CATEGORY,
        default_volume_tier=VolumeTier.m,
        required_slots=("activity", "destination"),
    ),
    SeedTemplate(
        pattern="туры в {destination}",
        cluster_type=ClusterType.commercial_core,
        intent_code=IntentCode.COMM_CATEGORY,
        default_volume_tier=VolumeTier.l,
        required_slots=("destination",),
    ),
    SeedTemplate(
        pattern="{activity} из {city}",
        cluster_type=ClusterType.commercial_core,
        intent_code=IntentCode.COMM_CATEGORY,
        default_volume_tier=VolumeTier.m,
        required_slots=("activity", "city"),
    ),
    SeedTemplate(
        pattern="путешествие {destination}",
        cluster_type=ClusterType.commercial_core,
        intent_code=IntentCode.COMM_CATEGORY,
        default_volume_tier=VolumeTier.s,
        required_slots=("destination",),
    ),
    SeedTemplate(
        pattern="поездка {destination}",
        cluster_type=ClusterType.commercial_core,
        intent_code=IntentCode.COMM_CATEGORY,
        default_volume_tier=VolumeTier.s,
        required_slots=("destination",),
    ),
)


# ---------- commercial_modifier (8 templates) -----------------------------
# Unique slot-shapes: {activity,city}, {activity,city,n}.
_COMMERCIAL_MODIFIER: tuple[SeedTemplate, ...] = (
    SeedTemplate(
        pattern="{activity} {city} цена",
        cluster_type=ClusterType.commercial_modifier,
        intent_code=IntentCode.COMM_MODIFIED,
        default_volume_tier=VolumeTier.m,
        required_slots=("activity", "city"),
    ),
    SeedTemplate(
        pattern="{activity} {city} недорого",
        cluster_type=ClusterType.commercial_modifier,
        intent_code=IntentCode.COMM_MODIFIED,
        default_volume_tier=VolumeTier.m,
        required_slots=("activity", "city"),
    ),
    SeedTemplate(
        pattern="{activity} {city} на {n} дней",
        cluster_type=ClusterType.commercial_modifier,
        intent_code=IntentCode.COMM_MODIFIED,
        default_volume_tier=VolumeTier.s,
        required_slots=("activity", "city", "n"),
    ),
    SeedTemplate(
        pattern="{activity} {city} с детьми",
        cluster_type=ClusterType.commercial_modifier,
        intent_code=IntentCode.COMM_MODIFIED,
        default_volume_tier=VolumeTier.s,
        required_slots=("activity", "city"),
    ),
    SeedTemplate(
        pattern="{activity} {city} индивидуальные",
        cluster_type=ClusterType.commercial_modifier,
        intent_code=IntentCode.COMM_MODIFIED,
        default_volume_tier=VolumeTier.s,
        required_slots=("activity", "city"),
    ),
    SeedTemplate(
        pattern="{activity} {city} групповые",
        cluster_type=ClusterType.commercial_modifier,
        intent_code=IntentCode.COMM_MODIFIED,
        default_volume_tier=VolumeTier.s,
        required_slots=("activity", "city"),
    ),
    SeedTemplate(
        pattern="{activity} {city} стоимость",
        cluster_type=ClusterType.commercial_modifier,
        intent_code=IntentCode.COMM_MODIFIED,
        default_volume_tier=VolumeTier.s,
        required_slots=("activity", "city"),
    ),
    SeedTemplate(
        pattern="лучшие {activity} {city}",
        cluster_type=ClusterType.commercial_modifier,
        intent_code=IntentCode.COMM_COMPARE,
        default_volume_tier=VolumeTier.s,
        required_slots=("activity", "city"),
    ),
)


# ---------- local_geo (6 templates) ---------------------------------------
# Unique slot-shapes: {activity,city}, {activity,pickup_city}, {city,destination}.
_LOCAL_GEO: tuple[SeedTemplate, ...] = (
    SeedTemplate(
        pattern="{activity} с выездом из {city}",
        cluster_type=ClusterType.local_geo,
        intent_code=IntentCode.LOCAL_GEO,
        default_volume_tier=VolumeTier.s,
        required_slots=("activity", "city"),
    ),
    SeedTemplate(
        pattern="{activity} из {pickup_city}",
        cluster_type=ClusterType.local_geo,
        intent_code=IntentCode.LOCAL_GEO,
        default_volume_tier=VolumeTier.m,
        required_slots=("activity", "pickup_city"),
    ),
    SeedTemplate(
        pattern="трансфер {city} {destination}",
        cluster_type=ClusterType.local_geo,
        intent_code=IntentCode.LOCAL_GEO,
        default_volume_tier=VolumeTier.s,
        required_slots=("city", "destination"),
    ),
    SeedTemplate(
        pattern="такси {city} {destination}",
        cluster_type=ClusterType.local_geo,
        intent_code=IntentCode.LOCAL_GEO,
        default_volume_tier=VolumeTier.s,
        required_slots=("city", "destination"),
    ),
    SeedTemplate(
        pattern="{activity} недалеко от {city}",
        cluster_type=ClusterType.local_geo,
        intent_code=IntentCode.LOCAL_GEO,
        default_volume_tier=VolumeTier.xs,
        required_slots=("activity", "city"),
    ),
    SeedTemplate(
        pattern="{activity} рядом с {city}",
        cluster_type=ClusterType.local_geo,
        intent_code=IntentCode.LOCAL_GEO,
        default_volume_tier=VolumeTier.xs,
        required_slots=("activity", "city"),
    ),
)


# ---------- informational_dest (6 templates) ------------------------------
# Unique slot-shapes: {destination}, {destination,n}.
_INFORMATIONAL_DEST: tuple[SeedTemplate, ...] = (
    SeedTemplate(
        pattern="что посмотреть в {destination}",
        cluster_type=ClusterType.informational_dest,
        intent_code=IntentCode.INFO_DEST,
        default_volume_tier=VolumeTier.m,
        required_slots=("destination",),
    ),
    SeedTemplate(
        pattern="достопримечательности {destination}",
        cluster_type=ClusterType.informational_dest,
        intent_code=IntentCode.INFO_DEST,
        default_volume_tier=VolumeTier.l,
        required_slots=("destination",),
    ),
    SeedTemplate(
        pattern="{destination} за {n} дней",
        cluster_type=ClusterType.informational_dest,
        intent_code=IntentCode.INFO_DEST,
        default_volume_tier=VolumeTier.s,
        required_slots=("destination", "n"),
    ),
    SeedTemplate(
        pattern="что посетить в {destination}",
        cluster_type=ClusterType.informational_dest,
        intent_code=IntentCode.INFO_DEST,
        default_volume_tier=VolumeTier.s,
        required_slots=("destination",),
    ),
    SeedTemplate(
        pattern="маршрут по {destination}",
        cluster_type=ClusterType.informational_dest,
        intent_code=IntentCode.INFO_DEST,
        default_volume_tier=VolumeTier.s,
        required_slots=("destination",),
    ),
    SeedTemplate(
        pattern="путеводитель {destination}",
        cluster_type=ClusterType.informational_dest,
        intent_code=IntentCode.INFO_DEST,
        default_volume_tier=VolumeTier.s,
        required_slots=("destination",),
    ),
)


# ---------- informational_prep (5 templates) ------------------------------
# Unique slot-shapes: {activity}, {destination}, {destination,month}.
_INFORMATIONAL_PREP: tuple[SeedTemplate, ...] = (
    SeedTemplate(
        pattern="что взять на {activity}",
        cluster_type=ClusterType.informational_prep,
        intent_code=IntentCode.INFO_PREP,
        default_volume_tier=VolumeTier.s,
        required_slots=("activity",),
    ),
    SeedTemplate(
        pattern="когда ехать в {destination}",
        cluster_type=ClusterType.informational_prep,
        intent_code=IntentCode.INFO_PREP,
        default_volume_tier=VolumeTier.s,
        required_slots=("destination",),
    ),
    SeedTemplate(
        pattern="погода {destination} {month}",
        cluster_type=ClusterType.informational_prep,
        intent_code=IntentCode.INFO_PREP,
        default_volume_tier=VolumeTier.s,
        required_slots=("destination", "month"),
    ),
    SeedTemplate(
        pattern="что надеть на {activity}",
        cluster_type=ClusterType.informational_prep,
        intent_code=IntentCode.INFO_PREP,
        default_volume_tier=VolumeTier.xs,
        required_slots=("activity",),
    ),
    SeedTemplate(
        pattern="стоимость поездки в {destination}",
        cluster_type=ClusterType.informational_prep,
        intent_code=IntentCode.INFO_PREP,
        default_volume_tier=VolumeTier.s,
        required_slots=("destination",),
    ),
)


# ---------- informational_logistics (4 templates) -------------------------
# Mapped to the `informational_prep` cluster_type with LOGISTICS intent.
# Unique slot-shapes within this group: {destination}, {destination,city}.
_INFORMATIONAL_LOGISTICS: tuple[SeedTemplate, ...] = (
    SeedTemplate(
        pattern="как добраться до {destination}",
        cluster_type=ClusterType.informational_prep,
        intent_code=IntentCode.INFO_LOGISTICS,
        default_volume_tier=VolumeTier.m,
        required_slots=("destination",),
    ),
    SeedTemplate(
        pattern="{destination} из {city} расстояние",
        cluster_type=ClusterType.informational_prep,
        intent_code=IntentCode.INFO_LOGISTICS,
        default_volume_tier=VolumeTier.s,
        required_slots=("destination", "city"),
    ),
    SeedTemplate(
        pattern="как доехать до {destination}",
        cluster_type=ClusterType.informational_prep,
        intent_code=IntentCode.INFO_LOGISTICS,
        default_volume_tier=VolumeTier.s,
        required_slots=("destination",),
    ),
    SeedTemplate(
        pattern="{destination} из {city} как добраться",
        cluster_type=ClusterType.informational_prep,
        intent_code=IntentCode.INFO_LOGISTICS,
        default_volume_tier=VolumeTier.s,
        required_slots=("destination", "city"),
    ),
)


# ---------- transactional_book (4 templates) ------------------------------
# Unique slot-shapes: {activity,city}.
_TRANSACTIONAL_BOOK: tuple[SeedTemplate, ...] = (
    SeedTemplate(
        pattern="забронировать {activity} {city}",
        cluster_type=ClusterType.transactional_book,
        intent_code=IntentCode.TRANS_BOOK,
        default_volume_tier=VolumeTier.m,
        required_slots=("activity", "city"),
    ),
    SeedTemplate(
        pattern="бронь {activity} {city}",
        cluster_type=ClusterType.transactional_book,
        intent_code=IntentCode.TRANS_BOOK,
        default_volume_tier=VolumeTier.s,
        required_slots=("activity", "city"),
    ),
    SeedTemplate(
        pattern="купить {activity} {city}",
        cluster_type=ClusterType.transactional_book,
        intent_code=IntentCode.TRANS_BOOK,
        default_volume_tier=VolumeTier.s,
        required_slots=("activity", "city"),
    ),
    SeedTemplate(
        pattern="заказать {activity} {city}",
        cluster_type=ClusterType.transactional_book,
        intent_code=IntentCode.TRANS_BOOK,
        default_volume_tier=VolumeTier.s,
        required_slots=("activity", "city"),
    ),
)


# ---------- trust (4 templates) -------------------------------------------
# Unique slot-shapes: {activity,city}.
_TRUST: tuple[SeedTemplate, ...] = (
    SeedTemplate(
        pattern="{activity} {city} отзывы",
        cluster_type=ClusterType.trust,
        intent_code=IntentCode.TRUST_LEGAL,
        default_volume_tier=VolumeTier.m,
        required_slots=("activity", "city"),
    ),
    SeedTemplate(
        pattern="{activity} {city} рейтинг",
        cluster_type=ClusterType.trust,
        intent_code=IntentCode.TRUST_LEGAL,
        default_volume_tier=VolumeTier.s,
        required_slots=("activity", "city"),
    ),
    SeedTemplate(
        pattern="отзывы о {activity} в {city}",
        cluster_type=ClusterType.trust,
        intent_code=IntentCode.TRUST_LEGAL,
        default_volume_tier=VolumeTier.s,
        required_slots=("activity", "city"),
    ),
    SeedTemplate(
        pattern="{activity} {city} проверенные",
        cluster_type=ClusterType.trust,
        intent_code=IntentCode.TRUST_LEGAL,
        default_volume_tier=VolumeTier.xs,
        required_slots=("activity", "city"),
    ),
)


# ---------- seasonality (5 templates) -------------------------------------
# Unique slot-shapes: {destination}, {destination,month}.
_SUMMER = (6, 7, 8)
_WINTER = (12, 1, 2)
_SHOULDER = (4, 5, 9, 10)
_SEASONALITY: tuple[SeedTemplate, ...] = (
    SeedTemplate(
        pattern="{destination} летом",
        cluster_type=ClusterType.seasonality,
        intent_code=IntentCode.INFO_DEST,
        default_volume_tier=VolumeTier.m,
        required_slots=("destination",),
        seasonal_months=_SUMMER,
    ),
    SeedTemplate(
        pattern="{destination} зимой",
        cluster_type=ClusterType.seasonality,
        intent_code=IntentCode.INFO_DEST,
        default_volume_tier=VolumeTier.s,
        required_slots=("destination",),
        seasonal_months=_WINTER,
    ),
    SeedTemplate(
        pattern="{destination} весной",
        cluster_type=ClusterType.seasonality,
        intent_code=IntentCode.INFO_DEST,
        default_volume_tier=VolumeTier.s,
        required_slots=("destination",),
        seasonal_months=_SHOULDER,
    ),
    SeedTemplate(
        pattern="{destination} осенью",
        cluster_type=ClusterType.seasonality,
        intent_code=IntentCode.INFO_DEST,
        default_volume_tier=VolumeTier.s,
        required_slots=("destination",),
        seasonal_months=_SHOULDER,
    ),
    SeedTemplate(
        pattern="{destination} в {month}",
        cluster_type=ClusterType.seasonality,
        intent_code=IntentCode.INFO_DEST,
        default_volume_tier=VolumeTier.s,
        required_slots=("destination", "month"),
    ),
)


# ---------- activity (10 templates) ---------------------------------------
# Unique slot-shapes: {region}, {city}.
_ACTIVITY: tuple[SeedTemplate, ...] = (
    SeedTemplate(
        pattern="багги тур {region}",
        cluster_type=ClusterType.activity,
        intent_code=IntentCode.COMM_CATEGORY,
        default_volume_tier=VolumeTier.s,
        required_slots=("region",),
    ),
    SeedTemplate(
        pattern="джип тур {region}",
        cluster_type=ClusterType.activity,
        intent_code=IntentCode.COMM_CATEGORY,
        default_volume_tier=VolumeTier.s,
        required_slots=("region",),
    ),
    SeedTemplate(
        pattern="яхта аренда {city}",
        cluster_type=ClusterType.activity,
        intent_code=IntentCode.COMM_CATEGORY,
        default_volume_tier=VolumeTier.s,
        required_slots=("city",),
    ),
    SeedTemplate(
        pattern="вертолётная экскурсия {region}",
        cluster_type=ClusterType.activity,
        intent_code=IntentCode.COMM_CATEGORY,
        default_volume_tier=VolumeTier.xs,
        required_slots=("region",),
    ),
    SeedTemplate(
        pattern="квадроциклы {region}",
        cluster_type=ClusterType.activity,
        intent_code=IntentCode.COMM_CATEGORY,
        default_volume_tier=VolumeTier.s,
        required_slots=("region",),
    ),
    SeedTemplate(
        pattern="рафтинг {region}",
        cluster_type=ClusterType.activity,
        intent_code=IntentCode.COMM_CATEGORY,
        default_volume_tier=VolumeTier.s,
        required_slots=("region",),
    ),
    SeedTemplate(
        pattern="конные прогулки {city}",
        cluster_type=ClusterType.activity,
        intent_code=IntentCode.COMM_CATEGORY,
        default_volume_tier=VolumeTier.s,
        required_slots=("city",),
    ),
    SeedTemplate(
        pattern="морская прогулка {city}",
        cluster_type=ClusterType.activity,
        intent_code=IntentCode.COMM_CATEGORY,
        default_volume_tier=VolumeTier.m,
        required_slots=("city",),
    ),
    SeedTemplate(
        pattern="дайвинг {city}",
        cluster_type=ClusterType.activity,
        intent_code=IntentCode.COMM_CATEGORY,
        default_volume_tier=VolumeTier.s,
        required_slots=("city",),
    ),
    SeedTemplate(
        pattern="сплав {region}",
        cluster_type=ClusterType.activity,
        intent_code=IntentCode.COMM_CATEGORY,
        default_volume_tier=VolumeTier.s,
        required_slots=("region",),
    ),
)


TOURISM_SEED_TEMPLATES: tuple[SeedTemplate, ...] = (
    *_COMMERCIAL_CORE,
    *_COMMERCIAL_MODIFIER,
    *_LOCAL_GEO,
    *_INFORMATIONAL_DEST,
    *_INFORMATIONAL_PREP,
    *_INFORMATIONAL_LOGISTICS,
    *_TRANSACTIONAL_BOOK,
    *_TRUST,
    *_SEASONALITY,
    *_ACTIVITY,
)

# Sanity — keep roughly 60-75 templates.
assert 60 <= len(TOURISM_SEED_TEMPLATES) <= 75, (
    f"unexpected template count: {len(TOURISM_SEED_TEMPLATES)}"
)
