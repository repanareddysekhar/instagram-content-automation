import base64
import io
import logging
import re
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Protocol

import httpx
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont

from app.config import Settings


logger = logging.getLogger("tech_content_agent.assets")


PALETTES = [
    ("#F2FF63", "#10120E", "#D5DF38"),
    ("#A7F3D0", "#0C1713", "#5CCCA1"),
    ("#B8C6FF", "#11162A", "#778BDF"),
]


class ImageProvider(Protocol):
    def generate(self, prompt: str) -> Image.Image | None: ...


class OpenAIImageProvider:
    def __init__(self, api_key: str, model: str):
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def generate(self, prompt: str) -> Image.Image | None:
        result = self.client.images.generate(
            model=self.model,
            prompt=prompt,
            size="1024x1536",
            quality="low",
        )
        if not result.data or not result.data[0].b64_json:
            return None
        return Image.open(io.BytesIO(base64.b64decode(result.data[0].b64_json))).convert("RGB")


class GeminiImageProvider:
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

    def generate(self, prompt: str) -> Image.Image | None:
        response = self.http.post(
            f"{self.base_url}/models/{self.model}:generateContent",
            headers={"x-goog-api-key": self.api_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseModalities": ["IMAGE"],
                    "responseFormat": {
                        "image": {"aspectRatio": "4:5", "imageSize": "1K"}
                    },
                },
            },
        )
        response.raise_for_status()
        try:
            parts = response.json()["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Gemini returned no image content") from exc
        for part in parts:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return Image.open(io.BytesIO(base64.b64decode(inline["data"]))).convert("RGB")
        return None


def build_image_provider(settings: Settings) -> ImageProvider | None:
    if (
        settings.mock_mode
        or not settings.enable_ai_art
        or settings.image_provider.lower() == "none"
    ):
        return None
    provider = settings.image_provider.lower()
    if provider == "openai" and settings.openai_ready:
        return OpenAIImageProvider(settings.openai_api_key, settings.openai_image_model)
    if provider == "gemini" and settings.gemini_ready:
        return GeminiImageProvider(
            settings.gemini_api_key,
            settings.gemini_image_model,
            settings.gemini_image_base_url,
            settings.ai_request_timeout_seconds,
        )
    if provider not in {"openai", "gemini"}:
        raise ValueError(f"Unsupported IMAGE_PROVIDER: {settings.image_provider}")
    raise RuntimeError(
        f"IMAGE_PROVIDER={settings.image_provider} is not configured; add its API key"
    )


class CarouselRenderer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.output_dir = settings.generated_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.image_provider = build_image_provider(settings)

    def render(self, post_id: int, slides: list[dict], source_name: str) -> list[str]:
        provider_name = self.settings.image_provider if self.image_provider else "deterministic"
        logger.info(
            "assets.render.start post_id=%d slides=%d provider=%s",
            post_id,
            len(slides),
            provider_name,
        )
        paths = []
        for index, slide in enumerate(slides, start=1):
            path = self.output_dir / f"post-{post_id}-slide-{index}.jpg"
            background = self._generate_art(slide["visual_prompt"]) if self.image_provider else None
            self._render_slide(
                path=path,
                number=index,
                total=len(slides),
                headline=slide["headline"],
                body=slide["body"],
                source_name=source_name,
                art=background,
            )
            paths.append(str(path))
            logger.info(
                "assets.render.slide post_id=%d slide=%d ai_background=%s path=%s",
                post_id,
                index,
                background is not None,
                path,
            )
        logger.info(
            "assets.render.completed post_id=%d files=%d provider=%s",
            post_id,
            len(paths),
            provider_name,
        )
        return paths

    def _generate_art(self, prompt: str) -> Image.Image | None:
        if not self.image_provider:
            return None
        return self.image_provider.generate(
            (
                "Editorial technology illustration, bold geometric forms, high contrast, "
                "no words, no logos, ample negative space for text. " + prompt
            )
        )

    @staticmethod
    def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        ]
        for candidate in candidates:
            if Path(candidate).exists():
                return ImageFont.truetype(candidate, size)
        return ImageFont.load_default()

    def _render_slide(
        self,
        path: Path,
        number: int,
        total: int,
        headline: str,
        body: str,
        source_name: str,
        art: Image.Image | None,
    ) -> None:
        width, height = 1080, 1350
        bg, ink, accent = PALETTES[(number - 1) % len(PALETTES)]
        image = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(image)

        if art:
            art = art.resize((width, height))
            overlay = Image.new("RGB", (width, height), bg)
            image = Image.blend(art, overlay, 0.66)
            draw = ImageDraw.Draw(image)

        draw.rounded_rectangle((62, 55, 270, 118), radius=28, fill=ink)
        draw.text((92, 70), "TECH / SIGNAL", fill=bg, font=self._font(20, True))
        draw.text((902, 72), f"{number:02}/{total:02}", fill=ink, font=self._font(22, True))

        headline_font = self._font(82 if len(headline) < 32 else 68, True)
        body_font = self._font(39)
        headline_lines = textwrap.wrap(headline, width=19 if len(headline) < 45 else 23)
        body_lines = textwrap.wrap(body, width=40)

        y = 250
        for line in headline_lines:
            draw.text((72, y), line, fill=ink, font=headline_font)
            y += headline_font.size + 8 if hasattr(headline_font, "size") else 90

        y += 70
        draw.rounded_rectangle((72, y - 25, 1008, y + len(body_lines) * 57 + 45), 32, fill=ink)
        for line in body_lines:
            draw.text((112, y), line, fill=bg, font=body_font)
            y += 57

        draw.rectangle((72, 1190, 210, 1200), fill=accent)
        draw.text((72, 1230), f"SOURCE  {source_name.upper()[:45]}", fill=ink, font=self._font(19, True))
        image.save(path, "JPEG", quality=92, optimize=True)


class ReelRenderer:
    """Create a vertical MP4 from text-led editorial cards.

    The reel intentionally uses deterministic typography so factual copy remains
    readable. An optional, licensed audio track can be mixed in at encode time.
    """

    width = 1080
    height = 1920

    def __init__(self, settings: Settings):
        self.settings = settings
        self.output_dir = settings.generated_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def render(
        self,
        post_id: int,
        slides: list[dict],
        source_name: str,
        hook: str,
        voiceover: str = "",
    ) -> list[str]:
        if not slides:
            raise ValueError("A reel needs at least one content beat")
        logger.info(
            "reel.render.start post_id=%d beats=%d segment_seconds=%.2f",
            post_id,
            len(slides),
            self.settings.reel_segment_seconds,
        )
        cards = [
            {
                "headline": self._opening_hook(hook),
                "body": "Stay to the end for the practical takeaway.",
                "label": "THE HOOK",
            },
            *[
                {
                    "headline": slide["headline"],
                    "body": slide["body"],
                    "label": f"INSIGHT {index:02d}",
                }
                for index, slide in enumerate(slides, start=1)
            ],
            {
                "headline": "Follow for source-grounded tech signals",
                "body": "Save this for your next planning session.",
                "label": "THE TAKEAWAY",
            },
        ]
        frames: list[Path] = []
        for index, card in enumerate(cards, start=1):
            frame = self.output_dir / f"post-{post_id}-reel-frame-{index}.jpg"
            self._render_card(frame, index, len(cards), card, source_name)
            frames.append(frame)
        video_path = self.output_dir / f"post-{post_id}-reel.mp4"
        narration = self._create_voiceover(
            post_id,
            voiceover or self._fallback_voiceover(hook, slides),
        )
        narration_duration = self._duration(narration) if narration else 0.0
        self._encode(video_path, frames, narration, narration_duration)
        logger.info(
            "reel.render.completed post_id=%d path=%s frames=%d voiceover=%s music=%s",
            post_id,
            video_path,
            len(frames),
            bool(narration),
            bool(self.settings.reel_audio_path),
        )
        return [str(video_path)]

    @staticmethod
    def _opening_hook(hook: str) -> str:
        clean = re.split(r"\s*\(Source:", hook, maxsplit=1)[0].strip()
        words = clean.split()
        if len(words) <= 16:
            return clean
        return " ".join(words[:16]).rstrip(".,;:") + "…"

    @staticmethod
    def _fallback_voiceover(hook: str, slides: list[dict]) -> str:
        parts = [ReelRenderer._opening_hook(hook)]
        for slide in slides:
            parts.append(f"{slide['headline']}. {slide['body']}")
        parts.append("Follow for practical, source-grounded tech signals.")
        return " ".join(parts)

    def _create_voiceover(self, post_id: int, script: str) -> Path | None:
        if not self.settings.enable_reel_voiceover or not script.strip():
            return None
        say = shutil.which("say")
        if not say:
            logger.warning("reel.voiceover.skipped reason=say_not_available")
            return None
        narration = self.output_dir / f"post-{post_id}-narration.aiff"
        try:
            subprocess.run(
                [
                    say,
                    "-v",
                    self.settings.reel_voice,
                    "-r",
                    str(self.settings.reel_voice_rate),
                    "-o",
                    str(narration),
                    script.strip(),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            if self._duration(narration) <= 0.1:
                narration.unlink(missing_ok=True)
                logger.warning("reel.voiceover.skipped reason=empty_audio")
                return None
            return narration
        except subprocess.CalledProcessError as exc:
            logger.warning(
                "reel.voiceover.skipped reason=synthesis_failed stderr=%s",
                exc.stderr[-500:],
            )
            return None

    @staticmethod
    def _duration(audio_path: Path) -> float:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return 0.0
        try:
            result = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(audio_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            return float(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError):
            logger.warning("reel.voiceover.duration_unavailable")
            return 0.0

    def _render_card(
        self,
        path: Path,
        number: int,
        total: int,
        card: dict[str, str],
        source_name: str,
    ) -> None:
        bg, ink, accent = PALETTES[(number - 1) % len(PALETTES)]
        image = Image.new("RGB", (self.width, self.height), bg)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((62, 64, 384, 128), radius=28, fill=ink)
        draw.text((92, 79), "TECH / SIGNAL", fill=bg, font=CarouselRenderer._font(20, True))
        draw.text(
            (900, 82),
            f"{number:02}/{total:02}",
            fill=ink,
            font=CarouselRenderer._font(22, True),
        )
        draw.text((72, 290), card["label"], fill=ink, font=CarouselRenderer._font(22, True))

        headline = card["headline"]
        if len(headline) < 44:
            headline_size, line_width = 86, 18
        elif len(headline) < 110:
            headline_size, line_width = 68, 25
        else:
            headline_size, line_width = 56, 31
        headline_font = CarouselRenderer._font(headline_size, True)
        headline_lines = textwrap.wrap(headline, width=line_width)
        y = 390
        for line in headline_lines:
            draw.text((72, y), line, fill=ink, font=headline_font)
            y += getattr(headline_font, "size", headline_size) + 12

        body_lines = textwrap.wrap(card["body"], width=39)
        y += 80
        body_font = CarouselRenderer._font(42)
        panel_bottom = y + max(1, len(body_lines)) * 64 + 60
        draw.rounded_rectangle((72, y - 28, 1008, panel_bottom), radius=32, fill=ink)
        for line in body_lines[:6]:
            draw.text((112, y), line, fill=bg, font=body_font)
            y += 64

        draw.rectangle((72, 1725, 250, 1737), fill=accent)
        draw.text(
            (72, 1770),
            f"SOURCE  {source_name.upper()[:45]}",
            fill=ink,
            font=CarouselRenderer._font(19, True),
        )
        image.save(path, "JPEG", quality=92, optimize=True)

    def _encode(
        self,
        video_path: Path,
        frames: list[Path],
        narration: Path | None,
        narration_duration: float,
    ) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg is required to render reels but was not found")
        segment = max(1.0, self.settings.reel_segment_seconds)
        transition = min(max(0.0, self.settings.reel_transition_seconds), segment / 2)
        if narration_duration:
            segment = max(
                segment,
                (narration_duration + transition * (len(frames) - 1)) / len(frames),
            )
        command = [ffmpeg, "-y"]
        for frame in frames:
            command.extend(
                ["-loop", "1", "-framerate", "30", "-t", str(segment), "-i", str(frame)]
            )

        music_path = Path(self.settings.reel_audio_path) if self.settings.reel_audio_path else None
        include_music = bool(music_path and music_path.is_file())
        if self.settings.reel_audio_path and not include_music:
            logger.warning("reel.audio.skipped reason=file_not_found")
        if narration:
            command.extend(["-i", str(narration)])
        if include_music:
            command.extend(["-stream_loop", "-1", "-i", str(music_path)])

        filters = [
            f"[{index}:v]fps=30,scale={self.width}:{self.height},setsar=1,format=yuv420p[v{index}]"
            for index in range(len(frames))
        ]
        output = "v0"
        offset = segment - transition
        for index in range(1, len(frames)):
            next_output = f"x{index}"
            filters.append(
                f"[{output}][v{index}]xfade=transition=fade:duration={transition:.2f}:"
                f"offset={offset:.2f}[{next_output}]"
            )
            output = next_output
            offset += segment - transition
        audio_output: str | None = None
        narration_index = len(frames) if narration else None
        music_index = len(frames) + (1 if narration else 0) if include_music else None
        if narration_index is not None and music_index is not None:
            filters.extend(
                [
                    f"[{narration_index}:a]volume=1.0[voice]",
                    f"[{music_index}:a]volume=0.12[music]",
                    "[voice][music]amix=inputs=2:duration=first:dropout_transition=2[audio]",
                ]
            )
            audio_output = "[audio]"
        elif narration_index is not None:
            audio_output = f"{narration_index}:a:0"
        elif music_index is not None:
            audio_output = f"{music_index}:a:0"

        command.extend(["-filter_complex", ";".join(filters), "-map", f"[{output}]"])
        if audio_output:
            command.extend(["-map", audio_output, "-c:a", "aac", "-b:a", "160k", "-shortest"])
        command.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "21",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(video_path),
            ]
        )
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            logger.error("reel.encode.failed returncode=%s stderr=%s", exc.returncode, exc.stderr[-1000:])
            raise RuntimeError("ffmpeg could not encode the reel") from exc
