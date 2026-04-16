"""Report section builders — each returns a DTO given DB session + site.

Data-driven sections only. Executive + action plan (LLM prose) live one
level up in `builder.py` because they depend on other sections.
"""

from app.core_audit.report.sections.coverage import build_coverage
from app.core_audit.report.sections.page_findings import build_page_findings
from app.core_audit.report.sections.query_trends import build_query_trends
from app.core_audit.report.sections.technical import build_technical

__all__ = [
    "build_coverage",
    "build_page_findings",
    "build_query_trends",
    "build_technical",
]
