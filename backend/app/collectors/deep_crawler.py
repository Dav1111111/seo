"""Playwright-based deep extractor — own pages and competitor URLs.

The classic site_crawler reads raw HTML over urllib + regex, which
misses everything rendered after JavaScript. Tilda landings, React
SPAs, and any modern booking widget are invisible to it. This module
spins up a headless Chromium, waits for the page to settle, then
pulls a much richer dictionary of facts:

  - Title / H1 / meta description (after JS)
  - Full visible text (rendered DOM, not source)
  - Ordered headings tree (H1-H4)
  - CTA inventory: every <button> + interactive link with computed
    color, font-size, position relative to the fold
  - Forms inventory: how many fields, what types
  - Image inventory with alt + dimensions + lazy-loading flag
  - Internal/external links with anchor text
  - CSS color palette (top hex codes by usage)
  - Fonts used
  - Layout meta: viewport, fold, page height, sticky header/CTA
  - Web vitals (LCP, FCP, CLS) from PerformanceObserver
  - JS console errors observed during render
  - Schema.org JSON-LD blocks
  - Screenshots: desktop 1280x800 + mobile 375x800 (above-fold)

Universal: works for both own pages (`is_competitor=False`) and
competitor URLs (`is_competitor=True`). The extractor itself doesn't
care; the caller sets the flag and writes to page_deep_extracts.

Anti-SSRF: every URL is validated through assert_public_url before
the page.goto call, including a manual recheck after redirect.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from app.security.network import SSRFBlocked, assert_public_url

log = logging.getLogger(__name__)


# Where to put screenshots inside the container. The Dockerfile creates
# this dir; in compose it can be bind-mounted to survive rebuilds.
SCREENSHOT_DIR = os.environ.get("DEEP_EXTRACT_SCREENSHOT_DIR", "/app/screenshots")

# Hard ceiling on full_text we save — beyond ~50KB the LLM context
# explodes and the marginal information is near zero.
FULL_TEXT_CAP = 50_000

# Fold position we treat as "above the fold" for CTA classification.
# 800px matches our mobile screenshot height; same threshold for
# desktop is conservative (below it on a 1080p screen but consistent).
FOLD_PX = 800

# Hard timeout per URL. Past this we kill the page and return what
# we have. 25s is generous for slow Tilda landings + our own server.
PAGE_TIMEOUT_MS = 25_000


@dataclass
class DeepExtractResult:
    url: str
    status: str  # completed | failed | timeout
    error: str | None = None
    duration_ms: int | None = None
    title: str | None = None
    h1: str | None = None
    meta_description: str | None = None
    full_text: str | None = None
    headings_tree: list[dict[str, Any]] = field(default_factory=list)
    cta_inventory: list[dict[str, Any]] = field(default_factory=list)
    forms_inventory: list[dict[str, Any]] = field(default_factory=list)
    links_inventory: list[dict[str, Any]] = field(default_factory=list)
    images_inventory: list[dict[str, Any]] = field(default_factory=list)
    css_palette: list[dict[str, Any]] = field(default_factory=list)
    fonts: list[dict[str, Any]] = field(default_factory=list)
    layout_meta: dict[str, Any] = field(default_factory=dict)
    performance: dict[str, Any] = field(default_factory=dict)
    js_errors: list[dict[str, Any]] = field(default_factory=list)
    schema_blocks: list[dict[str, Any]] = field(default_factory=list)
    screenshot_desktop_path: str | None = None
    screenshot_mobile_path: str | None = None


# JS that runs INSIDE the page to harvest the rich facts. Returning
# everything in one round-trip keeps the extractor simple — Playwright
# serializes the dict back to Python.
_EXTRACTION_SCRIPT = r"""
() => {
  const FOLD = 800;

  // ── Headings ───────────────────────────────────────────────────
  const headings = [];
  document.querySelectorAll('h1, h2, h3, h4').forEach((h) => {
    const text = (h.innerText || '').trim().slice(0, 200);
    if (text) {
      headings.push({ level: parseInt(h.tagName.slice(1)), text });
    }
  });

  // ── CTA inventory ─────────────────────────────────────────────
  const ctas = [];
  const ctaSelectors = 'button, a.btn, a.button, [role="button"], input[type="submit"]';
  document.querySelectorAll(ctaSelectors).forEach((el) => {
    const text = (el.innerText || el.value || '').trim();
    if (!text || text.length > 80) return;  // skip empty / huge
    const rect = el.getBoundingClientRect();
    if (rect.width < 10 || rect.height < 10) return;  // hidden
    const cs = window.getComputedStyle(el);
    ctas.push({
      text,
      tag: el.tagName.toLowerCase(),
      href: el.href || null,
      bg_color: cs.backgroundColor,
      color: cs.color,
      font_size: cs.fontSize,
      font_weight: cs.fontWeight,
      width: Math.round(rect.width),
      height: Math.round(rect.height),
      top: Math.round(rect.top + window.scrollY),
      above_fold: (rect.top + window.scrollY) < FOLD,
    });
  });

  // ── Forms inventory ───────────────────────────────────────────
  const forms = [];
  document.querySelectorAll('form').forEach((f) => {
    const inputs = Array.from(f.querySelectorAll('input, textarea, select'))
      .filter((i) => i.type !== 'hidden')
      .map((i) => ({
        type: i.type || i.tagName.toLowerCase(),
        name: i.name || null,
        placeholder: i.placeholder || null,
        required: i.required || false,
      }));
    const rect = f.getBoundingClientRect();
    forms.push({
      action: f.action || null,
      method: (f.method || 'get').toLowerCase(),
      field_count: inputs.length,
      fields: inputs,
      top: Math.round(rect.top + window.scrollY),
      above_fold: (rect.top + window.scrollY) < FOLD,
    });
  });

  // ── Links inventory ───────────────────────────────────────────
  const links = [];
  const ownHost = location.hostname.replace(/^www\./, '');
  document.querySelectorAll('a[href]').forEach((a) => {
    const href = a.href;
    if (!href || href.startsWith('javascript:') || href.startsWith('mailto:')) return;
    const anchor = (a.innerText || '').trim().slice(0, 120);
    let internal = false;
    try {
      const u = new URL(href, location.href);
      internal = u.hostname.replace(/^www\./, '') === ownHost;
    } catch {}
    links.push({ href, anchor, internal });
  });

  // ── Images inventory ─────────────────────────────────────────
  const images = [];
  document.querySelectorAll('img').forEach((img) => {
    images.push({
      src: img.src,
      alt: img.alt || null,
      width: img.naturalWidth || img.width,
      height: img.naturalHeight || img.height,
      lazy: img.loading === 'lazy',
    });
  });

  // ── CSS palette + fonts ──────────────────────────────────────
  const colorCount = {};
  const fontFamilies = {};
  const fontSizes = {};
  const els = document.querySelectorAll('body, body *');
  let scanned = 0;
  els.forEach((el) => {
    if (scanned > 800) return;  // sample cap
    scanned += 1;
    const cs = window.getComputedStyle(el);
    [cs.color, cs.backgroundColor].forEach((c) => {
      if (!c || c === 'rgba(0, 0, 0, 0)' || c === 'transparent') return;
      colorCount[c] = (colorCount[c] || 0) + 1;
    });
    const ff = (cs.fontFamily || '').split(',')[0].trim().replace(/['"]/g, '');
    if (ff) fontFamilies[ff] = (fontFamilies[ff] || 0) + 1;
    fontSizes[cs.fontSize] = (fontSizes[cs.fontSize] || 0) + 1;
  });
  const palette = Object.entries(colorCount)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10)
    .map(([color, count]) => ({ color, count }));
  const fontsList = Object.entries(fontFamilies)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8)
    .map(([family, count]) => ({ family, count }));

  // ── Layout meta ──────────────────────────────────────────────
  const stickyHeader = Array.from(
    document.querySelectorAll('header, [role="banner"], .header, .navbar')
  ).some((el) => window.getComputedStyle(el).position === 'sticky' ||
                 window.getComputedStyle(el).position === 'fixed');
  const stickyCta = Array.from(document.querySelectorAll(ctaSelectors))
    .some((el) => {
      const cs = window.getComputedStyle(el);
      return cs.position === 'sticky' || cs.position === 'fixed';
    });

  // ── Schema.org JSON-LD ───────────────────────────────────────
  const schemas = [];
  document.querySelectorAll('script[type="application/ld+json"]').forEach((s) => {
    try {
      const parsed = JSON.parse(s.textContent || '{}');
      schemas.push(parsed);
    } catch (e) {
      schemas.push({ __parse_error: String(e), __raw: (s.textContent || '').slice(0, 300) });
    }
  });

  // ── Performance — pull from PerformanceObserver buffer ───────
  // (we set up the observer in init script; LCP/CLS available here)
  const perf = window.__deepPerf || {};

  return {
    title: document.title || '',
    h1: (document.querySelector('h1')?.innerText || '').trim(),
    meta_description: document.querySelector('meta[name="description"]')?.content || null,
    full_text: document.body.innerText.slice(0, 50000),
    headings_tree: headings,
    cta_inventory: ctas,
    forms_inventory: forms,
    links_inventory: links,
    images_inventory: images,
    css_palette: palette,
    fonts: fontsList,
    font_sizes: Object.entries(fontSizes).slice(0, 12),
    layout_meta: {
      viewport_w: window.innerWidth,
      viewport_h: window.innerHeight,
      doc_height: document.documentElement.scrollHeight,
      sticky_header: stickyHeader,
      sticky_cta: stickyCta,
    },
    performance: perf,
    schema_blocks: schemas,
  };
}
"""


_PERFORMANCE_INIT_SCRIPT = r"""
window.__deepPerf = { lcp: null, cls: 0, fcp: null };
try {
  new PerformanceObserver((list) => {
    const entries = list.getEntries();
    const last = entries[entries.length - 1];
    if (last) window.__deepPerf.lcp = last.renderTime || last.loadTime;
  }).observe({ type: 'largest-contentful-paint', buffered: true });
} catch (e) {}
try {
  new PerformanceObserver((list) => {
    list.getEntries().forEach((e) => {
      if (!e.hadRecentInput) window.__deepPerf.cls += e.value;
    });
  }).observe({ type: 'layout-shift', buffered: true });
} catch (e) {}
try {
  new PerformanceObserver((list) => {
    list.getEntries().forEach((e) => {
      if (e.name === 'first-contentful-paint') window.__deepPerf.fcp = e.startTime;
    });
  }).observe({ type: 'paint', buffered: true });
} catch (e) {}
"""


def _safe_filename(url: str, suffix: str) -> str:
    """Stable, filesystem-safe screenshot path per URL+suffix."""
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return os.path.join(SCREENSHOT_DIR, f"{digest}_{suffix}.png")


async def _capture_screenshot(
    page,
    out_path: str,
    width: int,
    height: int,
    timeout_ms: int = 8_000,
) -> bool:
    """Try Playwright screenshot, fall back to raw CDP on timeout/instability.

    Returns True if a file was written, False otherwise. Doesn't raise.

    Why two paths: Playwright's `screenshot()` waits for fonts + caret +
    animations to settle. On pages with React hydration errors / infinite
    re-render loops (common on broken Next.js sites) that wait never
    resolves and the call times out. Raw CDP `Page.captureScreenshot`
    just snaps the current frame buffer — no stability checks. Ugly
    fallback but always produces SOMETHING for the UI.
    """
    import base64

    try:
        await page.screenshot(
            path=out_path,
            clip={"x": 0, "y": 0, "width": width, "height": height},
            type="png",
            timeout=timeout_ms,
            animations="disabled",
            caret="hide",
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log.info("deep_extract.screenshot_pw_failed_falling_back_to_cdp err=%s", exc)

    try:
        session = await page.context.new_cdp_session(page)
        result = await session.send(
            "Page.captureScreenshot",
            {
                "format": "png",
                "clip": {
                    "x": 0,
                    "y": 0,
                    "width": width,
                    "height": height,
                    "scale": 1,
                },
                "captureBeyondViewport": False,
                "fromSurface": True,
            },
        )
        data = result.get("data") if isinstance(result, dict) else None
        if not data:
            return False
        with open(out_path, "wb") as fh:
            fh.write(base64.b64decode(data))
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("deep_extract.screenshot_cdp_failed err=%s", exc)
        return False


def _competitor_domain_of(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except Exception:  # noqa: BLE001
        return ""


async def deep_extract(url: str) -> DeepExtractResult:
    """Run Playwright on URL, return rich extraction.

    Idempotent — the caller decides whether to write to DB. Errors are
    captured into the result.status / error fields rather than raised
    so the calling task can surface a partial row.
    """
    started_at = time.monotonic()
    result = DeepExtractResult(url=url, status="completed")

    # SSRF check before any I/O.
    try:
        assert_public_url(url)
    except SSRFBlocked as exc:
        result.status = "failed"
        result.error = f"ssrf_blocked: {exc}"
        result.duration_ms = int((time.monotonic() - started_at) * 1000)
        return result

    # Lazy-import Playwright so the module imports even when the
    # binary isn't installed (e.g. unit tests without Chromium).
    try:
        from playwright.async_api import async_playwright, Error as PWError
    except ImportError:
        result.status = "failed"
        result.error = "playwright_not_installed"
        result.duration_ms = int((time.monotonic() - started_at) * 1000)
        return result

    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        try:
            # Desktop pass — main extraction + desktop screenshot.
            ctx = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36 YandexGrowthTower-DeepBot/1.0"
                ),
                ignore_https_errors=True,
            )
            await ctx.add_init_script(_PERFORMANCE_INIT_SCRIPT)
            page = await ctx.new_page()

            console_errors: list[dict[str, Any]] = []
            page.on("pageerror", lambda exc: console_errors.append({
                "kind": "pageerror",
                "message": str(exc)[:500],
            }))
            page.on("console", lambda msg: (
                console_errors.append({
                    "kind": msg.type,
                    "message": (msg.text or "")[:500],
                }) if msg.type == "error" else None
            ))

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
                # Re-check final URL (after redirects) is still public.
                final_url = page.url
                if final_url != url:
                    try:
                        assert_public_url(final_url)
                    except SSRFBlocked as exc:
                        result.status = "failed"
                        result.error = f"ssrf_redirect_blocked: {exc}"
                        await browser.close()
                        result.duration_ms = int((time.monotonic() - started_at) * 1000)
                        return result
                # Give JS a moment to finish booting (sliders, lazy widgets).
                try:
                    await page.wait_for_load_state("networkidle", timeout=8_000)
                except Exception:  # noqa: BLE001
                    pass  # not critical — we have domcontentloaded

                data = await page.evaluate(_EXTRACTION_SCRIPT)

                # Desktop screenshot — above-fold (viewport).
                # We try Playwright's `screenshot()` first (with animations
                # disabled). If that times out — typical for pages with
                # React hydration errors / infinite re-render loops — fall
                # back to raw CDP `Page.captureScreenshot`, which doesn't
                # wait for "stability" and just snaps the current frame.
                # If even CDP fails, we still have all the DOM data; just
                # no thumbnail.
                screenshot_desktop = _safe_filename(url, "desktop")
                if await _capture_screenshot(
                    page, screenshot_desktop, 1280, 800, timeout_ms=8_000,
                ):
                    result.screenshot_desktop_path = screenshot_desktop
                else:
                    log.warning("deep_extract.screenshot_desktop_failed url=%s", url)
            except PWError as exc:
                msg = str(exc).lower()
                if "timeout" in msg:
                    result.status = "timeout"
                else:
                    result.status = "failed"
                result.error = str(exc)[:500]
                await browser.close()
                result.duration_ms = int((time.monotonic() - started_at) * 1000)
                return result

            # Mobile pass — only above-fold screenshot. Cheaper than full
            # mobile extraction (we already have all the DOM facts).
            mobile_ctx = await browser.new_context(
                viewport={"width": 375, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                    "Mobile/15E148 Safari/604.1"
                ),
                ignore_https_errors=True,
            )
            mobile_page = await mobile_ctx.new_page()
            try:
                await mobile_page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
                screenshot_mobile = _safe_filename(url, "mobile")
                if await _capture_screenshot(
                    mobile_page, screenshot_mobile, 375, 800, timeout_ms=8_000,
                ):
                    result.screenshot_mobile_path = screenshot_mobile
                else:
                    log.warning("deep_extract.screenshot_mobile_failed url=%s", url)
            except Exception as exc:  # noqa: BLE001
                log.warning("deep_extract.screenshot_mobile_failed url=%s err=%s", url, exc)
            finally:
                await mobile_ctx.close()

            # Pack data into result.
            result.title = (data.get("title") or "").strip()[:500]
            result.h1 = (data.get("h1") or "").strip()[:500]
            result.meta_description = (data.get("meta_description") or "")
            result.full_text = (data.get("full_text") or "")[:FULL_TEXT_CAP]
            result.headings_tree = data.get("headings_tree") or []
            result.cta_inventory = data.get("cta_inventory") or []
            result.forms_inventory = data.get("forms_inventory") or []
            result.links_inventory = data.get("links_inventory") or []
            result.images_inventory = data.get("images_inventory") or []
            result.css_palette = data.get("css_palette") or []
            result.fonts = data.get("fonts") or []
            result.layout_meta = data.get("layout_meta") or {}
            result.performance = data.get("performance") or {}
            result.schema_blocks = data.get("schema_blocks") or []
            result.js_errors = console_errors[:50]

        finally:
            await browser.close()

    result.duration_ms = int((time.monotonic() - started_at) * 1000)
    return result


def deep_extract_sync(url: str) -> DeepExtractResult:
    """Sync wrapper for Celery — Celery tasks are sync by nature."""
    return asyncio.run(deep_extract(url))


__all__ = [
    "DeepExtractResult",
    "deep_extract",
    "deep_extract_sync",
    "FULL_TEXT_CAP",
    "FOLD_PX",
]
