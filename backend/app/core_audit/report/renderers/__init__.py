"""Report renderers — one WeeklyReport → one serialized output per format."""

from app.core_audit.report.renderers.markdown import render_markdown

__all__ = ["render_markdown"]
