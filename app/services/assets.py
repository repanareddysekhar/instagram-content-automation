import base64
import io
import textwrap
from pathlib import Path

from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont

from app.config import Settings


PALETTES = [
    ("#F2FF63", "#10120E", "#D5DF38"),
    ("#A7F3D0", "#0C1713", "#5CCCA1"),
    ("#B8C6FF", "#11162A", "#778BDF"),
]


class CarouselRenderer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.output_dir = settings.generated_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.client = (
            OpenAI(api_key=settings.openai_api_key)
            if settings.openai_ready and settings.enable_ai_art
            else None
        )

    def render(self, post_id: int, slides: list[dict], source_name: str) -> list[str]:
        paths = []
        for index, slide in enumerate(slides, start=1):
            path = self.output_dir / f"post-{post_id}-slide-{index}.jpg"
            background = self._generate_art(slide["visual_prompt"]) if self.client else None
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
        return paths

    def _generate_art(self, prompt: str) -> Image.Image | None:
        result = self.client.images.generate(
            model=self.settings.openai_image_model,
            prompt=(
                "Editorial technology illustration, bold geometric forms, high contrast, "
                "no words, no logos, ample negative space for text. " + prompt
            ),
            size="1024x1536",
            quality="low",
        )
        if not result.data or not result.data[0].b64_json:
            return None
        return Image.open(io.BytesIO(base64.b64decode(result.data[0].b64_json))).convert("RGB")

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
