from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, TypeVar

from dotenv import load_dotenv
from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError
from pydantic import BaseModel

from app import constants
from app.io_utils import read_json

load_dotenv(constants.ROOT_DIR / ".env")

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_SKILLS_DIR = constants.ROOT_DIR / "skills"
_MODELS_PATH = constants.CONFIG_DIR / "agent_models.json"

_DEFAULT_MODEL = "gpt-5.4-mini-2026-03-17"
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 2
_RETRYABLE = (RateLimitError, APIConnectionError, APITimeoutError)


def _load_system_prompt(agent_name: str) -> str:
    skill_path = _SKILLS_DIR / agent_name / "SKILL.md"
    if not skill_path.exists():
        raise FileNotFoundError(f"System prompt not found: {skill_path}")
    return skill_path.read_text(encoding="utf-8").strip()


def _resolve_model(agent_name: str) -> str:
    if _MODELS_PATH.exists():
        try:
            models = read_json(_MODELS_PATH)
            entry = models.get(agent_name, {})
            raw = entry.get("model", "") if isinstance(entry, dict) else ""
            model_id = raw.removeprefix("openai/").strip()
            if model_id:
                return model_id
        except Exception:
            logger.warning("Failed to read agent_models.json, using default model")
    return _DEFAULT_MODEL


class AgentLLM:
    """Centralized OpenAI client for all pipeline agents."""

    def __init__(self) -> None:
        self.client = OpenAI()
        self._prompt_cache: dict[str, str] = {}
        self._model_cache: dict[str, str] = {}

    def _get_prompt(self, agent_name: str) -> str:
        if agent_name not in self._prompt_cache:
            self._prompt_cache[agent_name] = _load_system_prompt(agent_name)
        return self._prompt_cache[agent_name]

    def _get_model(self, agent_name: str) -> str:
        if agent_name not in self._model_cache:
            self._model_cache[agent_name] = _resolve_model(agent_name)
        return self._model_cache[agent_name]

    def call(
        self,
        agent_name: str,
        user_content: str,
        *,
        response_format: type[T] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        extra_system: str | None = None,
    ) -> str | T:
        system_prompt = self._get_prompt(agent_name)
        if extra_system:
            system_prompt = f"{system_prompt}\n\n{extra_system}"

        model = self._get_model(agent_name)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                if response_format is not None:
                    resp = self.client.beta.chat.completions.parse(
                        model=model,
                        messages=messages,
                        response_format=response_format,
                        temperature=temperature,
                        max_completion_tokens=max_tokens,
                    )
                    parsed = resp.choices[0].message.parsed
                    if parsed is None:
                        raise ValueError("LLM returned unparseable response")
                    usage = resp.usage
                    if usage:
                        logger.info(
                            "LLM [%s/%s] tokens in=%d out=%d",
                            agent_name,
                            model,
                            usage.prompt_tokens,
                            usage.completion_tokens,
                        )
                    return parsed
                else:
                    resp = self.client.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=temperature,
                        max_completion_tokens=max_tokens,
                    )
                    text = resp.choices[0].message.content or ""
                    usage = resp.usage
                    if usage:
                        logger.info(
                            "LLM [%s/%s] tokens in=%d out=%d",
                            agent_name,
                            model,
                            usage.prompt_tokens,
                            usage.completion_tokens,
                        )
                    return text

            except _RETRYABLE as exc:
                last_exc = exc
                wait = _RETRY_BACKOFF_BASE ** attempt
                logger.warning(
                    "LLM call %s attempt %d failed (%s), retrying in %ds",
                    agent_name,
                    attempt,
                    type(exc).__name__,
                    wait,
                )
                time.sleep(wait)

        raise RuntimeError(
            f"LLM call to {agent_name} failed after {_MAX_RETRIES} attempts"
        ) from last_exc

    def search(
        self,
        agent_name: str,
        query: str,
        *,
        search_context_size: str = "low",
        max_tokens: int = 1024,
    ) -> tuple[str, list[dict[str, str]]]:
        """Web search via Responses API. Returns (output_text, citations).

        Citations are extracted from both API annotations and URL patterns
        in the output text (the model may format URLs inline when given
        structured output instructions).
        """
        import re as _re

        model = self._get_model(agent_name)
        instructions = self._get_prompt(agent_name)

        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = self.client.responses.create(
                    model=model,
                    instructions=instructions,
                    input=query,
                    tools=[{"type": "web_search", "search_context_size": search_context_size}],
                    max_output_tokens=max_tokens,
                )
                seen_urls: set[str] = set()
                citations: list[dict[str, str]] = []

                for item in response.output:
                    if not hasattr(item, "content"):
                        continue
                    for block in item.content:
                        for ann in getattr(block, "annotations", []):
                            if getattr(ann, "type", "") == "url_citation":
                                url = getattr(ann, "url", "").split("?utm_source=")[0]
                                if url and url not in seen_urls:
                                    seen_urls.add(url)
                                    citations.append({
                                        "url": url,
                                        "title": getattr(ann, "title", "") or "",
                                    })

                text = response.output_text
                for match in _re.finditer(r"(https?://\S+)", text):
                    url = match.group(1).rstrip(".,;:)>|\"'")
                    url = url.split("?utm_source=")[0]
                    if url not in seen_urls:
                        seen_urls.add(url)
                        title = ""
                        line = text[max(0, match.start() - 5):text.find("\n", match.end()) + 1]
                        parts = line.split("|")
                        if len(parts) >= 2:
                            title = parts[1].strip()[:120]
                        citations.append({"url": url, "title": title})

                usage = getattr(response, "usage", None)
                if usage:
                    logger.info(
                        "LLM search [%s/%s] tokens in=%d out=%d",
                        agent_name,
                        model,
                        getattr(usage, "input_tokens", 0),
                        getattr(usage, "output_tokens", 0),
                    )
                return text, citations

            except _RETRYABLE as exc:
                last_exc = exc
                wait = _RETRY_BACKOFF_BASE ** attempt
                logger.warning(
                    "LLM search %s attempt %d failed (%s), retrying in %ds",
                    agent_name,
                    attempt,
                    type(exc).__name__,
                    wait,
                )
                time.sleep(wait)

        raise RuntimeError(
            f"LLM search call to {agent_name} failed after {_MAX_RETRIES} attempts"
        ) from last_exc

    def call_with_image(
        self,
        agent_name: str,
        text: str,
        image_path: Path,
        *,
        response_format: type[T] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> str | T:
        """Call LLM with an image attachment (for future screenshot analysis)."""
        import base64

        if not image_path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        mime = "image/png" if image_path.suffix == ".png" else "image/jpeg"
        system_prompt = self._get_prompt(agent_name)
        model = self._get_model(agent_name)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            },
        ]

        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                if response_format is not None:
                    resp = self.client.beta.chat.completions.parse(
                        model=model,
                        messages=messages,
                        response_format=response_format,
                        temperature=temperature,
                        max_completion_tokens=max_tokens,
                    )
                    parsed = resp.choices[0].message.parsed
                    if parsed is None:
                        raise ValueError("LLM returned unparseable response")
                    return parsed
                else:
                    resp = self.client.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=temperature,
                        max_completion_tokens=max_tokens,
                    )
                    return resp.choices[0].message.content or ""

            except _RETRYABLE as exc:
                last_exc = exc
                wait = _RETRY_BACKOFF_BASE ** attempt
                logger.warning(
                    "LLM vision call %s attempt %d failed (%s), retrying in %ds",
                    agent_name,
                    attempt,
                    type(exc).__name__,
                    wait,
                )
                time.sleep(wait)

        raise RuntimeError(
            f"LLM vision call to {agent_name} failed after {_MAX_RETRIES} attempts"
        ) from last_exc
