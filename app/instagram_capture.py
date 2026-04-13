from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

LOGIN_HINTS = (
    "login",
    "entrar",
    "sign in",
    "sign up",
    "cadastre-se",
    "faca login",
)

_INDIVIDUAL_POST_RE = re.compile(r"instagram\.com/(p|reel|reels|tv)/[\w-]+", re.IGNORECASE)


def is_instagram_post(url: str) -> bool:
    """True if URL points to an individual post/reel, not a profile."""
    return bool(_INDIVIDUAL_POST_RE.search(url))


@dataclass
class InstagramCaptureResult:
    screenshot_ok: bool
    page_title: str | None
    visible_claims: list[str]
    raw_text: str
    instagram_modal_dismissed: bool
    instagram_block_reason: str | None
    error: str | None
    caption: str | None = None


def _fold_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", without_accents.lower()).strip()


def _is_login_wall(text: str) -> bool:
    low = _fold_text(text)
    return any(token in low for token in LOGIN_HINTS)


def _click_if_visible(page: object, selector: str, timeout_ms: int) -> bool:
    locator = page.locator(selector).first
    if locator.count() < 1:
        return False
    if not locator.is_visible(timeout=min(timeout_ms, 1200)):
        return False
    locator.click(timeout=max(500, timeout_ms), force=True)
    return True


def _dismiss_login_modal(page: object, attempts: int, dismiss_timeout_ms: int) -> bool:
    selectors = [
        "button:has-text('Agora não')",
        "button:has-text('Agora nao')",
        "button:has-text('Not now')",
        "button:has-text('Agora mais tarde')",
        "[aria-label='Fechar']",
        "[aria-label='Close']",
        "svg[aria-label='Fechar']",
        "svg[aria-label='Close']",
    ]
    dismissed = False
    for _ in range(max(1, attempts)):
        clicked = False
        for selector in selectors:
            try:
                if _click_if_visible(page, selector, dismiss_timeout_ms):
                    clicked = True
                    dismissed = True
                    page.wait_for_timeout(700)
            except Exception:  # noqa: PERF203
                continue
        try:
            page.keyboard.press("Escape")
        except Exception:  # noqa: PERF203
            pass
        if not clicked:
            page.wait_for_timeout(500)
    return dismissed


def _extract_post_caption(page: object) -> str | None:
    """Extract caption text from an individual Instagram post page."""
    caption_selectors = [
        "article h1",
        "article span[dir='auto']",
        "div[role='dialog'] span[dir='auto']",
        "meta[property='og:description']",
    ]
    for selector in caption_selectors:
        try:
            if selector.startswith("meta"):
                el = page.locator(selector).first
                if el.count() > 0:
                    content = el.get_attribute("content", timeout=2000)
                    if content and len(content.strip()) > 10:
                        return content.strip()[:2000]
            else:
                els = page.locator(selector).all()
                texts = []
                for el in els[:5]:
                    t = el.inner_text(timeout=2000)
                    if t and len(t.strip()) > 10:
                        texts.append(t.strip())
                if texts:
                    return "\n".join(texts)[:2000]
        except Exception:  # noqa: PERF203
            continue
    return None


def _capture_individual_post(
    page: object,
    url: str,
    screenshot_path: Path,
    timeout_ms: int,
) -> InstagramCaptureResult:
    """Capture an individual Instagram post (publicly accessible without login)."""
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(2500)

    modal_dismissed = _dismiss_login_modal(page, attempts=1, dismiss_timeout_ms=1500)

    try:
        body_text = page.inner_text("body", timeout=4000)
    except Exception:
        body_text = ""

    block_reason = "instagram_login_wall" if _is_login_wall(body_text) else None
    caption = _extract_post_caption(page)

    page.screenshot(path=str(screenshot_path), full_page=True, timeout=timeout_ms)
    page_title = page.title()

    visible_claims: list[str] = []
    if caption:
        visible_claims.append(caption[:500])

    try:
        chunks = page.locator("h1, h2, article, main").all_inner_texts()
        for chunk in chunks:
            clean = re.sub(r"\s+", " ", chunk).strip()
            if len(clean) >= 15 and clean not in visible_claims:
                visible_claims.append(clean)
            if len(visible_claims) >= 12:
                break
    except Exception:
        pass

    return InstagramCaptureResult(
        screenshot_ok=screenshot_path.exists() and screenshot_path.stat().st_size > 120,
        page_title=page_title or None,
        visible_claims=visible_claims,
        raw_text=(body_text or "")[:12000],
        instagram_modal_dismissed=modal_dismissed,
        instagram_block_reason=block_reason,
        error=None,
        caption=caption,
    )


def _capture_profile(
    page: object,
    url: str,
    screenshot_path: Path,
    timeout_ms: int,
    dismiss_attempts: int,
    dismiss_timeout_ms: int,
) -> InstagramCaptureResult:
    """Capture an Instagram profile page (may require login modal dismiss)."""
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(2200)

    modal_dismissed = _dismiss_login_modal(
        page,
        attempts=dismiss_attempts,
        dismiss_timeout_ms=dismiss_timeout_ms,
    )

    try:
        body_text = page.inner_text("body", timeout=4000)
    except Exception:
        body_text = ""

    block_reason = "instagram_login_wall" if _is_login_wall(body_text) else None
    if block_reason and modal_dismissed:
        block_reason = "instagram_login_wall_after_dismiss"

    page.screenshot(path=str(screenshot_path), full_page=True, timeout=timeout_ms)
    page_title = page.title()

    visible_claims: list[str] = []
    try:
        chunks = page.locator("h1, h2, h3, article, main").all_inner_texts()
        for chunk in chunks:
            clean = re.sub(r"\s+", " ", chunk).strip()
            if len(clean) >= 15 and clean not in visible_claims:
                visible_claims.append(clean)
            if len(visible_claims) >= 12:
                break
    except Exception:
        visible_claims = []

    return InstagramCaptureResult(
        screenshot_ok=screenshot_path.exists() and screenshot_path.stat().st_size > 120,
        page_title=page_title or None,
        visible_claims=visible_claims,
        raw_text=(body_text or "")[:12000],
        instagram_modal_dismissed=modal_dismissed,
        instagram_block_reason=block_reason,
        error=None,
    )


def capture_instagram_with_playwright(
    *,
    url: str,
    screenshot_path: Path,
    timeout_seconds: int,
    dismiss_attempts: int = 3,
    dismiss_timeout_seconds: int = 2,
) -> InstagramCaptureResult:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return InstagramCaptureResult(
            screenshot_ok=False,
            page_title=None,
            visible_claims=[],
            raw_text="",
            instagram_modal_dismissed=False,
            instagram_block_reason="playwright_not_installed",
            error="playwright_not_installed",
        )

    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    timeout_ms = max(5, timeout_seconds) * 1000
    dismiss_timeout_ms = max(1, dismiss_timeout_seconds) * 1000

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                locale="pt-BR",
                user_agent=USER_AGENT,
                viewport={"width": 1366, "height": 900},
            )
            page = context.new_page()

            if is_instagram_post(url):
                result = _capture_individual_post(page, url, screenshot_path, timeout_ms)
            else:
                result = _capture_profile(
                    page, url, screenshot_path, timeout_ms,
                    dismiss_attempts, dismiss_timeout_ms,
                )

            browser.close()
            return result
    except Exception as exc:
        return InstagramCaptureResult(
            screenshot_ok=False,
            page_title=None,
            visible_claims=[],
            raw_text="",
            instagram_modal_dismissed=False,
            instagram_block_reason="instagram_capture_failed",
            error=f"{type(exc).__name__}:{exc}",
        )
