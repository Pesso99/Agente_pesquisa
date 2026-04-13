from __future__ import annotations

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]

CONFIG_DIR = ROOT_DIR / "config"
SCHEMAS_DIR = ROOT_DIR / "schemas"
DATA_DIR = ROOT_DIR / "data"
PROMPTS_DIR = ROOT_DIR / "prompts"
SKILLS_DIR = ROOT_DIR / "skills"

CANDIDATES_DIR = DATA_DIR / "candidates"
OBSERVATIONS_DIR = DATA_DIR / "observations"
CAMPAIGNS_DIR = DATA_DIR / "campaigns"
REPORTS_DIR = DATA_DIR / "reports"
STATE_DIR = DATA_DIR / "state"
JOBS_DIR = DATA_DIR / "jobs"
LOGS_DIR = DATA_DIR / "logs"

ARTIFACTS_DIR = DATA_DIR / "artifacts"
SCREENSHOTS_DIR = ARTIFACTS_DIR / "screenshots"
RAW_HTML_DIR = ARTIFACTS_DIR / "raw_html"
RAW_TEXT_DIR = ARTIFACTS_DIR / "raw_text"
RUNTIME_DB_PATH = STATE_DIR / "runtime.db"

VALID_STATUSES = (
    "validated",
    "validated_with_reservations",
    "review",
    "discarded",
)

SOURCE_TYPES = (
    "official_site",
    "social_official",
    "search_result",
    "third_party",
)
