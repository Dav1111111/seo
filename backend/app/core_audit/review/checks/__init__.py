"""Pure detection layer — each check returns CheckResult with findings+stats.

Layer boundary: checks NEVER emit Russian text or Recommendation rows.
Wording happens in composer.py (layer 2). Orchestration in
run_python_checks.py (layer 3).
"""

from app.core_audit.review.checks.commercial_checks import check_commercial
from app.core_audit.review.checks.density_checks import check_density
from app.core_audit.review.checks.eeat_checks import check_eeat
from app.core_audit.review.checks.h1_checks import check_h1
from app.core_audit.review.checks.h2_completeness import check_h2_completeness
from app.core_audit.review.checks.overoptimization import check_overoptimization
from app.core_audit.review.checks.schema_checks import check_schema
from app.core_audit.review.checks.title_checks import check_title

__all__ = [
    "check_commercial",
    "check_density",
    "check_eeat",
    "check_h1",
    "check_h2_completeness",
    "check_overoptimization",
    "check_schema",
    "check_title",
]
