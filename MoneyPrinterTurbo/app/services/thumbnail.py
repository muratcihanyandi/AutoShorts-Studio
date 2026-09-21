import os
import re
from typing import Optional
import numpy as np
from loguru import logger
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageOps

from app.utils import utils

# Available fonts in priority order
PREFFERED_FONTS = [
    "BeVietnamPro-Bold.ttf",
    "MicrosoftYaHeiBold.ttc",
    "STHeitiMedium.ttc",
    "Charm-Bold.ttf",
]


def _turkish_upper(text: str) -> str:
    """Uppercases text while properly handling Turkish i -> İ and ı -> I."""
    tr_map = str.maketrans({"i": "İ", "ı": "I"})
    return text.translate(tr_map).upper()


def _get_font(font_size: int, is_cjk: bool = False) -> ImageFont.ImageFont:
    """Finds the best available bold font for thumbnail text."""
    font_folder = utils.font_dir()

    if is_cjk:
        order = ["MicrosoftYaHeiBold.ttc", "STHeitiMedium.ttc", "BeVietnamPro-Bold.ttf"]
    else:
        order = ["BeVietnamPro-Bold.ttf", "Charm-Bold.ttf", "MicrosoftYaHeiBold.ttc"]

    for font_name in order:
        font_path = os.path.join(font_folder, font_name)
        if os.path.exists(font_path):
            try:
                return ImageFont.truetype(font_path, font_size)
            except Exception:
                continue

    # Fallback to system fonts (Windows / Linux)
    sys_fonts = [
        "C:\\Windows\\Fonts\\impact.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
        "impact.ttf",
        "arialbd.ttf",
        "arial.ttf",
        "DejaVuSans-Bold.ttf",
    ]
    for sys_font in sys_fonts:
        try:
            return ImageFont.truetype(sys_font, font_size)
        except Exception:
            continue

    return ImageFont.load_default()


def _clean_title_for_thumbnail(
    title: str,
    max_words: int = 6,
    is_turkish: Optional[bool] = None,
) -> str:
    """Cleans and extracts a punchy phrase from video title for maximum CTR."""
    if not title:
        return ""
    # Strip hashtags like #shorts, #viral
    cleaned = re.sub(r"#\S+", "", title).strip()
    # Strip leading list indices like "1.", "1 -", "Top 5:", etc.
    cleaned = re.sub(
        r"^(?:\d+[\.\:\-\)]\s*|top\s*\d+\s*[\:\-]?\s*)",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    # Strip emojis and quotation/brackets
    cleaned = re.sub(r"[\"'\(\)\[\]\{\}<>]+", "", cleaned).strip()
    # Remove repeated punctuation
    cleaned = re.sub(r"[\!\?\.]{2,}", "!", cleaned).strip()

    words = cleaned.split()
    if len(words) > max_words:
        truncated = words[:max_words]
        # Strip trailing prepositions/conjunctions if cut mid-sentence
        trailing_stop_words = {
            "ve", "ile", "de", "da", "ki", "ama", "fakat", "için",
            "and", "or", "with", "the", "a", "an", "for", "to", "in", "on", "of", "by",
        }
        while len(truncated) > 3 and truncated[-1].lower() in trailing_stop_words:
            truncated.pop()
        cleaned = " ".join(truncated)

    if is_turkish is None:
        is_turkish = any(c in "çğıöşüÇĞİÖŞÜ" for c in title)

    if is_turkish:
        return _turkish_upper(cleaned)
    return cleaned.upper()


def _wrap_text(
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    draw: ImageDraw.ImageDraw,
) -> list[str]:
    """Wraps text into lines that fit within max_width."""
    words = text.split()
    if not words:
        return []

    lines = []
    current_line = []

    for word in words:
        test_line = " ".join(current_line + [word])
        bbox = draw.textbbox((0, 0), test_line, font=font)
        line_width = bbox[2] - bbox[0]
        if line_width <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(" ".join(current_line))
                current_line = [word]
            else:
                lines.append(word)
                current_line = []

    if current_line:
        lines.append(" ".join(current_line))

    return lines[:3]  # Maximum 3 lines on thumbnail for readability


def _score_frame(frame_arr: np.ndarray) -> float:
    """
    Scores a candidate video frame to find the sharpest, most vibrant,
    non-black, high-contrast frame.
    """
    try:
        h, w = frame_arr.shape[:2]
        step = max(1, min(h, w) // 240)
        sample = frame_arr[::step, ::step, :].astype(np.float32)

        # Luminance
        lum = 0.299 * sample[:, :, 0] + 0.587 * sample[:, :, 1] + 0.114 * sample[:, :, 2]
        mean_lum = float(np.mean(lum))
        lum_std = float(np.std(lum))

        # Sharpness / edge gradients
        dx = np.abs(lum[:, 1:] - lum[:, :-1])
        dy = np.abs(lum[1:, :] - lum[:-1, :])
        sharpness = float(np.mean(dx) + np.mean(dy))

        # Color saturation
        rgb_max = np.max(sample, axis=2)
        rgb_min = np.min(sample, axis=2)
        saturation = float(np.mean(rgb_max - rgb_min))

        score = (sharpness * 1.5) + (saturation * 1.2) + (lum_std * 0.8)

        # Severe penalties for black/dark opening frames or blown-out white frames
        if mean_lum < 40.0:
            score -= (40.0 - mean_lum) * 10.0
        elif mean_lum > 230.0:
            score -= (mean_lum - 230.0) * 10.0

        return score
    except Exception:
        return 0.0


def _select_best_frame(clip, duration: float) -> Image.Image:
    """
    Evaluates candidate timestamps across the video and extracts the most
    visually compelling frame, skipping black fade-ins and blur artifacts.
    """
    if duration <= 1.0:
        candidate_times = [max(0.0, duration * 0.5)]
    else:
        candidate_times = [
            duration * pct
            for pct in (0.15, 0.28, 0.42, 0.55, 0.68, 0.80)
            if 0.1 <= duration * pct <= max(0.1, duration - 0.2)
        ]
        if not candidate_times:
            candidate_times = [min(2.0, max(0.5, duration / 2.0))]

    best_frame = None
    best_score = float("-inf")

    for t in candidate_times:
        try:
            frame = clip.get_frame(t)
            score = _score_frame(frame)
            if score > best_score or best_frame is None:
                best_score = score
                best_frame = frame
        except Exception:
            continue

    if best_frame is None:
        fallback_t = min(2.0, max(0.2, duration / 3.0))
        best_frame = clip.get_frame(fallback_t)

    return Image.fromarray(best_frame)


def generate_thumbnail(
    video_path: str,
    title: str = "",
    output_path: Optional[str] = None,
    video_aspect: str = "9:16",
) -> Optional[str]:
    """
    Generates a high-CTR, algorithmically-optimized YouTube/Shorts thumbnail from video.

    Features:
    - Automatically checks for clean pre-subtitle source (combined-*.mp4) to avoid burnt-in text clutter.
    - Evaluates multiple candidate frames across the video and scores them for clarity, vibrancy, and contrast.
    - Sized accurately: 1280x720 for 16:9 landscape, 1080x1920 for 9:16 portrait/shorts.
    - Color and contrast enhanced (+20% saturation, +15% contrast, +15% sharpness).
    - Safe-zone positioning avoiding YouTube time badges and Shorts player UI buttons.
    - High-contrast pill container with electric yellow hook line and crisp white body.
    """
    if not video_path or not os.path.exists(video_path):
        logger.error(f"Cannot generate thumbnail: Video not found at {video_path}")
        return None

    # 1. Clean footage detection:
    # If final-*.mp4 is provided, check if combined-*.mp4 exists in the same folder.
    # combined-*.mp4 has the identical visuals WITHOUT burnt-in subtitles!
    source_video = video_path
    video_dir = os.path.dirname(os.path.abspath(video_path))
    video_base = os.path.basename(video_path)
    if "final-" in video_base:
        clean_name = video_base.replace("final-", "combined-")
        clean_candidate = os.path.join(video_dir, clean_name)
        if os.path.exists(clean_candidate):
            logger.info(f"Using clean source without subtitles for thumbnail: {clean_candidate}")
            source_video = clean_candidate

    try:
        from moviepy import VideoFileClip

        with VideoFileClip(source_video) as clip:
            duration = clip.duration or 3.0
            img = _select_best_frame(clip, duration)

        # Aspect Ratio Resolution Sizing
        is_landscape = "16:9" in str(video_aspect) or "landscape" in str(video_aspect).lower()
        target_size = (1280, 720) if is_landscape else (1080, 1920)

        # Resize with high quality Lanczos filter
        img = ImageOps.fit(img, target_size, method=Image.Resampling.LANCZOS)

        # Enhance visuals for high CTR (Vibrant colors, sharp contrast)
        img = ImageEnhance.Color(img).enhance(1.20)
        img = ImageEnhance.Contrast(img).enhance(1.15)
        img = ImageEnhance.Sharpness(img).enhance(1.15)

        # Add High-CTR Text Overlay
        thumb_text = _clean_title_for_thumbnail(title)
        if thumb_text:
            is_cjk = any("\u4e00" <= c <= "\u9fff" for c in thumb_text)
            draw = ImageDraw.Draw(img, "RGBA")
            base_width, base_height = target_size

            font_size = int(base_width * 0.058) if is_landscape else int(base_width * 0.078)
            font = _get_font(font_size, is_cjk=is_cjk)

            max_text_width = int(base_width * 0.82)
            lines = _wrap_text(thumb_text, font, max_text_width, draw)

            if lines:
                line_heights = []
                line_widths = []
                for line in lines:
                    bbox = draw.textbbox((0, 0), line, font=font)
                    line_widths.append(bbox[2] - bbox[0])
                    line_heights.append(bbox[3] - bbox[1])

                line_spacing = int(font_size * 0.32)

                # Safe zone positioning:
                # 16:9 (Landscape): Upper-middle (y: 20-25%), safe from bottom duration badge
                # 9:16 (Shorts): Middle-upper (y: 28-32%), safe from Shorts controls and title
                start_y = int(base_height * 0.20) if is_landscape else int(base_height * 0.28)

                padding_x = int(font_size * 0.42)
                padding_y = int(font_size * 0.28)

                current_y = start_y
                for i, line in enumerate(lines):
                    w = line_widths[i]
                    h = line_heights[i]
                    x = (base_width - w) // 2

                    # Modern pill container with subtle white highlight border
                    box_rect = [
                        x - padding_x,
                        current_y - padding_y,
                        x + w + padding_x,
                        current_y + h + padding_y,
                    ]
                    # Semi-transparent dark background
                    draw.rounded_rectangle(
                        box_rect,
                        radius=14,
                        fill=(12, 12, 12, 210),
                        outline=(255, 255, 255, 50),
                        width=2,
                    )

                    # First line is electric yellow (#FFE600), remaining lines are pure white
                    text_color = (255, 230, 0, 255) if i == 0 else (255, 255, 255, 255)
                    stroke_color = (0, 0, 0, 255)
                    stroke_width = max(3, font_size // 16)

                    draw.text(
                        (x, current_y),
                        line,
                        font=font,
                        fill=text_color,
                        stroke_fill=stroke_color,
                        stroke_width=stroke_width,
                    )
                    current_y += h + line_spacing

        # Determine target output path (based on original video_path)
        if not output_path:
            orig_dir = os.path.dirname(os.path.abspath(video_path))
            orig_name = os.path.splitext(os.path.basename(video_path))[0]
            output_path = os.path.join(orig_dir, f"{orig_name}_thumbnail.jpg")

        # Convert back to RGB and save JPEG (ensuring strictly under 2MB YouTube limit)
        final_img = img.convert("RGB")
        final_img.save(output_path, format="JPEG", quality=94)

        if os.path.exists(output_path) and os.path.getsize(output_path) > 1.95 * 1024 * 1024:
            final_img.save(output_path, format="JPEG", quality=85)

        logger.success(f"Generated high-CTR thumbnail at: {output_path}")
        return output_path

    except Exception as e:
        logger.exception(f"Failed to generate thumbnail for {video_path}: {e}")
        return None
