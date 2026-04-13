from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

_SPA_DOMAINS: set[str] = {
    "bancointer.com.br",
    "inter.co",
    "bradesco.com.br",
    "nubank.com.br",
}


def is_spa_domain(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(d in host for d in _SPA_DOMAINS)


@dataclass
class SpaCaptureResult:
    screenshot_ok: bool
    page_title: str | None
    visible_claims: list[str]
    raw_html: str
    raw_text: str
    error: str | None


def capture_spa_with_playwright(
    *,
    url: str,
    screenshot_path: Path,
    timeout_seconds: int = 20,
) -> SpaCaptureResult:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return SpaCaptureResult(
            screenshot_ok=False,
            page_title=None,
            visible_claims=[],
            raw_html="",
            raw_text="",
            error="playwright_not_installed",
        )

    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    timeout_ms = max(8, timeout_seconds) * 1000

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                locale="pt-BR",
                user_agent=USER_AGENT,
                viewport={"width": 1366, "height": 900},
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(4000)

            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass

            page_title = page.title() or None

            try:
                raw_html = page.content()
            except Exception:
                raw_html = ""

            try:
                raw_text = page.inner_text("body", timeout=5000)
            except Exception:
                raw_text = ""

            visible_claims: list[str] = []
            try:
                for sel in ["h1", "h2", "h3", "main", "[role='main']"]:
                    els = page.locator(sel).all()
                    for el in els[:6]:
                        txt = el.inner_text(timeout=2000)
                        clean = re.sub(r"\s+", " ", txt).strip()
                        if len(clean) >= 10 and clean not in visible_claims:
                            visible_claims.append(clean[:300])
                        if len(visible_claims) >= 12:
                            break
                    if len(visible_claims) >= 12:
                        break
            except Exception:
                pass

            try:
                page.screenshot(
                    path=str(screenshot_path), full_page=False, timeout=timeout_ms,
                )
            except Exception:
                pass

            screenshot_ok = screenshot_path.exists() and screenshot_path.stat().st_size > 120

            browser.close()
            return SpaCaptureResult(
                screenshot_ok=screenshot_ok,
                page_title=page_title,
                visible_claims=visible_claims[:12],
                raw_html=raw_html[:50000],
                raw_text=raw_text[:12000],
                error=None,
            )
    except Exception as exc:
        return SpaCaptureResult(
            screenshot_ok=False,
            page_title=None,
            visible_claims=[],
            raw_html="",
            raw_text="",
            error=f"{type(exc).__name__}:{exc}",
        )
