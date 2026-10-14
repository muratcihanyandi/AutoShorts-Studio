import os
import math
import subprocess
from typing import Optional, List
from loguru import logger

from app.utils import utils
from app.config import config
from app.models.clip_schema import (
    ClipItem, ClipPipelineSettings, ClipStatus, ClipTargetFormat, ClipSourceKind
)
from app.services.clip_db import clip_db

try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None


def _hex_to_ass_color(hex_color: str) -> str:
    """#RRGGBB hex rengini ASS &H00BBGGRR formatina cevirir."""
    value = (hex_color or "").strip().lstrip("#")
    if len(value) == 6:
        try:
            r, g, b = value[0:2], value[2:4], value[4:6]
            return f"&H00{b}{g}{r}".upper()
        except Exception:
            pass
    return "&H0000D7FF"  # varsayilan altin


class ClipEditorService:
    def __init__(self):
        base_dir = os.path.dirname(utils.root_dir())
        ffmpeg_bin = os.path.join(base_dir, "lib", "ffmpeg", "ffmpeg-7.0-essentials_build", "ffmpeg.exe")
        if not os.path.exists(ffmpeg_bin):
            ffmpeg_bin = "ffmpeg"
        self.ffmpeg_bin = ffmpeg_bin

        self.ready_dir = os.path.join(utils.root_dir(), "storage", "clips", "ready")
        self.temp_dir = os.path.join(utils.root_dir(), "storage", "clips", "temp")
        os.makedirs(self.ready_dir, exist_ok=True)
        os.makedirs(self.temp_dir, exist_ok=True)

        self._whisper_model = None

    def _get_whisper_model(self):
        if self._whisper_model is not None:
            return self._whisper_model
        if WhisperModel is None:
            logger.warning("faster_whisper not available")
            return None

        whisper_cfg = getattr(config, "whisper", {}) if hasattr(config, "whisper") else {}
        model_size = whisper_cfg.get("model_size", "base")
        device = whisper_cfg.get("device", "cpu")
        compute_type = whisper_cfg.get("compute_type", "int8")

        # Yerel model klasoru kontrolu
        local_model_path = os.path.join(utils.root_dir(), "models", f"whisper-{model_size}")
        if os.path.isdir(local_model_path):
            model_target = local_model_path
        else:
            model_target = model_size

        try:
            logger.info(f"Loading faster-whisper model: {model_target} (device={device})")
            self._whisper_model = WhisperModel(model_target, device=device, compute_type=compute_type)
        except Exception as e:
            logger.warning(f"Could not load whisper model '{model_target}', trying 'base' on cpu: {e}")
            try:
                self._whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
            except Exception as ex2:
                logger.error(f"Failed to initialize whisper fallback: {ex2}")
                return None
        return self._whisper_model

    def extract_audio(self, video_path: str, out_audio_path: str) -> bool:
        """Videodan 16kHz mono WAV ses cikarir (Whisper icin ideal format)."""
        cmd = [
            self.ffmpeg_bin, "-y",
            "-i", video_path,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            out_audio_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        return res.returncode == 0 and os.path.exists(out_audio_path)

    def generate_animated_ass_subtitles(
        self,
        audio_path: str,
        out_ass_path: str,
        highlight_color: str = "#FFD700"
    ) -> Optional[str]:
        """
        faster-whisper ile kelime bazli zaman damgalari cikarir ve
        TikTok/Shorts tarzinda aktif kelime vurgulu .ass dosyasi uretir.
        """
        model = self._get_whisper_model()
        if not model:
            logger.warning("Whisper model unavailable, skipping subtitles")
            return None

        try:
            segments, info = model.transcribe(audio_path, word_timestamps=True, vad_filter=True)
            segments = list(segments)
        except Exception as e:
            logger.error(f"Whisper transcription error: {e}")
            return None

        if not segments:
            logger.info("No speech detected in audio")
            return None

        highlight_ass = _hex_to_ass_color(highlight_color)
        white_ass = "&H00FFFFFF"

        # ASS Altyazi Dosyasi Basligi (9:16 - 1080x1920 cozunurluk)
        # Font: Arial / Microsoft YaHei, vurgu rengi sarimsi altin, kalin siyah outline
        ass_content = [
            "[Script Info]",
            "ScriptType: v4.00+",
            "PlayResX: 1080",
            "PlayResY: 1920",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            # Alignment=2 (Alt Orta), MarginV=450 (Shorts dugmelerinin ustunde guvenli bolge), Fontsize=58
            "Style: Default,Arial,58,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,80,80,450,1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
        ]

        def format_time(seconds: float) -> str:
            hrs = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            csecs = int((seconds - int(seconds)) * 100)
            return f"{hrs:d}:{mins:02d}:{secs:02d}.{csecs:02d}"

        full_transcript = []
        for segment in segments:
            full_transcript.append(segment.text.strip())
            words = getattr(segment, "words", None)

            if words and len(words) > 0:
                # Kelimeleri 4'luk kucuk gruplar halinde ekrana bas;
                # her kelime suresince o kelime vurgu rengiyle isiklanir
                group_size = 4
                for i in range(0, len(words), group_size):
                    chunk = words[i:i + group_size]
                    chunk_end = chunk[-1].end

                    for k, word in enumerate(chunk):
                        ev_start = word.start
                        # Sonraki kelimeye kadar goster (kelimeler arasi boslukta da gorunsun)
                        ev_end = chunk[k + 1].start if k + 1 < len(chunk) else chunk_end
                        if ev_end <= ev_start:
                            ev_end = min(ev_start + 0.2, chunk_end)
                        if ev_end <= ev_start:
                            continue

                        text_parts = []
                        for j, w in enumerate(chunk):
                            txt = w.word.strip()
                            if not txt:
                                continue
                            if j == k:
                                text_parts.append(rf"{{\c{highlight_ass}}}{txt}{{\c{white_ass}}}")
                            else:
                                text_parts.append(txt)

                        ass_content.append(
                            f"Dialogue: 0,{format_time(ev_start)},{format_time(ev_end)},Default,,0,0,0,,{' '.join(text_parts)}"
                        )
            else:
                # Kelime ayrilamadiysa segment bazinda yaz
                start_str = format_time(segment.start)
                end_str = format_time(segment.end)
                ass_content.append(
                    f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{segment.text.strip()}"
                )

        with open(out_ass_path, "w", encoding="utf-8") as f:
            f.write("\n".join(ass_content))

        logger.info(f"Generated ASS subtitles with {len(segments)} segments: {out_ass_path}")
        return " ".join(full_transcript)

    # ================= OpenShorts Kanca (Hook) Görseli =================

    def generate_hook_overlay_image(
        self,
        text: str,
        out_png_path: str,
        width: int = 960,
        height: int = 150,
        bg_color=(20, 20, 26, 230),
        border_color=(255, 215, 0, 220),
        text_color=(255, 255, 255, 255),
        border_width: int = 3,
        corner_radius: int = 24
    ) -> Optional[str]:
        """
        OpenShorts stili modern ve dikkat cekici bir kanca (hook) baslik kutusu (sticker) uretir.
        Shorts'un ust kisminda (guvenli bolgede) gosterilir.
        """
        if not text or not text.strip():
            return None

        try:
            from PIL import Image, ImageDraw, ImageFont

            img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            # 1. Yuvarlatilmis dikdortgen kutu (Pill / Badge)
            draw.rounded_rectangle(
                [(border_width, border_width), (width - border_width, height - border_width)],
                radius=corner_radius,
                fill=bg_color,
                outline=border_color,
                width=border_width
            )

            # 2. Font secimi (Windows Segoe UI, Arial veya varsayilan)
            font = None
            font_candidates = [
                "C:\\Windows\\Fonts\\segoeuib.ttf",
                "C:\\Windows\\Fonts\\arialbd.ttf",
                "C:\\Windows\\Fonts\\tahoma.ttf",
                "Arial.ttf"
            ]
            font_size = 42
            if len(text) > 35:
                font_size = 36
            if len(text) > 55:
                font_size = 28

            for fc in font_candidates:
                if os.path.exists(fc):
                    try:
                        font = ImageFont.truetype(fc, font_size)
                        break
                    except Exception:
                        pass

            if font is None:
                try:
                    font = ImageFont.load_default()
                except Exception:
                    pass

            # 3. Metni ortalayarak ciz (gerekirse iki satira bol)
            clean_text = text.strip()
            if len(clean_text) > 30 and " " in clean_text:
                words = clean_text.split()
                mid = len(words) // 2
                lines = [" ".join(words[:mid]), " ".join(words[mid:])]
            else:
                lines = [clean_text]

            line_height = font_size + 8
            total_text_h = len(lines) * line_height
            cur_y = max(10.0, (height - total_text_h) / 2.0)

            for line in lines:
                if font:
                    bbox = draw.textbbox((0, 0), line, font=font)
                    line_w = bbox[2] - bbox[0]
                else:
                    line_w = len(line) * 10
                cur_x = max(10.0, (width - line_w) / 2.0)
                draw.text((cur_x, cur_y), line, fill=text_color, font=font)
                cur_y += line_height

            img.save(out_png_path, format="PNG")
            logger.info(f"Generated hook overlay image: {out_png_path}")
            return out_png_path
        except Exception as e:
            logger.warning(f"Failed to generate hook overlay image: {e}")
            return None

    # ================= Shorts (9:16) Kurgusu =================

    def process_clip_to_shorts(
        self,
        clip: ClipItem,
        settings: Optional[ClipPipelineSettings] = None,
        cut_start_sec: float = 0.0,
        cut_end_sec: float = 0.0
    ) -> str:
        """
        Klibi 9:16 Shorts formatina cevirir, istenen araligi keser,
        OpenShorts akilli yayinci yerlesimi veya blur_bg uygular,
        hook overlay ve faster-whisper altyazisini ekler ve nihai MP4 dosyasini uretir.
        """
        if not clip.raw_video_path or not os.path.exists(clip.raw_video_path):
            raise FileNotFoundError(f"Raw video path not found: {clip.raw_video_path}")

        if settings is None:
            settings = clip_db.get_settings()

        clip_db.update_clip_status(clip.clip_id, ClipStatus.EDITING)

        out_shorts_path = os.path.join(self.ready_dir, f"{clip.clip_id}_shorts.mp4")
        temp_audio_path = os.path.join(self.temp_dir, f"{clip.clip_id}_audio.wav")
        temp_ass_path = os.path.join(self.temp_dir, f"{clip.clip_id}_sub.ass")
        temp_hook_path = os.path.join(self.temp_dir, f"{clip.clip_id}_hook.png")

        # 1. Kesme parametreleri
        has_cut = cut_end_sec > cut_start_sec and (cut_end_sec - cut_start_sec) >= 5.0
        time_params = []
        if has_cut:
            duration = cut_end_sec - cut_start_sec
            time_params = ["-ss", str(cut_start_sec), "-t", str(duration)]
            logger.info(f"Applying cut: {cut_start_sec:.1f}s - {cut_end_sec:.1f}s (duration: {duration:.1f}s)")

        # 2. Altyazi uretimi (opsiyonel)
        transcript_text = ""
        ass_available = False
        if settings.subtitle_enabled:
            # Once videodan/kesitten sesi cikar
            extract_cmd = [self.ffmpeg_bin, "-y"]
            if has_cut:
                extract_cmd.extend(time_params)
            extract_cmd.extend([
                "-i", clip.raw_video_path,
                "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                temp_audio_path
            ])
            subprocess.run(extract_cmd, capture_output=True, text=True, errors="replace")

            if os.path.exists(temp_audio_path):
                transcript = self.generate_animated_ass_subtitles(
                    temp_audio_path,
                    temp_ass_path,
                    highlight_color=settings.subtitle_color
                )
                if transcript and os.path.exists(temp_ass_path):
                    transcript_text = transcript
                    ass_available = True

        # 3. Kanca (Hook) Görseli Hazırlama (OpenShorts Hook Overlay)
        hook_available = False
        hook_text = clip.hook_text
        if settings.hook_overlay_enabled and hook_text:
            generated_hook = self.generate_hook_overlay_image(hook_text, temp_hook_path)
            if generated_hook and os.path.exists(temp_hook_path):
                hook_available = True

        # 4. 9:16 Layout ve Altyazi Birlestirme Filtresi (FFmpeg Filter Complex)
        escaped_ass_path = temp_ass_path.replace("\\", "/").replace(":", "\\:")

        layout = settings.video_layout or "streamer_facecam"

        if layout == "streamer_facecam":
            # OpenShorts Akilli Yayinci/Oyun Duzeni (Facecam + Gameplay)
            # Uste yayinci kamerasi (1080x780), alta ana oyun/ekran (1080x1140)
            face_x = "0"
            face_y = "ih*0.52"
            pos = getattr(settings, "facecam_position", "bottom_left") or "bottom_left"
            if pos == "bottom_right":
                face_x = "iw*0.64"
                face_y = "ih*0.52"
            elif pos == "top_left":
                face_x = "0"
                face_y = "0"
            elif pos == "top_right":
                face_x = "iw*0.64"
                face_y = "0"

            filter_str = (
                f"[0:v]crop=w=iw*0.36:h=ih*0.48:x={face_x}:y={face_y},"
                f"scale=1080:780:force_original_aspect_ratio=increase,crop=1080:780[top_face];"
                f"[0:v]scale=1080:1140:force_original_aspect_ratio=increase,crop=1080:1140[bot_game];"
                f"[top_face][bot_game]vstack=inputs=2[base]"
            )
            final_v = "[base]"
        elif layout == "blur_bg":
            # 9:16 Arka plan bulanik + Ortada orijinal 16:9 keskin video
            filter_str = (
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:5[bg];"
                "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"
            )
            final_v = "[base]"
        elif layout == "center_crop":
            # Merkeze odaklanip dikey kirpma
            filter_str = (
                "[0:v]scale=-1:1920,crop=1080:1920[base]"
            )
            final_v = "[base]"
        else: # split screen (esit iki parca)
            filter_str = (
                "[0:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960[top];"
                "[0:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960[bot];"
                "[top][bot]vstack=inputs=2[base]"
            )
            final_v = "[base]"

        # Kanca çıkartması overlay (videonun ilk 7 saniyesinde tepe ortada göster)
        if hook_available:
            filter_str += f";{final_v}[1:v]overlay=(W-w)/2:120:enable='between(t,0,7)'[with_hook]"
            final_v = "[with_hook]"

        # Altyazı bindirme
        if ass_available:
            filter_str += f";{final_v}ass='{escaped_ass_path}'[outv]"
            map_v = "[outv]"
        else:
            filter_str += f";{final_v}copy[outv]"
            map_v = "[outv]"

        cmd = [self.ffmpeg_bin, "-y"]
        if has_cut:
            cmd.extend(time_params)
        cmd.extend(["-i", clip.raw_video_path])
        if hook_available:
            cmd.extend(["-i", temp_hook_path])

        cmd.extend([
            "-filter_complex", filter_str,
            "-map", map_v,
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "22",
            "-c:a", "aac",
            "-b:a", "192k",
            out_shorts_path
        ])

        logger.info(f"Rendering 9:16 Shorts with FFmpeg to {out_shorts_path} (layout={layout}, hook={hook_available})...")
        res = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=600)

        if res.returncode != 0:
            err = f"FFmpeg render error: {res.stderr}"
            logger.error(err)
            clip_db.update_clip_status(clip.clip_id, ClipStatus.FAILED, error_message=err)
            raise RuntimeError(err)

        if not os.path.exists(out_shorts_path) or os.path.getsize(out_shorts_path) == 0:
            raise RuntimeError(f"Rendered video missing or empty: {out_shorts_path}")

        # Temizlik
        for tmp in [temp_audio_path, temp_ass_path, temp_hook_path]:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass

        clip.processed_video_path = out_shorts_path
        clip.status = ClipStatus.READY
        clip_db.update_clip_status(
            clip.clip_id,
            ClipStatus.READY,
            processed_video_path=out_shorts_path,
            cut_start_sec=cut_start_sec,
            cut_end_sec=cut_end_sec,
            transcript=transcript_text if transcript_text else None,
            hook_text=clip.hook_text
        )
        logger.info(f"9:16 Shorts successfully created: {out_shorts_path} ({os.path.getsize(out_shorts_path)} bytes)")
        return out_shorts_path

    # ================= Yatay (16:9) Kurgusu =================

    def _render_horizontal_segment(
        self,
        raw_path: str,
        out_path: str,
        start_sec: float,
        duration_sec: float,
        reencode: bool
    ) -> str:
        """
        Yatay videonun bir bolumunu kesip kodlar.
        Altyazi eklenmez, en-boy orani degistirilmez (kullanicinin tercihi).
        reencode=False yalnizca hic kesme yoksa (hizli remux) kullanilmalidir.
        """
        cmd = [self.ffmpeg_bin, "-y"]
        if start_sec > 0:
            cmd.extend(["-ss", str(start_sec)])
        if duration_sec > 0:
            cmd.extend(["-t", str(duration_sec)])

        if reencode:
            # Kesme varsa yeniden kodla (senkron ve kare hizalamasi icin)
            cmd.extend([
                "-i", raw_path,
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "22",
                "-c:a", "aac",
                "-b:a", "192k",
                out_path
            ])
            timeout = int(max(600, duration_sec * 30))
        else:
            # Kesme yoksa hizli remux (yeniden kodlama gereksiz)
            cmd.extend([
                "-i", raw_path,
                "-c", "copy",
                "-movflags", "+faststart",
                out_path
            ])
            timeout = 600

        logger.info(f"Rendering horizontal segment to {out_path}...")
        res = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)

        if res.returncode != 0:
            err = f"FFmpeg horizontal render error: {res.stderr}"
            logger.error(err)
            raise RuntimeError(err)
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError(f"Rendered video missing or empty: {out_path}")
        return out_path

    def process_clip_horizontal(
        self,
        clip: ClipItem,
        settings: Optional[ClipPipelineSettings] = None,
        cut_start_sec: float = 0.0,
        cut_end_sec: float = 0.0
    ) -> List[ClipItem]:
        """
        Klibi yatay (16:9) video olarak kurgular: intro/outro kirpilir,
        govde oldugu gibi korunur. Uzun VOD'lar parcalara bolunur.

        Donus: READY durumdaki yayinlanabilir ogeler.
        - Kisa/tek parcali icerik -> [parent]
        - Parcalanan VOD -> parent (SPLIT) + parca ogeleri
        """
        if not clip.raw_video_path or not os.path.exists(clip.raw_video_path):
            raise FileNotFoundError(f"Raw video path not found: {clip.raw_video_path}")

        if settings is None:
            settings = clip_db.get_settings()

        clip_db.update_clip_status(clip.clip_id, ClipStatus.EDITING)

        # Sureyi belirle (kesim sonrasi kalan govde)
        total_duration = clip.duration or 0.0
        if total_duration <= 0:
            from app.services.hype_detector import hype_detector
            total_duration = hype_detector.get_media_duration(clip.raw_video_path)

        start = cut_start_sec if cut_start_sec > 0 else 0.0
        end = cut_end_sec if cut_end_sec > 0 else total_duration
        body_duration = max(0.0, end - start)

        max_part_sec = settings.horizontal_max_part_minutes * 60
        needs_split = (
            clip.source_kind == ClipSourceKind.VOD
            and max_part_sec > 0
            and body_duration > max_part_sec
        )

        if not needs_split:
            out_path = os.path.join(self.ready_dir, f"{clip.clip_id}_horizontal.mp4")
            trim_applied = (start > 0.01) or (body_duration < total_duration - 0.01)
            self._render_horizontal_segment(
                clip.raw_video_path, out_path,
                start,
                body_duration if trim_applied else 0.0,
                reencode=trim_applied
            )

            clip.processed_video_path = out_path
            clip.status = ClipStatus.READY
            clip_db.update_clip_status(
                clip.clip_id,
                ClipStatus.READY,
                processed_video_path=out_path,
                cut_start_sec=start,
                cut_end_sec=end
            )
            logger.info(f"Horizontal video created: {out_path}")
            return [clip]

        # VOD parcalama
        part_count = int(math.ceil(body_duration / max_part_sec))
        ready_items: List[ClipItem] = []
        source_key = clip.source_key or clip.clip_id

        for part_idx in range(part_count):
            part_start = start + part_idx * max_part_sec
            part_end = min(end, part_start + max_part_sec)
            part_duration = part_end - part_start
            if part_duration < 5.0:
                continue

            part_clip_id = f"{clip.clip_id}_p{part_idx + 1}"
            out_path = os.path.join(self.ready_dir, f"{part_clip_id}_horizontal.mp4")
            self._render_horizontal_segment(
                clip.raw_video_path, out_path,
                part_start, part_duration,
                reencode=True
            )

            part_item = ClipItem(
                clip_id=part_clip_id,
                source_key=source_key,
                platform=clip.platform,
                channel_name=clip.channel_name,
                title=f"{clip.title} — Bölüm {part_idx + 1}",
                url=clip.url,
                duration=part_duration,
                view_count=clip.view_count,
                thumbnail_url=clip.thumbnail_url,
                target_format=ClipTargetFormat.HORIZONTAL,
                source_kind=ClipSourceKind.VOD_PART,
                status=ClipStatus.READY,
                raw_video_path=clip.raw_video_path,
                processed_video_path=out_path,
                hype_score=clip.hype_score,
                priority=clip.priority,
                created_at=clip.created_at
            )
            clip_db.save_clip(part_item)
            ready_items.append(part_item)
            logger.info(f"VOD part {part_idx + 1}/{part_count} created: {out_path}")

        if not ready_items:
            raise RuntimeError(f"VOD split produced no parts for {clip.clip_id}")

        clip.status = ClipStatus.SPLIT
        clip_db.update_clip_status(
            clip.clip_id,
            ClipStatus.SPLIT,
            cut_start_sec=start,
            cut_end_sec=end
        )
        return ready_items


clip_editor = ClipEditorService()
