from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class Topic(BaseModel):
    title: str
    url: str
    summary: str = ""
    source_name: str
    published_at: str = ""
    tags: list[str] = Field(default_factory=list)
    score: float = 0


class Claim(BaseModel):
    text: str
    source_url: str
    evidence: str
    verified: bool = False


class Slide(BaseModel):
    headline: str
    body: str
    visual_prompt: str


class ContentDraft(BaseModel):
    title: str
    hook: str
    caption: str
    hashtags: list[str]
    slides: list[Slide]
    claims: list[Claim]
    format: Literal["carousel", "reel"] = "carousel"


class PipelineRequest(BaseModel):
    topic_url: HttpUrl | None = None
    force_demo: bool = False


class DecisionRequest(BaseModel):
    note: str = ""

