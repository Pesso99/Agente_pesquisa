from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Artifact(BaseModel):
    type: str
    path: str


class Candidate(BaseModel):
    candidate_id: str
    institution_id: str
    source_type: Literal["official_site", "social_official", "search_result", "third_party"]
    source_url: str
    headline: str
    discovered_at: str
    confidence_initial: float = Field(ge=0, le=1)
    summary: str | None = None
    notes: str | None = None


class Observation(BaseModel):
    observation_id: str
    candidate_id: str
    captured_at: str
    source_url: str
    visible_claims: list[str]
    artifacts: list[Artifact]
    page_title: str | None = None
    raw_html_path: str | None = None
    raw_text_path: str | None = None
    instagram_modal_dismissed: bool | None = None
    instagram_block_reason: str | None = None


class Campaign(BaseModel):
    campaign_id: str
    institution_id: str
    campaign_name: str
    campaign_type: str
    source_url: str
    status: Literal["validated", "validated_with_reservations", "review", "discarded"]
    confidence_final: float = Field(ge=0, le=1)
    evidence_refs: list[str]
    benefit: str | None = None
    audience: str | None = None
    source_type: str | None = None
    regulation_url: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    validation_notes: str | None = None
    channels: list[str] = Field(default_factory=list)
    history_match_id: str | None = None


class ReportSection(BaseModel):
    title: str
    items: list[dict]


class Report(BaseModel):
    report_id: str
    generated_at: str
    summary: str
    sections: list[ReportSection]


# --- LLM structured output models ---


class ExtractionResult(BaseModel):
    """Structured output from the extract agent LLM call."""

    is_campaign: bool
    campaign_name: str | None = None
    campaign_type: str | None = None
    benefit: str | None = None
    audience: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    regulation_url: str | None = None
    confidence_reasoning: str


class ValidationVerdict(BaseModel):
    """Structured output from a single validator LLM call."""

    status: Literal["validated", "validated_with_reservations", "review", "discarded"]
    confidence: float = Field(ge=0, le=1)
    reasoning: str
    concerns: list[str]


class PageClassification(BaseModel):
    """Structured output from the quality gate LLM call."""

    label: Literal[
        "campaign_like",
        "institutional",
        "login_wall",
        "error_page",
        "blank_or_broken",
    ]
    reasoning: str


class ScreenshotAnalysis(BaseModel):
    """Structured output from the screenshot analyst LLM vision call."""

    has_promotional_content: bool
    visual_confidence: float = Field(ge=0, le=1)
    visual_elements_found: list[str]
    page_type_visual: Literal[
        "promotional",
        "institutional",
        "login",
        "error",
        "blocked",
        "mixed",
    ]
    reasoning: str


class WebSearchCandidate(BaseModel):
    """Structured representation of a web search discovery result."""

    url: str
    title: str
    snippet: str = ""
    confidence: float = Field(ge=0, le=1)
    source_type: Literal["official_site", "social_official", "search_result", "third_party"]
    is_instagram_post: bool = False


# --- Pipeline infrastructure models ---


class Handoff(BaseModel):
    job_id: str
    trace_id: str
    task: str
    source_agent: str
    target_agent: str
    input_refs: list[str]
    created_at: str
    attempt: int = Field(ge=1)
    source_quality_label: str
    capture_quality_score: float = Field(ge=0, le=1)
    blocking_reasons: list[str] = Field(default_factory=list)
    instagram_modal_dismissed: bool | None = None
    instagram_block_reason: str | None = None
    priority: str | None = None
    notes: str | None = None
