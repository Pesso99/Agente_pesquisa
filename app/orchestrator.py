from __future__ import annotations

import hashlib
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from app import constants
from app.deduper import build_full_catalog_for_report, dedupe_campaigns
from app.emailer import send_email
from app.io_utils import ensure_project_structure, iso_now_tz, read_json, stamp_for_id, write_json, write_model
from app.instagram_capture import capture_instagram_with_playwright
from app.spa_capture import capture_spa_with_playwright, is_spa_domain
from app.llm_client import AgentLLM
from app.models import Artifact, Campaign, Candidate, ExtractionResult, Handoff, Observation, ValidationVerdict
from app.normalizers import normalize_campaign, normalize_campaign_type, normalize_institution_id
from app.quality_gate import QualityAssessment, assess_observation_quality
from app.reporter import build_report, save_report_files
from app.runtime_db import RuntimeDB
from app.feedback import get_discovery_boost, get_extraction_prior, get_validation_adjustment
from app.scoring import analyze_screenshot, validate_campaign_two_pass
from app.validators import validate_model_against_schema

T = TypeVar("T")
REQUEST_TIMEOUT = 20

import logging as _logging

_logger = _logging.getLogger(__name__)

_llm: AgentLLM | None = None


def _get_llm() -> AgentLLM:
    global _llm
    if _llm is None:
        _llm = AgentLLM()
    return _llm
KEYWORDS = ("promoc", "campanh", "cashback", "oferta", "desconto", "bonus", "cupom", "anuidade", "milha", "ponto", "cdb", "cdi", "rendimento")
DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b")
CDI_PERCENT_RE = re.compile(r"\d+\s*%\s*do\s*cdi")
MONEY_RE = re.compile(r"r\$\s*\d+", re.IGNORECASE)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
BROWSER_CANDIDATES = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)

CAMPAIGN_SIGNAL_TERMS = (
    "promocao",
    "campanha",
    "cashback",
    "desconto",
    "oferta por tempo limitado",
    "participe",
    "regulamento",
    "cupom",
    "ganhe",
    "acumule pontos",
    "anuidade gratis",
    "isencao de anuidade",
    "milhas",
    "bonus",
)

INSTITUTIONAL_NEGATIVE_TERMS = (
    "sistema de informacoes de credito",
    "politica de privacidade",
    "termos de uso",
    "ouvidoria",
    "fale conosco",
    "tarifas",
    "acessibilidade",
    "sustentabilidade",
    "governanca",
    "carreiras",
    "imprensa",
)

DEFAULT_DISCOVERY_PATHS = (
    "",
    "/promocoes",
    "/campanhas",
    "/ofertas",
    "/cartao",
    "/investimentos",
    "/blog",
)

_SKIP_URL_RE = re.compile(
    r"(careers|vagas|login|signin|signup|cadastro|lgpd|privacidade|"
    r"governanca|sustentabilidade|ouvidoria|imprensa|"
    r"wikipedia\.org|glassdoor|reclameaqui|"
    r"/pdf/|\.pdf$|\.xlsx?$|\.docx?$)",
    re.IGNORECASE,
)


def _should_skip_url(url: str) -> bool:
    """Deterministic pre-filter: reject URLs that are clearly not campaigns (zero cost)."""
    return bool(_SKIP_URL_RE.search(url))

@dataclass
class ManualCycleResult:
    job_id: str
    candidates: list[Candidate]
    observations: list[Observation]
    campaigns: list[Campaign]
    dedupe_groups: dict[str, list[str]]
    report_paths: dict[str, Path]
    email_sent: bool


def _to_rel(path: Path) -> str:
    return path.relative_to(constants.ROOT_DIR).as_posix()


def _canon_url(url: str) -> str:
    parsed = urlparse(url)
    clean = parsed._replace(fragment="", query="")
    return urlunparse(clean).rstrip("/")


def _fingerprint(inst_id: str, url: str, title: str) -> str:
    normalized = f"{normalize_institution_id(inst_id)}|{_canon_url(url)}|{re.sub(r'[^a-z0-9 ]+', '', title.lower())}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _fold_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", without_accents.lower()).strip()


def _parse_candidate_notes(notes: str | None) -> dict[str, str]:
    if not notes:
        return {}
    parsed: dict[str, str] = {}
    for chunk in notes.split(";"):
        item = chunk.strip()
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        parsed[key.strip()] = value.strip()
    return parsed


def _is_historical_candidate(candidate: Candidate) -> bool:
    return _parse_candidate_notes(candidate.notes).get("is_historical", "false").lower() == "true"


def _build_notes(*, fingerprint: str, is_historical: bool = False, expected_label: str | None = None) -> str:
    payload = {"fingerprint": fingerprint, "is_historical": ("true" if is_historical else "false")}
    if expected_label:
        payload["expected_label"] = expected_label
    return ";".join(f"{k}={v}" for k, v in payload.items())


def _to_seed_entry(seed: str | dict[str, Any], *, default_source_type: str = "official_site") -> dict[str, Any]:
    if isinstance(seed, str):
        return {"url": seed, "source_type": default_source_type}
    if isinstance(seed, dict):
        payload = dict(seed)
        payload.setdefault("source_type", default_source_type)
        return payload
    return {}


def _expand_source_templates(institution: dict[str, Any], domains: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    raw_templates = institution.get("source_templates") or []
    for template in raw_templates:
        if isinstance(template, str):
            template_url = template
            paths = [""]
            source_type = "official_site"
            confidence = 0.78
        elif isinstance(template, dict):
            template_url = str(template.get("template", "")).strip()
            paths = template.get("paths") or [""]
            source_type = str(template.get("source_type", "official_site"))
            confidence = float(template.get("confidence", 0.78))
        else:
            continue
        if not template_url:
            continue
        for domain in domains:
            try:
                base = template_url.format(domain=domain).rstrip("/")
            except KeyError:
                continue
            for path in paths:
                suffix = str(path or "")
                if suffix and not suffix.startswith("/"):
                    suffix = f"/{suffix}"
                url = f"{base}{suffix}" if suffix else base
                entries.append(
                    {
                        "url": url,
                        "title": f"{institution.get('display_name', institution.get('institution_id', 'instituicao'))} template oficial",
                        "confidence": confidence,
                        "source_type": source_type,
                        "is_historical": False,
                    }
                )
    return entries


def _safe_get(url: str) -> requests.Response | None:
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-BR,pt;q=0.9"})
        resp.raise_for_status()
        return resp
    except requests.RequestException:
        return None


def _is_official(url: str, domains: list[str]) -> bool:
    host = urlparse(url).netloc.lower()
    return any(domain.lower() in host for domain in domains)


def _is_instagram(url: str) -> bool:
    return "instagram.com" in urlparse(url).netloc.lower()


def _discover_rows(base_url: str, html: str, domains: list[str], search_terms: list[str], max_links: int = 5) -> list[tuple[str, str, float, str]]:
    """Returns (url, title, confidence, source_type) tuples."""
    soup = BeautifulSoup(html, "lxml")
    rows: list[tuple[str, str, float, str]] = []
    seen: set[str] = set()
    title = (soup.title.text or "").strip() if soup.title else ""
    heading_text = " ".join(h.get_text(" ", strip=True) for h in soup.find_all(["h1", "h2"])[:8])
    if any(k in f"{title} {heading_text}".lower() for k in KEYWORDS):
        rows.append((base_url, title or base_url, 0.72, "official_site"))
        seen.add(base_url)
    non_official_count = 0
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"]).strip()
        if not href.startswith("http") or href in seen or _should_skip_url(href):
            continue
        is_official = _is_official(href, domains)
        if not is_official and non_official_count >= 2:
            continue
        text = f"{href} {a.get_text(' ', strip=True)}".lower()
        kw = sum(1 for k in KEYWORDS if k in text)
        if kw == 0:
            continue
        if is_official:
            conf = min(0.6 + 0.06 * kw + 0.03 * sum(1 for t in search_terms if t.lower() in text), 0.93)
            source_type = "official_site"
        else:
            conf = min(0.50 + 0.04 * kw, 0.65)
            source_type = "third_party"
            non_official_count += 1
        rows.append((href, a.get_text(" ", strip=True) or href, conf, source_type))
        seen.add(href)
        if len(rows) >= max_links:
            break
    return rows


def _find_browser() -> Path | None:
    for path in BROWSER_CANDIDATES:
        if path.exists():
            return path
    return None


def _screenshot(browser_bin: Path, url: str, out_png: Path, timeout_s: int) -> tuple[bool, str | None]:
    cmd = [str(browser_bin), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--window-size=1366,900", "--virtual-time-budget=15000", f"--screenshot={out_png}", url]
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
    except Exception as exc:  # noqa: PERF203
        return False, f"browser_error:{type(exc).__name__}"
    if cp.returncode == 0 and out_png.exists() and out_png.stat().st_size > 120:
        return True, None
    err = (cp.stderr or cp.stdout or str(cp.returncode)).strip()
    return False, f"screenshot_failed:{err[:180]}"


def _write_handoff(
    job_id: str,
    *,
    trace_id: str,
    task: str,
    source_agent: str,
    target_agent: str,
    input_refs: list[str],
    runtime_db: RuntimeDB | None = None,
    attempt: int = 1,
    source_quality_label: str = "campaign_like",
    capture_quality_score: float = 1.0,
    blocking_reasons: list[str] | None = None,
    instagram_modal_dismissed: bool | None = None,
    instagram_block_reason: str | None = None,
) -> None:
    handoff = Handoff(
        job_id=job_id,
        trace_id=trace_id,
        task=task,
        source_agent=source_agent,
        target_agent=target_agent,
        input_refs=input_refs,
        created_at=iso_now_tz(),
        attempt=attempt,
        source_quality_label=source_quality_label,
        capture_quality_score=capture_quality_score,
        blocking_reasons=blocking_reasons or [],
        instagram_modal_dismissed=instagram_modal_dismissed,
        instagram_block_reason=instagram_block_reason,
        priority="normal",
    )
    validate_model_against_schema(handoff)
    out = constants.JOBS_DIR / f"{job_id}_{task}_{target_agent}.json"
    write_model(out, handoff)
    if runtime_db:
        runtime_db.add_handoff(handoff)


def load_configs() -> dict[str, Any]:
    models_path = constants.CONFIG_DIR / "agent_models.json"
    historical_path = constants.CONFIG_DIR / "historical_seeds.json"
    return {
        "institutions": read_json(constants.CONFIG_DIR / "institutions.json"),
        "routing": read_json(constants.CONFIG_DIR / "routing_rules.json"),
        "scoring": read_json(constants.CONFIG_DIR / "scoring_rules.json"),
        "report": read_json(constants.CONFIG_DIR / "report_settings.json"),
        "email": read_json(constants.CONFIG_DIR / "email_settings.json"),
        "agent_models": read_json(models_path) if models_path.exists() else {},
        "historical_seeds": read_json(historical_path) if historical_path.exists() else [],
    }


def _process_web_search_citations(
    citations: list[dict[str, str]],
    domains: list[str],
    existing_urls: set[str],
    inst_id: str,
) -> list[dict[str, Any]]:
    """Convert raw citations into candidate entries, deduplicating against existing_urls."""
    results: list[dict[str, Any]] = []
    for cit in citations:
        raw_url = cit.get("url", "").strip()
        if not raw_url.startswith("http"):
            continue
        url = _canon_url(raw_url)
        if url in existing_urls or _should_skip_url(url):
            continue
        existing_urls.add(url)

        if _is_official(url, domains):
            source_type = "official_site"
            confidence = 0.80
        elif _is_instagram(url):
            source_type = "social_official"
            confidence = 0.75
        else:
            source_type = "search_result"
            confidence = 0.62

        results.append({
            "url": url,
            "title": cit.get("title", url)[:120],
            "confidence": confidence,
            "source_type": source_type,
        })
    return results


def _discover_via_web_search(
    inst: dict[str, Any],
    domains: list[str],
    existing_urls: set[str],
    search_context_size: str = "low",
) -> list[dict[str, Any]]:
    """Use LLM web search to find campaign URLs. Two queries per institution:
    one for official site campaigns, one for social media / Instagram."""
    terms = inst.get("search_terms", [])
    if not terms:
        return []
    try:
        llm = _get_llm()
    except Exception:
        _logger.warning("LLM not available for web search, skipping")
        return []

    results: list[dict[str, Any]] = []
    inst_id = inst.get("institution_id", "?")

    combined = " OR ".join(f'"{t}"' for t in terms[:4])
    query_site = (
        f"Encontre campanhas e promocoes ATIVAS de {inst['display_name']} em 2026. "
        f"Busque no site oficial e noticias: {combined}"
    )
    try:
        _text, citations = llm.search(
            "discover", query_site, search_context_size=search_context_size,
        )
        batch = _process_web_search_citations(citations, domains, existing_urls, inst_id)
        results.extend(batch)
        _logger.info(
            "Web search (site) for %s: %d citations, %d kept",
            inst_id, len(citations), len(batch),
        )
    except Exception as exc:
        _logger.warning("Web search (site) failed for %s: %s", inst_id, exc)

    insta_handle = (inst.get("official_socials") or {}).get("instagram", "")
    if insta_handle:
        query_social = (
            f"Encontre posts recentes de Instagram de {inst['display_name']} sobre "
            f"promocoes, campanhas ou ofertas em 2026. Perfil: {insta_handle}"
        )
        try:
            _text2, citations2 = llm.search(
                "discover", query_social, search_context_size=search_context_size,
            )
            batch2 = _process_web_search_citations(citations2, domains, existing_urls, inst_id)
            results.extend(batch2)
            _logger.info(
                "Web search (social) for %s: %d citations, %d kept",
                inst_id, len(citations2), len(batch2),
            )
        except Exception as exc:
            _logger.warning("Web search (social) failed for %s: %s", inst_id, exc)

    return results


def _collect_rows_for_institution(
    inst: dict[str, Any],
    global_hist: list[dict[str, Any]],
    routing: dict[str, Any],
    threshold: float,
    include_instagram: bool,
) -> list[dict[str, Any]]:
    """Gather and rank all candidate rows for a single institution."""
    domains = [str(d).strip() for d in (inst.get("official_domains") or []) if str(d).strip()]
    if not domains:
        return []

    rows: dict[str, dict[str, Any]] = {}

    def upsert_row(entry: dict[str, Any]) -> None:
        url = str(entry.get("url", "")).strip()
        if not url.startswith("http"):
            return
        key = _canon_url(url)
        title = str(entry.get("title") or key)
        confidence = float(entry.get("confidence", threshold))
        source_type = str(entry.get("source_type", "official_site"))
        is_historical = bool(entry.get("is_historical", False))
        expected_label = entry.get("expected_label")
        prev = rows.get(key)
        if prev is None or confidence > float(prev.get("confidence", 0.0)):
            rows[key] = {
                "url": key,
                "title": title,
                "confidence": confidence,
                "source_type": source_type,
                "is_historical": is_historical,
                "expected_label": expected_label,
            }

    for domain in domains:
        for path in DEFAULT_DISCOVERY_PATHS:
            upsert_row({
                "url": f"https://{domain}{path}",
                "title": f"{inst['display_name']} seed oficial",
                "confidence": 0.68,
                "source_type": "official_site",
            })

    for entry in _expand_source_templates(inst, domains):
        upsert_row(entry)

    for raw_seed in inst.get("discovery_seeds") or []:
        upsert_row(_to_seed_entry(raw_seed, default_source_type="official_site"))

    inst_id_lower = str(inst.get("institution_id", "")).strip().lower()
    for hseed in global_hist:
        if str(hseed.get("institution_id", "")).strip().lower() != inst_id_lower:
            continue
        seed_payload = _to_seed_entry(hseed, default_source_type="official_site")
        seed_payload["is_historical"] = True
        seed_payload.setdefault("confidence", 0.82)
        upsert_row(seed_payload)

    for seed in list(rows.values()):
        seed_url = str(seed["url"])
        if not _is_official(seed_url, domains):
            continue
        resp = _safe_get(seed_url)
        if not resp:
            continue
        for url, title, conf, src_type in _discover_rows(seed_url, resp.text, domains, inst.get("search_terms", [])):
            upsert_row({"url": url, "title": title, "confidence": conf, "source_type": src_type})

    if routing.get("enable_web_search", False):
        ws_context = str(routing.get("web_search_context_size", "low"))
        existing = {r["url"] for r in rows.values()}
        for ws_result in _discover_via_web_search(inst, domains, existing, ws_context):
            upsert_row(ws_result)

    if include_instagram:
        insta_url = (inst.get("official_socials") or {}).get("instagram")
        if isinstance(insta_url, str) and insta_url.startswith("http"):
            upsert_row({
                "url": insta_url,
                "title": f"{inst['display_name']} instagram oficial",
                "confidence": 0.64,
                "source_type": "social_official",
            })

    if not rows:
        fallback = f"https://{domains[0]}"
        upsert_row({
            "url": fallback,
            "title": f"{inst['display_name']} - pagina oficial",
            "confidence": max(0.62, threshold + 0.02),
            "source_type": "official_site",
        })

    ordered = sorted(
        rows.values(),
        key=lambda row: (
            bool(row.get("is_historical", False)),
            row.get("source_type") == "official_site",
            float(row.get("confidence", 0.0)),
        ),
        reverse=True,
    )

    priority: list[dict[str, Any]] = []
    if include_instagram:
        social = [r for r in ordered if r.get("source_type") == "social_official"]
        if social:
            priority.append(max(social, key=lambda r: float(r.get("confidence", 0.0))))

    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in priority + ordered:
        url = str(row["url"])
        if url in seen:
            continue
        if float(row["confidence"]) < threshold:
            continue
        seen.add(url)
        result.append(row)
    return result


def _emit_candidates(
    rows: list[dict[str, Any]],
    inst: dict[str, Any],
    *,
    limit: int,
    stamp: str,
    idx_start: int,
    job_id: str,
    trace: str,
    runtime_db: RuntimeDB | None = None,
) -> list[Candidate]:
    """Create Candidate objects from ranked rows, up to *limit*."""
    output: list[Candidate] = []
    idx = idx_start
    for row in rows:
        if len(output) >= limit:
            break
        url = str(row["url"])
        title = str(row["title"])
        source_type = str(row["source_type"])
        is_historical = bool(row.get("is_historical", False))
        expected_label = row.get("expected_label")

        fprint = _fingerprint(inst["institution_id"], url, title)
        if runtime_db and not runtime_db.register_fingerprint(fprint, job_id=job_id):
            continue
        history_boost = 0.0
        if runtime_db:
            history_boost = get_discovery_boost(runtime_db, inst["institution_id"], source_type)
        adjusted_conf = max(0.0, min(1.0, float(row["confidence"]) + history_boost))
        cand = Candidate(
            candidate_id=f"cand_{stamp}_{idx:03d}",
            institution_id=inst["institution_id"],
            source_type=source_type,  # type: ignore[arg-type]
            source_url=url,
            headline=(title or f"{inst['display_name']} campanha")[:180],
            discovered_at=iso_now_tz(),
            confidence_initial=round(adjusted_conf, 3),
            summary="discover oficial/templates/seeds",
            notes=_build_notes(
                fingerprint=fprint,
                is_historical=is_historical,
                expected_label=(str(expected_label) if expected_label else None),
            ),
        )
        validate_model_against_schema(cand)
        candidate_path = constants.CANDIDATES_DIR / f"{cand.candidate_id}.json"
        write_model(candidate_path, cand)
        output.append(cand)
        idx += 1
        if runtime_db:
            runtime_db.index_artifact(
                job_id=job_id,
                entity_type="candidate",
                entity_id=cand.candidate_id,
                artifact_type="candidate_json",
                path=_to_rel(candidate_path),
                meta={"source_type": cand.source_type, "is_historical": is_historical},
            )
            runtime_db.add_agent_message(
                job_id=job_id,
                trace_id=trace,
                source_agent="discover",
                target_agent="capture",
                message_type="candidate_found",
                body={
                    "candidate_id": cand.candidate_id,
                    "url": cand.source_url,
                    "source_type": cand.source_type,
                    "is_historical": is_historical,
                },
            )
    return output


def discover_candidates(
    job_id: str,
    institutions: list[dict],
    routing: dict,
    *,
    historical_seeds: list[dict[str, Any]] | None = None,
    runtime_db: RuntimeDB | None = None,
    trace_id: str | None = None,
    attempt: int = 1,
) -> list[Candidate]:
    threshold = float(routing.get("discovery_to_capture_min_confidence", 0.6))
    max_total = max(1, int(routing.get("max_candidates_total", 8)))
    max_per_inst = max(1, int(routing.get("max_candidates_per_institution", 2)))
    min_per_inst = max(1, int(routing.get("min_candidates_per_institution", 2)))
    include_instagram = bool(routing.get("include_instagram_sources", True))
    trace = trace_id or f"{job_id}:discover"
    stamp = stamp_for_id()
    global_hist = historical_seeds or []

    sorted_institutions = sorted(
        institutions,
        key=lambda x: (x.get("priority", 99), x.get("institution_id", "")),
    )

    rows_by_inst: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for inst in sorted_institutions:
        inst_rows = _collect_rows_for_institution(
            inst, global_hist, routing, threshold, include_instagram,
        )
        if inst_rows:
            rows_by_inst.append((inst, inst_rows))

    output: list[Candidate] = []
    idx = 1
    consumed: dict[str, int] = {}

    # -- Pass 1: guarantee minimum coverage for every institution --
    for inst, inst_rows in rows_by_inst:
        iid = inst["institution_id"]
        take = min(min_per_inst, len(inst_rows))
        emitted = _emit_candidates(
            inst_rows[:take],
            inst,
            limit=take,
            stamp=stamp,
            idx_start=idx,
            job_id=job_id,
            trace=trace,
            runtime_db=runtime_db,
        )
        output.extend(emitted)
        idx += len(emitted)
        consumed[iid] = len(emitted)

    # -- Pass 2: fill remaining slots up to max_total --
    for inst, inst_rows in rows_by_inst:
        if len(output) >= max_total:
            break
        iid = inst["institution_id"]
        already = consumed.get(iid, 0)
        remaining_for_inst = max_per_inst - already
        if remaining_for_inst <= 0:
            continue
        remaining_total = max_total - len(output)
        if remaining_total <= 0:
            break
        take = min(remaining_for_inst, remaining_total)
        leftover_rows = inst_rows[already:]
        emitted = _emit_candidates(
            leftover_rows[:take],
            inst,
            limit=take,
            stamp=stamp,
            idx_start=idx,
            job_id=job_id,
            trace=trace,
            runtime_db=runtime_db,
        )
        output.extend(emitted)
        idx += len(emitted)
        consumed[iid] = already + len(emitted)

    _write_handoff(
        job_id,
        trace_id=trace,
        task="discover_to_capture",
        source_agent="discover",
        target_agent="capture",
        input_refs=[_to_rel(constants.CANDIDATES_DIR / f"{x.candidate_id}.json") for x in output],
        runtime_db=runtime_db,
        attempt=attempt,
    )
    return output


def capture_observations(
    job_id: str,
    candidates: list[Candidate],
    *,
    capture_timeout_seconds: int = 12,
    instagram_capture_mode: str = "playwright_dismiss",
    instagram_dismiss_attempts: int = 3,
    instagram_dismiss_timeout_seconds: int = 2,
    runtime_db: RuntimeDB | None = None,
    trace_id: str | None = None,
    attempt: int = 1,
    block_institutional: bool = True,
) -> tuple[list[Observation], dict[str, QualityAssessment]]:
    trace = trace_id or f"{job_id}:capture"
    stamp = stamp_for_id()
    browser = _find_browser()
    observations: list[Observation] = []
    quality_by_obs: dict[str, QualityAssessment] = {}
    for i, cand in enumerate(candidates, start=1):
        obs_id = f"obs_{stamp}_{i:03d}"
        png = constants.SCREENSHOTS_DIR / f"{obs_id}.png"
        html_path = constants.RAW_HTML_DIR / f"{obs_id}.html"
        text_path = constants.RAW_TEXT_DIR / f"{obs_id}.txt"
        html: str | None = None
        text: str | None = None
        title: str | None = None
        claims: list[str] = []
        screenshot_ok = False
        note: str | None = None
        instagram_modal_dismissed: bool | None = None
        instagram_block_reason: str | None = None

        if _is_instagram(cand.source_url) and instagram_capture_mode == "playwright_dismiss":
            insta_result = capture_instagram_with_playwright(
                url=cand.source_url,
                screenshot_path=png,
                timeout_seconds=max(6, capture_timeout_seconds),
                dismiss_attempts=max(1, instagram_dismiss_attempts),
                dismiss_timeout_seconds=max(1, instagram_dismiss_timeout_seconds),
            )
            screenshot_ok = insta_result.screenshot_ok
            instagram_modal_dismissed = insta_result.instagram_modal_dismissed
            instagram_block_reason = insta_result.instagram_block_reason
            note = insta_result.error
            if insta_result.page_title:
                title = insta_result.page_title
            if insta_result.visible_claims:
                claims = insta_result.visible_claims[:12]
            if insta_result.raw_text:
                text = insta_result.raw_text[:12000]
            if (not screenshot_ok) and browser:
                retry_ok, retry_err = _screenshot(browser, cand.source_url, png, min(30, max(8, capture_timeout_seconds * 2)))
                screenshot_ok = retry_ok
                note = None if retry_ok else retry_err

        elif is_spa_domain(cand.source_url):
            _logger.info("SPA capture via Playwright for %s", cand.source_url)
            spa_result = capture_spa_with_playwright(
                url=cand.source_url,
                screenshot_path=png,
                timeout_seconds=max(10, capture_timeout_seconds + 8),
            )
            screenshot_ok = spa_result.screenshot_ok
            note = spa_result.error
            title = spa_result.page_title
            claims = spa_result.visible_claims[:12]
            html = spa_result.raw_html or None
            text = spa_result.raw_text or None

        else:
            resp = _safe_get(cand.source_url)
            html = resp.text if resp else None
            text = BeautifulSoup(html, "lxml").get_text("\n", strip=True) if html else None
            if html:
                soup = BeautifulSoup(html, "lxml")
                title = (soup.title.text or "").strip() if soup.title else None
                claims = [h.get_text(" ", strip=True) for h in soup.find_all(["h1", "h2", "h3"]) if h.get_text(" ", strip=True)][:12]
            if browser:
                screenshot_ok, shot_err = _screenshot(browser, cand.source_url, png, max(4, capture_timeout_seconds))
                if (not screenshot_ok) and html:
                    retry_ok, retry_err = _screenshot(browser, cand.source_url, png, min(30, max(8, capture_timeout_seconds * 2)))
                    screenshot_ok = retry_ok
                    shot_err = None if retry_ok else retry_err
                note = shot_err
            else:
                note = "no_browser"
        artifacts: list[Artifact] = []
        if screenshot_ok and png.exists():
            artifacts.append(Artifact(type="screenshot_full", path=_to_rel(png)))
        if html:
            html_path.write_text(html, encoding="utf-8")
            artifacts.append(Artifact(type="raw_html", path=_to_rel(html_path)))
        if text:
            text_path.write_text(text, encoding="utf-8")
            artifacts.append(Artifact(type="raw_text", path=_to_rel(text_path)))
        visible_claims = claims or [cand.headline]
        if note:
            visible_claims = visible_claims + [note]
        if instagram_block_reason:
            visible_claims = visible_claims + [instagram_block_reason]
        obs = Observation(
            observation_id=obs_id,
            candidate_id=cand.candidate_id,
            captured_at=iso_now_tz(),
            source_url=cand.source_url,
            page_title=title or cand.headline,
            visible_claims=visible_claims[:12],
            artifacts=artifacts,
            raw_html_path=_to_rel(html_path) if html else None,
            raw_text_path=_to_rel(text_path) if text else None,
            instagram_modal_dismissed=instagram_modal_dismissed,
            instagram_block_reason=instagram_block_reason,
        )
        validate_model_against_schema(obs)
        observation_path = constants.OBSERVATIONS_DIR / f"{obs.observation_id}.json"
        write_model(observation_path, obs)
        observations.append(obs)
        qa = assess_observation_quality(cand, obs, block_institutional=block_institutional)
        quality_by_obs[obs.observation_id] = qa
        if runtime_db:
            runtime_db.index_artifact(
                job_id=job_id,
                entity_type="observation",
                entity_id=obs.observation_id,
                artifact_type="observation_json",
                path=_to_rel(observation_path),
                meta={"source_quality_label": qa.source_quality_label},
            )
            for art in artifacts:
                runtime_db.index_artifact(job_id=job_id, entity_type="observation", entity_id=obs.observation_id, artifact_type=art.type, path=art.path, meta={"source_url": cand.source_url})
            runtime_db.add_agent_message(job_id=job_id, trace_id=trace, source_agent="capture", target_agent="extract", message_type="observation_captured", body={"observation_id": obs.observation_id, "quality_label": qa.source_quality_label, "capture_quality_score": qa.capture_quality_score, "blocked": qa.should_block})
    avg_score = (sum(q.capture_quality_score for q in quality_by_obs.values()) / len(quality_by_obs)) if quality_by_obs else 0.0
    blocking = sorted({r for q in quality_by_obs.values() for r in q.blocking_reasons if r.startswith("blocked_source_label:")})
    ig_observations = [obs for obs in observations if _is_instagram(obs.source_url)]
    ig_dismissed = any(obs.instagram_modal_dismissed is True for obs in ig_observations) if ig_observations else None
    ig_block_reason = next((obs.instagram_block_reason for obs in ig_observations if obs.instagram_block_reason), None)
    _write_handoff(
        job_id,
        trace_id=trace,
        task="capture_to_extract",
        source_agent="capture",
        target_agent="extract",
        input_refs=[_to_rel(constants.OBSERVATIONS_DIR / f"{x.observation_id}.json") for x in observations],
        runtime_db=runtime_db,
        attempt=attempt,
        source_quality_label="campaign_like",
        capture_quality_score=round(avg_score, 3),
        blocking_reasons=blocking,
        instagram_modal_dismissed=ig_dismissed,
        instagram_block_reason=ig_block_reason,
    )
    return observations, quality_by_obs


def _infer_benefit(text: str) -> str | None:
    low = _fold_text(text)
    if "cashback" in low:
        return "Oferta com cashback para produtos elegiveis."
    if "isencao de anuidade" in low or "anuidade gratis" in low:
        return "Oferta com isencao de anuidade."
    if "acumule pontos" in low or "milhas" in low:
        return "Oferta com acumulo de pontos/milhas."
    if "cdb" in low or "cdi" in low or CDI_PERCENT_RE.search(low):
        return "Oferta promocional de renda fixa."
    if "desconto" in low or MONEY_RE.search(low):
        return "Oferta com desconto financeiro claro."
    return None


def _looks_like_campaign(text: str) -> bool:
    folded = _fold_text(text)
    positive_hits = sum(1 for token in CAMPAIGN_SIGNAL_TERMS if token in folded)
    negative_hits = sum(1 for token in INSTITUTIONAL_NEGATIVE_TERMS if token in folded)
    has_date = bool(DATE_RE.search(text))
    has_value = bool(CDI_PERCENT_RE.search(folded) or MONEY_RE.search(folded))

    if negative_hits >= 2 and positive_hits <= 1:
        return False
    if positive_hits >= 2:
        return True
    if positive_hits >= 1 and (has_date or has_value or "regulamento" in folded):
        return True
    return False


def _text_tokens(text: str) -> set[str]:
    folded = _fold_text(text)
    return {tok for tok in re.split(r"[^a-z0-9]+", folded) if len(tok) >= 5}


def _best_official_confirmation(
    *,
    seed_candidate: Candidate,
    seed_observation: Observation,
    candidates_by_id: dict[str, Candidate],
    observations: list[Observation],
    quality_by_obs: dict[str, QualityAssessment],
) -> tuple[str | None, Observation | None]:
    if not _is_instagram(seed_candidate.source_url):
        return None, None

    seed_text = f"{seed_observation.page_title or ''} {' '.join(seed_observation.visible_claims)}"
    seed_tokens = _text_tokens(seed_text)
    best: tuple[float, Candidate, Observation] | None = None

    for obs in observations:
        if obs.candidate_id == seed_observation.candidate_id:
            continue
        other_cand = candidates_by_id.get(obs.candidate_id)
        if not other_cand:
            continue
        if other_cand.institution_id != seed_candidate.institution_id:
            continue
        if _is_instagram(other_cand.source_url):
            continue
        qa = quality_by_obs.get(obs.observation_id)
        if qa and qa.should_block:
            continue
        has_screenshot = any(art.type.startswith("screenshot") for art in obs.artifacts)
        if not has_screenshot:
            continue
        obs_text = f"{obs.page_title or ''} {' '.join(obs.visible_claims)}"
        obs_tokens = _text_tokens(obs_text)
        if not obs_tokens:
            continue
        overlap = len(seed_tokens & obs_tokens) / max(1, len(seed_tokens | obs_tokens))
        campaign_hint_overlap = sum(1 for token in CAMPAIGN_SIGNAL_TERMS if token in _fold_text(obs_text))
        score = overlap + (0.05 * campaign_hint_overlap) + (0.1 if (qa and qa.capture_quality_score >= 0.65) else 0.0)
        if score < 0.12:
            continue
        if best is None or score > best[0]:
            best = (score, other_cand, obs)

    if not best:
        return None, None
    return best[1].source_url, best[2]


def _build_extract_prompt(cand: Candidate, obs: Observation, raw_text: str) -> str:
    claims_text = "\n".join(f"- {c}" for c in obs.visible_claims[:12]) if obs.visible_claims else "(nenhum)"
    return (
        f"Instituicao: {cand.institution_id}\n"
        f"URL: {cand.source_url}\n"
        f"Tipo de fonte: {cand.source_type}\n"
        f"Titulo da pagina: {obs.page_title or '(sem titulo)'}\n"
        f"\nClaims visiveis:\n{claims_text}\n"
        f"\nTexto da pagina (ate 4000 chars):\n{raw_text[:4000]}\n"
    )


def _llm_extract(cand: Candidate, obs: Observation, raw_text: str) -> ExtractionResult | None:
    """Call the extract LLM agent. Returns None on failure (caller falls back to deterministic)."""
    try:
        llm = _get_llm()
        prompt = _build_extract_prompt(cand, obs, raw_text)
        result = llm.call("extract", prompt, response_format=ExtractionResult)
        return result  # type: ignore[return-value]
    except Exception as exc:
        _logger.warning("LLM extract failed for %s: %s", obs.observation_id, exc)
        return None


def extract_campaigns(
    job_id: str,
    candidates: list[Candidate],
    observations: list[Observation],
    quality_by_obs: dict[str, QualityAssessment] | None = None,
    *,
    runtime_db: RuntimeDB | None = None,
    trace_id: str | None = None,
    attempt: int = 1,
    min_capture_quality_score: float = 0.55,
) -> list[Campaign]:
    trace = trace_id or f"{job_id}:extract"
    by_candidate = {c.candidate_id: c for c in candidates}
    quality_map = quality_by_obs or {}
    stamp = stamp_for_id()
    campaigns: list[Campaign] = []
    for i, obs in enumerate(observations, start=1):
        cand = by_candidate[obs.candidate_id]
        qa = quality_map.get(obs.observation_id)
        if qa and qa.should_block:
            if runtime_db:
                runtime_db.add_dead_letter(job_id=job_id, stage="quality_gate", record_id=obs.observation_id, error_message="Observation blocked by quality gate.", payload={"source_quality_label": qa.source_quality_label, "blocking_reasons": qa.blocking_reasons, "source_url": obs.source_url})
            continue
        if qa and qa.capture_quality_score < min_capture_quality_score:
            if runtime_db:
                runtime_db.add_dead_letter(
                    job_id=job_id,
                    stage="capture_quality",
                    record_id=obs.observation_id,
                    error_message="Observation capture quality below threshold.",
                    payload={
                        "capture_quality_score": qa.capture_quality_score,
                        "min_capture_quality_score": min_capture_quality_score,
                        "source_url": obs.source_url,
                    },
                )
            continue
        raw_text = ""
        if obs.raw_text_path:
            p = constants.ROOT_DIR / obs.raw_text_path
            if p.exists():
                raw_text = p.read_text(encoding="utf-8")[:6000]
        combined = f"{cand.headline} {obs.page_title or ''} {' | '.join(obs.visible_claims[:10])} {raw_text}".strip()

        # --- LLM extraction with deterministic fallback ---
        llm_result = _llm_extract(cand, obs, raw_text)

        if llm_result is not None:
            if not llm_result.is_campaign:
                if runtime_db:
                    runtime_db.add_dead_letter(
                        job_id=job_id,
                        stage="extract_filter",
                        record_id=obs.observation_id,
                        error_message=f"LLM classified as non-campaign: {llm_result.confidence_reasoning}",
                        payload={
                            "source_url": obs.source_url,
                            "page_title": obs.page_title,
                            "llm_reasoning": llm_result.confidence_reasoning,
                        },
                    )
                continue
            extracted_name = (llm_result.campaign_name or obs.page_title or cand.headline or cand.source_url)[:180]
            extracted_type = llm_result.campaign_type or normalize_campaign_type(combined)
            extracted_benefit = llm_result.benefit
            extracted_audience = llm_result.audience or "clientes em geral"
            extracted_start_date = llm_result.start_date
            extracted_end_date = llm_result.end_date
            extracted_regulation_url = llm_result.regulation_url
            extraction_notes = llm_result.confidence_reasoning
        else:
            # Deterministic fallback
            if not _looks_like_campaign(combined):
                if runtime_db:
                    runtime_db.add_dead_letter(
                        job_id=job_id,
                        stage="extract_filter",
                        record_id=obs.observation_id,
                        error_message="Rejected by deterministic campaign filter (LLM unavailable).",
                        payload={
                            "source_url": obs.source_url,
                            "page_title": obs.page_title,
                            "source_quality_label": (qa.source_quality_label if qa else "unknown"),
                        },
                    )
                continue
            dates_fallback = DATE_RE.findall(combined)
            extracted_name = (obs.page_title or cand.headline or cand.source_url)[:180]
            extracted_type = normalize_campaign_type(combined)
            extracted_benefit = _infer_benefit(combined)
            extracted_audience = "clientes em geral"
            extracted_start_date = dates_fallback[0] if len(dates_fallback) >= 1 else None
            extracted_end_date = dates_fallback[1] if len(dates_fallback) >= 2 else None
            extracted_regulation_url = None
            extraction_notes = "Extraido por fallback deterministico (LLM indisponivel)."

        channels = ["site_oficial" if cand.source_type == "official_site" else "instagram_publico"]
        if _is_historical_candidate(cand):
            channels.append("historical_seed")

        regulation_url = extracted_regulation_url
        evidence_refs = [obs.observation_id] + [a.path for a in obs.artifacts]
        if _is_instagram(cand.source_url):
            confirmed_url, confirmed_obs = _best_official_confirmation(
                seed_candidate=cand,
                seed_observation=obs,
                candidates_by_id=by_candidate,
                observations=observations,
                quality_by_obs=quality_map,
            )
            if confirmed_url and confirmed_obs:
                regulation_url = confirmed_url
                channels.extend(["official_confirmation", "official_confirmation_screenshot_ok"])
                if confirmed_obs.observation_id not in evidence_refs:
                    evidence_refs.append(confirmed_obs.observation_id)
                for art in confirmed_obs.artifacts:
                    if art.path not in evidence_refs:
                        evidence_refs.append(art.path)
            else:
                channels.append("needs_official_confirmation")

        initial_notes = extraction_notes
        if "needs_official_confirmation" in channels:
            initial_notes = "needs_official_confirmation"
        campaign = Campaign(
            campaign_id=f"camp_{stamp}_{i:03d}",
            institution_id=cand.institution_id,
            campaign_name=extracted_name,
            campaign_type=extracted_type,
            source_url=cand.source_url,
            status="review",
            confidence_final=0.0,
            evidence_refs=evidence_refs,
            benefit=extracted_benefit,
            audience=extracted_audience,
            source_type=cand.source_type,
            regulation_url=regulation_url,
            start_date=extracted_start_date,
            end_date=extracted_end_date,
            validation_notes=initial_notes,
            channels=channels,
        )
        fp = _fingerprint(campaign.institution_id, campaign.source_url, campaign.campaign_name)
        if runtime_db and not runtime_db.register_fingerprint(fp, job_id=job_id, campaign_id=campaign.campaign_id):
            continue

        if runtime_db:
            similar = runtime_db.find_similar_in_history(fingerprint=fp, source_url=cand.source_url)
            if similar:
                best = similar[0]
                feedbacks = runtime_db.get_feedback_for_campaign(best["campaign_id"])
                if feedbacks:
                    last_verdict = feedbacks[0]["verdict"]
                    if last_verdict == "confirmed":
                        campaign.history_match_id = best["campaign_id"]
                        prior = get_extraction_prior(runtime_db, cand.institution_id, extracted_type)
                        campaign.confidence_final = max(0.0, min(1.0, campaign.confidence_final + prior))
                    elif last_verdict == "denied":
                        campaign.validation_notes = f"history_denied:{best['campaign_id']}; {campaign.validation_notes or ''}"
                else:
                    campaign.history_match_id = best["campaign_id"]

        campaign = normalize_campaign(campaign)
        validate_model_against_schema(campaign)
        campaign_path = constants.CAMPAIGNS_DIR / f"{campaign.campaign_id}.json"
        write_model(campaign_path, campaign)
        campaigns.append(campaign)
        if runtime_db:
            runtime_db.index_artifact(
                job_id=job_id,
                entity_type="campaign",
                entity_id=campaign.campaign_id,
                artifact_type="campaign_json",
                path=_to_rel(campaign_path),
                meta={"status": campaign.status},
            )
    _write_handoff(job_id, trace_id=trace, task="extract_to_validate", source_agent="extract", target_agent="validate", input_refs=[_to_rel(constants.CAMPAIGNS_DIR / f"{x.campaign_id}.json") for x in campaigns], runtime_db=runtime_db, attempt=attempt)
    return campaigns


def _find_screenshot_path(camp: Campaign, by_obs: dict[str, Observation]) -> Path | None:
    """Find the first usable screenshot path from evidence refs."""
    for ref in camp.evidence_refs:
        if ref.endswith(".png") and "screenshot" in ref:
            full = constants.ROOT_DIR / ref
            if full.exists() and full.stat().st_size > 120:
                return full
        obs = by_obs.get(ref)
        if obs:
            for art in obs.artifacts:
                if art.type.startswith("screenshot"):
                    full = constants.ROOT_DIR / art.path
                    if full.exists() and full.stat().st_size > 120:
                        return full
    return None


def validate_campaigns(
    job_id: str,
    campaigns: list[Campaign],
    observations: list[Observation],
    scoring_rules: dict,
    *,
    instagram_require_official_confirmation: bool = True,
    runtime_db: RuntimeDB | None = None,
    trace_id: str | None = None,
    attempt: int = 1,
) -> list[Campaign]:
    trace = trace_id or f"{job_id}:validate"
    by_obs = {o.observation_id: o for o in observations}
    out: list[Campaign] = []
    for camp in campaigns:
        has_screenshot = False
        screenshot_path: Path | None = None
        for ref in camp.evidence_refs:
            obs = by_obs.get(ref)
            if obs and any(a.type.startswith("screenshot") for a in obs.artifacts):
                has_screenshot = True
                break
        try:
            llm = _get_llm()
        except Exception:
            llm = None
        screenshot_analysis = None
        if has_screenshot and llm is not None:
            screenshot_path = _find_screenshot_path(camp, by_obs)
            if screenshot_path:
                screenshot_analysis = analyze_screenshot(llm, screenshot_path, camp)
                if screenshot_analysis:
                    _logger.info(
                        "Screenshot analysis for %s: type=%s, promo=%s, conf=%.2f",
                        camp.campaign_id,
                        screenshot_analysis.page_type_visual,
                        screenshot_analysis.has_promotional_content,
                        screenshot_analysis.visual_confidence,
                    )
        primary, critic, final = validate_campaign_two_pass(
            camp, scoring_rules,
            has_screenshot=has_screenshot,
            llm=llm,
            screenshot_analysis=screenshot_analysis,
        )
        if runtime_db:
            hist_adj = get_validation_adjustment(runtime_db, final)
            if hist_adj != 0.0:
                final.confidence_final = round(max(0.0, min(1.0, final.confidence_final + hist_adj)), 3)
        if instagram_require_official_confirmation and _is_instagram(camp.source_url):
            has_official_confirmation = bool(camp.regulation_url) and ("official_confirmation_screenshot_ok" in camp.channels)
            if not has_official_confirmation:
                final.status = "review"
                final.validation_notes = (
                    "needs_official_confirmation: campanha via instagram requer URL oficial nao-instagram com screenshot util."
                    f" | Validacao original: {final.validation_notes or '(sem notas)'}"
                )
        if "historical_seed" in camp.channels and final.status in {"validated", "validated_with_reservations"}:
            final.status = "review"
            final.validation_notes = (
                "historical_seed_requires_current_evidence"
                f" | Validacao original: {final.validation_notes or '(sem notas)'}"
            )
        validate_model_against_schema(final)
        write_model(constants.CAMPAIGNS_DIR / f"{final.campaign_id}.json", final)
        out.append(final)
        if runtime_db:
            runtime_db.add_agent_message(job_id=job_id, trace_id=trace, source_agent="validator_primary", target_agent="validator_critic", message_type=("validator_consensus" if primary.status == critic.status else "validator_divergence"), body={"campaign_id": camp.campaign_id, "primary_status": primary.status, "critic_status": critic.status, "final_status": final.status})
    _write_handoff(job_id, trace_id=trace, task="validate_to_report", source_agent="validate", target_agent="report", input_refs=[_to_rel(constants.CAMPAIGNS_DIR / f"{x.campaign_id}.json") for x in out], runtime_db=runtime_db, attempt=attempt)
    return out


def generate_report(
    job_id: str,
    campaigns: list[Campaign],
    report_settings: dict,
    *,
    full_catalog: list[Campaign] | None = None,
    runtime_db: RuntimeDB | None = None,
    trace_id: str | None = None,
    attempt: int = 1,
) -> dict[str, Path]:
    merged, _merge_groups = (
        (full_catalog, {}) if full_catalog is not None else build_full_catalog_for_report(campaigns)
    )
    new_ids = {c.campaign_id for c in campaigns}
    report = build_report(
        merged,
        report_settings,
        report_id=f"report_{job_id}",
        runtime_db=runtime_db,
        new_cycle_campaign_ids=new_ids,
        cycle_campaigns_for_insights=campaigns,
    )
    paths = save_report_files(report, constants.REPORTS_DIR)
    if runtime_db:
        runtime_db.index_artifact(job_id=job_id, entity_type="report", entity_id=report.report_id, artifact_type="report_json", path=_to_rel(paths["json"]), meta={})
        runtime_db.index_artifact(job_id=job_id, entity_type="report", entity_id=report.report_id, artifact_type="report_markdown", path=_to_rel(paths["markdown"]), meta={})
        runtime_db.index_artifact(job_id=job_id, entity_type="report", entity_id=report.report_id, artifact_type="report_html", path=_to_rel(paths["html"]), meta={})
    _write_handoff(job_id, trace_id=(trace_id or f"{job_id}:report"), task="report_to_sender", source_agent="report", target_agent="sender", input_refs=[_to_rel(paths["json"]), _to_rel(paths["markdown"]), _to_rel(paths["html"])], runtime_db=runtime_db, attempt=attempt)
    return paths


def _run_with_retry(runtime_db: RuntimeDB, job_id: str, stage: str, max_attempts: int, backoff_base: int, fn: Callable[[int], T]) -> T:
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = fn(attempt)
            runtime_db.log_run(job_id=job_id, stage=stage, attempt=attempt, status="success")
            return result
        except Exception as exc:  # noqa: PERF203
            last_exc = exc
            backoff = int(backoff_base * (2 ** (attempt - 1)))
            runtime_db.log_run(job_id=job_id, stage=stage, attempt=attempt, status="failed", error_message=str(exc), backoff_seconds=backoff)
            if attempt == max_attempts:
                runtime_db.add_dead_letter(job_id=job_id, stage=stage, error_message=str(exc), payload={"attempt": attempt})
            else:
                time.sleep(min(backoff, 2))
    raise RuntimeError(f"Stage {stage} failed after {max_attempts} attempts") from last_exc


def _can_send(runtime_db: RuntimeDB, job_id: str) -> bool:
    runtime_db.ensure_approval(job_id)
    return runtime_db.get_approval_status(job_id) == "approved"


def run_autonomous_cycle(job_id: str, *, send_report_email: bool = False, recipients: list[str] | None = None, routing_overrides: dict[str, Any] | None = None, autonomous: bool = True) -> ManualCycleResult:
    ensure_project_structure()
    cfg = load_configs()
    routing = dict(cfg["routing"])
    if routing_overrides:
        routing.update(routing_overrides)
    max_attempts = max(1, int(routing.get("max_stage_attempts", 3)))
    backoff = max(1, int(routing.get("retry_backoff_base_seconds", 2)))
    trace = f"{job_id}:{stamp_for_id()}"
    with RuntimeDB() as db:
        db.upsert_job(job_id, status="running", mode=("autonomous" if autonomous else "manual"), config=routing)
        db.ensure_approval(job_id)
        db.add_agent_message(job_id=job_id, trace_id=trace, source_agent="maestro", target_agent="all", message_type="cycle_start", body={"models": cfg.get("agent_models", {}), "routing": routing})
        try:
            candidates = _run_with_retry(
                db,
                job_id,
                "discover",
                max_attempts,
                backoff,
                lambda a: discover_candidates(
                    job_id,
                    cfg["institutions"],
                    routing,
                    historical_seeds=cfg.get("historical_seeds", []),
                    runtime_db=db,
                    trace_id=f"{trace}:discover",
                    attempt=a,
                ),
            )
            observations, quality = _run_with_retry(
                db,
                job_id,
                "capture",
                max_attempts,
                backoff,
                lambda a: capture_observations(
                    job_id,
                    candidates,
                    capture_timeout_seconds=int(routing.get("capture_timeout_seconds", 12)),
                    instagram_capture_mode=str(routing.get("instagram_capture_mode", "playwright_dismiss")),
                    instagram_dismiss_attempts=int(routing.get("instagram_dismiss_attempts", 3)),
                    instagram_dismiss_timeout_seconds=int(routing.get("instagram_dismiss_timeout_seconds", 2)),
                    runtime_db=db,
                    trace_id=f"{trace}:capture",
                    attempt=a,
                    block_institutional=bool(routing.get("block_institutional_sources", True)),
                ),
            )
            extracted = _run_with_retry(
                db,
                job_id,
                "extract",
                max_attempts,
                backoff,
                lambda a: extract_campaigns(
                    job_id,
                    candidates,
                    observations,
                    quality_by_obs=quality,
                    runtime_db=db,
                    trace_id=f"{trace}:extract",
                    attempt=a,
                    min_capture_quality_score=float(routing.get("min_capture_quality_score_extract", 0.55)),
                ),
            )
            validated = _run_with_retry(
                db,
                job_id,
                "validate",
                max_attempts,
                backoff,
                lambda a: validate_campaigns(
                    job_id,
                    extracted,
                    observations,
                    cfg["scoring"],
                    instagram_require_official_confirmation=bool(routing.get("instagram_require_official_confirmation", True)),
                    runtime_db=db,
                    trace_id=f"{trace}:validate",
                    attempt=a,
                ),
            )
            unique_campaigns, groups = dedupe_campaigns(validated)
            for camp in unique_campaigns:
                fp = _fingerprint(camp.institution_id, camp.source_url, camp.campaign_name)
                db.save_to_history(camp, job_id=job_id, fingerprint=fp)
            full_report_catalog, _report_merge = build_full_catalog_for_report(unique_campaigns)
            report_paths = _run_with_retry(
                db,
                job_id,
                "report",
                max_attempts,
                backoff,
                lambda a: generate_report(
                    job_id,
                    unique_campaigns,
                    cfg["report"],
                    full_catalog=full_report_catalog,
                    runtime_db=db,
                    trace_id=f"{trace}:report",
                    attempt=a,
                ),
            )
            email_sent = False
            if send_report_email:
                if _can_send(db, job_id):
                    to = recipients or cfg["email"].get("default_recipients", [])
                    subject = f"{cfg['report'].get('subject_prefix', 'Monitor diario')} - {job_id}"
                    send_email(html_path=str(report_paths["html"]), subject=subject, recipients=to, smtp_host=cfg["email"].get("smtp_host", "smtp.gmail.com"), smtp_port=int(cfg["email"].get("smtp_port", 587)), use_tls=bool(cfg["email"].get("use_tls", True)))
                    email_sent = True
                    db.log_run(job_id=job_id, stage="sender", attempt=1, status="success")
                else:
                    db.log_run(job_id=job_id, stage="sender", attempt=1, status="blocked", error_message="approval_status != approved")
            useful = sum(1 for q in quality.values() if q.capture_quality_score >= 0.6)
            instagram_blocked = sum(
                1
                for obs in observations
                if _is_instagram(obs.source_url) and (obs.instagram_block_reason is not None)
            )
            summary = {
                "job_id": job_id,
                "created_at": iso_now_tz(),
                "counts": {
                    "candidates": len(candidates),
                    "observations": len(observations),
                    "campaigns_validated": len(validated),
                    "campaigns_after_dedupe": len(unique_campaigns),
                    "campaigns_in_full_report": len(full_report_catalog),
                },
                "quality": {
                    "screenshot_useful_ratio": round((useful / len(quality)) if quality else 0.0, 3),
                    "blocked_observations": sum(1 for q in quality.values() if q.should_block),
                    "instagram_blocked_observations": instagram_blocked,
                },
                "dedupe_groups": groups,
                "report_files": {k: _to_rel(v) for k, v in report_paths.items()},
                "approval_status": db.get_approval_status(job_id),
                "email_sent": email_sent,
                "browser_binary": str(_find_browser()) if _find_browser() else None,
                "routing_used": routing,
            }
            write_json(constants.JOBS_DIR / f"{job_id}_summary.json", summary)
            db.set_job_status(job_id, "completed")
            db.add_agent_message(job_id=job_id, trace_id=trace, source_agent="maestro", target_agent="all", message_type="cycle_end", body={"status": "completed", "campaigns": len(unique_campaigns), "email_sent": email_sent})
            return ManualCycleResult(job_id=job_id, candidates=candidates, observations=observations, campaigns=unique_campaigns, dedupe_groups=groups, report_paths=report_paths, email_sent=email_sent)
        except Exception as exc:  # noqa: PERF203
            db.set_job_status(job_id, "failed", last_error=str(exc))
            db.add_dead_letter(job_id=job_id, stage="maestro", error_message=str(exc), payload={"trace_id": trace})
            raise


def run_manual_cycle(job_id: str, *, send_report_email: bool = False, recipients: list[str] | None = None, routing_overrides: dict[str, Any] | None = None) -> ManualCycleResult:
    return run_autonomous_cycle(job_id, send_report_email=send_report_email, recipients=recipients, routing_overrides=routing_overrides, autonomous=False)
