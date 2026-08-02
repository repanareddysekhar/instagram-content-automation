import json
import logging
from time import perf_counter
from typing import Any, Protocol

import httpx
from openai import OpenAI
from pydantic import ValidationError

from app.config import Settings
from app.schema import ContentDraft, Topic


logger = logging.getLogger("tech_content_agent.ai")


CONTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "hook", "caption", "hashtags", "slides", "claims", "format"],
    "properties": {
        "title": {"type": "string"},
        "hook": {"type": "string"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}, "minItems": 3},
        "format": {"type": "string", "enum": ["carousel", "reel"]},
        "voiceover": {"type": "string"},
        "slides": {
            "type": "array",
            "minItems": 5,
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["headline", "body", "visual_prompt"],
                "properties": {
                    "headline": {"type": "string"},
                    "body": {"type": "string"},
                    "visual_prompt": {"type": "string"},
                },
            },
        },
        "claims": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "source_url", "evidence", "verified"],
                "properties": {
                    "text": {"type": "string"},
                    "source_url": {"type": "string"},
                    "evidence": {"type": "string"},
                    "verified": {"type": "boolean"},
                },
            },
        },
    },
}


class TextProvider(Protocol):
    def generate(self, prompt: str, schema: dict[str, Any]) -> str: ...


class OpenAITextProvider:
    def __init__(self, api_key: str, model: str):
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def generate(self, prompt: str, schema: dict[str, Any]) -> str:
        response = self.client.responses.create(
            model=self.model,
            reasoning={"effort": "low"},
            input=prompt,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "carousel_draft",
                    "strict": True,
                    "schema": schema,
                }
            },
        )
        return response.output_text


class GeminiTextProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float,
        http_client: httpx.Client | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.http = http_client or httpx.Client(timeout=timeout)

    def generate(self, prompt: str, schema: dict[str, Any]) -> str:
        response = self.http.post(
            f"{self.base_url}/models/{self.model}:generateContent",
            headers={"x-goog-api-key": self.api_key},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseJsonSchema": schema,
                },
            },
        )
        response.raise_for_status()
        try:
            parts = response.json()["candidates"][0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts)
            if not text:
                raise KeyError
            return text
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Gemini returned no text content") from exc


class AnthropicTextProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float,
        http_client: httpx.Client | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.http = http_client or httpx.Client(timeout=timeout)

    def generate(self, prompt: str, schema: dict[str, Any]) -> str:
        response = self.http.post(
            f"{self.base_url}/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": self.model,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}],
                "output_config": {
                    "format": {
                        "type": "json_schema",
                        "schema": schema,
                    }
                },
            },
        )
        response.raise_for_status()
        try:
            blocks = response.json()["content"]
            text = "".join(
                block.get("text", "")
                for block in blocks
                if block.get("type") == "text"
            )
            if not text:
                raise KeyError
            return text
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Anthropic returned no text content") from exc


class OpenAICompatibleTextProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float,
        http_client: httpx.Client | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.is_openrouter = "openrouter.ai" in self.base_url
        self.http = http_client or httpx.Client(timeout=timeout)

    def generate(self, prompt: str, schema: dict[str, Any]) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "carousel_draft",
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        if self.is_openrouter:
            payload["provider"] = {"require_parameters": True}
            payload["plugins"] = [{"id": "response-healing"}]
        response = self.http.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        try:
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError
            usage = result.get("usage") or {}
            logger.info(
                "compatible.response routed_model=%s prompt_tokens=%s completion_tokens=%s",
                result.get("model", "unknown"),
                usage.get("prompt_tokens", "unknown"),
                usage.get("completion_tokens", "unknown"),
            )
            return content
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("The OpenAI-compatible provider returned no text content") from exc


def build_text_provider(settings: Settings) -> TextProvider | None:
    provider = settings.text_provider.lower()
    if provider == "openai" and settings.openai_ready:
        return OpenAITextProvider(settings.openai_api_key, settings.openai_text_model)
    if provider == "gemini" and settings.gemini_ready:
        return GeminiTextProvider(
            settings.gemini_api_key,
            settings.gemini_text_model,
            settings.gemini_base_url,
            settings.ai_request_timeout_seconds,
        )
    if provider == "anthropic" and settings.anthropic_ready:
        return AnthropicTextProvider(
            settings.anthropic_api_key,
            settings.anthropic_text_model,
            settings.anthropic_base_url,
            settings.ai_request_timeout_seconds,
        )
    if provider == "openai_compatible" and settings.openai_compatible_ready:
        return OpenAICompatibleTextProvider(
            settings.openai_compatible_api_key,
            settings.openai_compatible_text_model,
            settings.openai_compatible_base_url,
            settings.ai_request_timeout_seconds,
        )
    if provider not in {"openai", "gemini", "anthropic", "openai_compatible"}:
        raise ValueError(f"Unsupported TEXT_PROVIDER: {settings.text_provider}")
    return None


class ContentWriter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.provider = None if settings.mock_mode else build_text_provider(settings)

    def _model_name(self) -> str:
        return {
            "openai": self.settings.openai_text_model,
            "gemini": self.settings.gemini_text_model,
            "anthropic": self.settings.anthropic_text_model,
            "openai_compatible": self.settings.openai_compatible_text_model,
        }.get(self.settings.text_provider.lower(), "unknown")

    def write(self, topic: Topic, content_format: str = "carousel") -> ContentDraft:
        if content_format not in {"carousel", "reel"}:
            raise ValueError(f"Unsupported content format: {content_format}")
        if self.settings.mock_mode:
            logger.info("llm.bypass reason=mock_mode topic_url=%s", topic.url)
            return self._demo_draft(topic, content_format)
        if not self.provider:
            raise RuntimeError(
                f"TEXT_PROVIDER={self.settings.text_provider} is not configured; "
                "add its API key/model settings or enable MOCK_MODE"
            )

        prompt = f"""
Role: You are the editorial lead for a credible technology education account.

Goal: Turn the source below into a concise, useful Instagram {content_format}.

Success criteria:
- Return format exactly as "{content_format}"
- for a carousel, use 6 slides: hook, context, 3 useful insights, closing takeaway
- for a reel, use 5 concise insight beats; the renderer supplies the opening and closing cards
- each slide body is at most 38 words
- for a reel, make the hook an immediate curiosity-driven sentence of at most 12 words
  and make the final beat a clear follow/save call to action; keep the total suitable for a 20-second reel
- for a reel, the narration is spoken directly from the returned hook, headlines, and bodies:
  use at most 75 words across all of that text, with each headline at most 8 words and
  each body at most 9 words. Do not include source URLs in on-screen or spoken copy.
- no hype, invented metrics, dates, or capabilities
- every factual claim includes the supplied source URL and a short evidence excerpt
- the caption ends with a thoughtful question

Source:
Title: {topic.title}
Publisher: {topic.source_name}
URL: {topic.url}
Summary: {topic.summary}

Return only the required structured result.
"""
        total_started = perf_counter()
        attempts = max(1, self.settings.ai_generation_attempts)
        for attempt in range(1, attempts + 1):
            attempt_started = perf_counter()
            logger.info(
                "llm.request.start provider=%s model=%s attempt=%d/%d topic_url=%s",
                self.settings.text_provider,
                self._model_name(),
                attempt,
                attempts,
                topic.url,
            )
            try:
                raw = self.provider.generate(prompt, CONTENT_SCHEMA)
                draft = ContentDraft.model_validate(json.loads(raw))
                if draft.format != content_format:
                    raise ValueError(
                        f"Provider returned {draft.format}, expected {content_format}"
                    )
                if content_format == "reel":
                    self._validate_reel_script(draft)
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                logger.warning(
                    "llm.response.invalid provider=%s model=%s attempt=%d/%d error_type=%s",
                    self.settings.text_provider,
                    self._model_name(),
                    attempt,
                    attempts,
                    type(exc).__name__,
                )
                if attempt < attempts:
                    continue
                logger.error(
                    "llm.request.failed provider=%s model=%s duration_ms=%d",
                    self.settings.text_provider,
                    self._model_name(),
                    round((perf_counter() - total_started) * 1000),
                )
                raise
            except Exception:
                logger.exception(
                    "llm.request.failed provider=%s model=%s attempt=%d/%d duration_ms=%d",
                    self.settings.text_provider,
                    self._model_name(),
                    attempt,
                    attempts,
                    round((perf_counter() - attempt_started) * 1000),
                )
                raise
            logger.info(
                "llm.request.completed provider=%s model=%s attempt=%d/%d duration_ms=%d total_duration_ms=%d slides=%d claims=%d",
                self.settings.text_provider,
                self._model_name(),
                attempt,
                attempts,
                round((perf_counter() - attempt_started) * 1000),
                round((perf_counter() - total_started) * 1000),
                len(draft.slides),
                len(draft.claims),
            )
            return draft
        raise RuntimeError("Text generation exhausted without a result")

    @staticmethod
    def _validate_reel_script(draft: ContentDraft) -> None:
        words = draft.hook.split()
        for slide in draft.slides:
            if len(slide.headline.split()) > 8:
                raise ValueError("Reel headline exceeds eight words")
            if len(slide.body.split()) > 9:
                raise ValueError("Reel body exceeds nine words")
            words.extend(slide.headline.split())
            words.extend(slide.body.split())
        words.extend("Follow for practical source-grounded tech signals".split())
        if len(words) > 75:
            raise ValueError("Reel spoken script exceeds 75 words")

    @staticmethod
    def _demo_draft(topic: Topic, content_format: str) -> ContentDraft:
        return ContentDraft.model_validate(
            {
                "title": topic.title,
                "hook": "The smartest AI stack may not use the biggest model for every task.",
                "caption": (
                    "Production AI is becoming a routing problem: match each task to the "
                    "smallest model that clears your quality bar, then escalate the hard "
                    "cases. That can improve speed, cost control, and reliability. "
                    "Which task would you route first?"
                ),
                "hashtags": ["#AIEngineering", "#LLMOps", "#DeveloperTools", "#TechExplained"],
                "format": content_format,
                "voiceover": (
                    "The smartest AI stack does not use one giant model for everything. "
                    "Start with the smallest model that clears your quality bar for routine work. "
                    "Escalate ambiguous or high-stakes tasks only when they need more reasoning. "
                    "Measure accepted output, not just token price. Follow for practical, source-grounded tech signals."
                ),
                "slides": [
                    {
                        "headline": "Bigger isn’t always better",
                        "body": "The best production AI stack uses the right model for each job—not one model for everything.",
                        "visual_prompt": "A clean router splitting tasks into three model sizes.",
                    },
                    {
                        "headline": "Start with the workload",
                        "body": "Extraction, tagging, routing, and repetitive transformations often need consistency more than deep reasoning.",
                        "visual_prompt": "Four simple task cards flowing through a compact processor.",
                    },
                    {
                        "headline": "Escalate the hard cases",
                        "body": "Send ambiguous or high-stakes decisions to a stronger model while keeping routine traffic on a faster tier.",
                        "visual_prompt": "A decision gate directing one complex task upward.",
                    },
                    {
                        "headline": "Measure successful cost",
                        "body": "Compare cost per accepted output—not just token price. Retries and manual corrections change the real economics.",
                        "visual_prompt": "A balanced scorecard with quality, latency, and cost.",
                    },
                    {
                        "headline": "Keep an eval set",
                        "body": "Test model routes on representative examples before changing prompts, models, or reasoning settings.",
                        "visual_prompt": "A small checklist beside a model comparison chart.",
                    },
                    {
                        "headline": "Design the router first",
                        "body": "Use the smallest tier that passes your quality bar, then escalate only when the task requires it.",
                        "visual_prompt": "A final three-tier routing diagram with a clear upward path.",
                    },
                ],
                "claims": [
                    {
                        "text": "Production workflows can route different task classes to different model tiers.",
                        "source_url": topic.url,
                        "evidence": topic.summary or topic.title,
                        "verified": False,
                    }
                ],
            }
        )
