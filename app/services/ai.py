import json
from typing import Any

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


class ContentWriter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = OpenAI(api_key=settings.openai_api_key) if settings.openai_ready else None

    def write(self, topic: Topic) -> ContentDraft:
        if not self.client or self.settings.mock_mode:
            return self._demo_draft(topic)

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
        response = self.client.responses.create(
            model=self.settings.openai_text_model,
            reasoning={"effort": "low"},
            input=prompt,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "carousel_draft",
                    "strict": True,
                    "schema": CONTENT_SCHEMA,
                }
            },
        )
        return ContentDraft.model_validate(json.loads(response.output_text))

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

