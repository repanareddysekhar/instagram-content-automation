import json
from typing import Any, Protocol

import httpx
from openai import OpenAI

from app.config import Settings
from app.schema import ContentDraft, Topic


CONTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "hook", "caption", "hashtags", "slides", "claims", "format"],
    "properties": {
        "title": {"type": "string"},
        "hook": {"type": "string"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}, "minItems": 3},
        "format": {"type": "string", "enum": ["carousel"]},
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
        self.http = http_client or httpx.Client(timeout=timeout)

    def generate(self, prompt: str, schema: dict[str, Any]) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        response = self.http.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "carousel_draft",
                        "strict": True,
                        "schema": schema,
                    },
                },
            },
        )
        response.raise_for_status()
        try:
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError
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

    def write(self, topic: Topic) -> ContentDraft:
        if self.settings.mock_mode:
            return self._demo_draft(topic)
        if not self.provider:
            raise RuntimeError(
                f"TEXT_PROVIDER={self.settings.text_provider} is not configured; "
                "add its API key/model settings or enable MOCK_MODE"
            )

        prompt = f"""
Role: You are the editorial lead for a credible technology education account.

Goal: Turn the source below into a concise, useful Instagram carousel.

Success criteria:
- 6 slides: hook, context, 3 useful insights, closing takeaway
- each slide body is at most 38 words
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
        raw = self.provider.generate(prompt, CONTENT_SCHEMA)
        return ContentDraft.model_validate(json.loads(raw))

    @staticmethod
    def _demo_draft(topic: Topic) -> ContentDraft:
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
                "format": "carousel",
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
