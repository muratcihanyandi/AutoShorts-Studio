import os
import math
import subprocess
import uuid
from typing import Optional, List
from loguru import logger
from PIL import Image, ImageDraw, ImageFont
import numpy as np

try:
    import cv2
    _OPENCV_AVAILABLE = True
except ImportError:
    _OPENCV_AVAILABLE = False

from app.utils import utils
from app.config import config
from app.models.clip_schema import (
    ClipItem, ClipPipelineSettings, ClipStatus, ClipTargetFormat, ClipSourceKind
)
from app.services.clip_db import clip_db
from app.services.hardware_detector import hardware_detector
from app.services.video_progress_tracker import video_progress_tracker

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

        ffprobe_bin = os.path.join(base_dir, "lib", "ffmpeg", "ffmpeg-7.0-essentials_build", "ffprobe.exe")
        if not os.path.exists(ffprobe_bin):
            ffprobe_bin = "ffprobe"
        self.ffprobe_bin = ffprobe_bin

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

    def probe_video_properties(self, video_path: str) -> dict:
        """
        ffprobe ile video cozunurlugu, en-boy orani ve altyazi akislarini sorgular.
        """
        result = {
            "width": 1920,
            "height": 1080,
            "is_vertical": False,
            "duration": 0.0,
            "has_subtitle_stream": False
        }
        if not video_path or not os.path.exists(video_path):
            return result

        try:
            cmd = [
                self.ffprobe_bin,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,duration",
                "-of", "csv=s=x:p=0",
                video_path
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=15)
            line = res.stdout.strip().split("\n")[0] if res.stdout.strip() else ""
            parts = line.split("x")
            if len(parts) >= 2:
                w = int(parts[0])
                h = int(parts[1])
                result["width"] = w
                result["height"] = h
                result["is_vertical"] = (h > w)
        except Exception as e:
            logger.warning(f"Failed to probe video dimensions: {e}")

        try:
            sub_cmd = [
                self.ffprobe_bin,
                "-v", "error",
                "-select_streams", "s",
                "-show_entries", "stream=index",
                "-of", "csv=p=0",
                video_path
            ]
            sub_res = subprocess.run(sub_cmd, capture_output=True, text=True, errors="replace", timeout=15)
            if sub_res.stdout.strip():
                result["has_subtitle_stream"] = True
        except Exception:
            pass

        return result

    def check_video_has_subtitles(self, video_path: str) -> bool:
        """
        Videoda gomulu veya ekrana basilmis (hardcoded) altyazi olup olmadigini tespit eder.
        1. ffprobe ile soft subtitle akisi kontrolu
        2. Ornek kareler cikarilarak alt bolgede (lower-third) yuksek kontrastli altyazi pikselleri tespiti
        """
        props = self.probe_video_properties(video_path)
        if props.get("has_subtitle_stream"):
            return True

        # Video karesi cikarip metin/altyazi bolgesini incele
        try:
            from PIL import Image
            import numpy as np

            # 2. ve 5. saniyelerden 2 ornek kare al
            sample_times = [2.0, 5.0]
            detected_samples = 0

            for sec in sample_times:
                tmp_frame = os.path.join(self.temp_dir, f"sub_chk_{os.getpid()}_{int(sec)}.jpg")
                cmd = [
                    self.ffmpeg_bin, "-y",
                    "-ss", str(sec),
                    "-i", video_path,
                    "-vframes", "1",
                    "-q:v", "3",
                    tmp_frame
                ]
                subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=10)
                if os.path.exists(tmp_frame):
                    try:
                        with Image.open(tmp_frame) as img:
                            w, h = img.size
                            # Altyazinin yerlesebilecegi alt bolge (h*0.60 ile h*0.90 arasi)
                            crop_box = (int(w * 0.10), int(h * 0.60), int(w * 0.90), int(h * 0.90))
                            sub_region = img.crop(crop_box).convert("L")
                            arr = np.array(sub_region, dtype=np.float32)

                            # Metin pikselleri yuksek parlaklik (>215) ve kenarlarinda koyu cerceve (<45) barindirir
                            bright_pixels = np.sum(arr > 215)
                            dark_pixels = np.sum(arr < 45)
                            total_px = arr.size
                            
                            if total_px > 0 and (bright_pixels / total_px > 0.012) and (dark_pixels / total_px > 0.035):
                                grad = np.abs(arr[:, 1:] - arr[:, :-1])
                                if np.mean(grad) > 16.0:
                                    detected_samples += 1
                    finally:
                        if os.path.exists(tmp_frame):
                            try:
                                os.remove(tmp_frame)
                            except Exception:
                                pass

            if detected_samples >= 1:
                logger.info(f"Existing subtitles detected in '{video_path}' via visual check.")
                return True

        except Exception as e:
            logger.debug(f"Subtitle visual check skipped: {e}")

        return False

    def detect_facecam_position(
        self,
        video_path: str,
        sample_fractions: tuple[float, ...] = (0.15, 0.35, 0.55, 0.75)
    ) -> str:
        """
        Videodaki yayıncı webcamini (facecam) bilgisayarlı görü, zamansal hareket (temporal motion)
        ve yüz analizi ile otomatik tespit eder.
        
        Özellikler:
        1. İki ardışık zaman karesi (t1, t2) arasındaki piksel farkı ile hareket analizi yapar.
        2. MSI, Asus, bağış barı gibi tamamen STATİK olan sponsor logolarını sıfır hareketten tespit edip eler.
        3. OpenCV Haar Cascade ile bulunan yüzün canlılığını (hareket ve en/boy oranını) doğrular.
        4. Cilt tonu (Skin Color) analizinde doygun kırmızı (saturated red) logo piksellerini filtreler.
        """
        if not video_path or not os.path.exists(video_path):
            return "bottom_left"

        props = self.probe_video_properties(video_path)
        duration = float(props.get("duration", 0.0) or 0.0)

        sample_times = []
        if duration > 3.0:
            for frac in sample_fractions:
                t = duration * frac
                if 1.0 <= t <= max(1.0, duration - 1.2):
                    sample_times.append(t)
        if not sample_times:
            sample_times = [1.5, 3.5]

        votes = {
            "bottom_left": 0.0,
            "bottom_right": 0.0,
            "top_left": 0.0,
            "top_right": 0.0,
        }

        face_cascade = None
        if _OPENCV_AVAILABLE:
            try:
                cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
                face_cascade = cv2.CascadeClassifier(cascade_path)
                if face_cascade.empty():
                    face_cascade = None
            except Exception as e:
                logger.debug(f"OpenCV cascade classifier init failed: {e}")
                face_cascade = None

        for t in sample_times:
            t2 = min(duration - 0.2, t + 0.5) if duration > 2.0 else t + 0.3
            f1_path = os.path.join(self.temp_dir, f"face_m1_{uuid.uuid4().hex[:8]}.jpg")
            f2_path = os.path.join(self.temp_dir, f"face_m2_{uuid.uuid4().hex[:8]}.jpg")

            try:
                # 1. Kareyi çıkar (t1)
                cmd1 = [
                    self.ffmpeg_bin, "-y",
                    "-ss", f"{t:.2f}",
                    "-i", video_path,
                    "-vframes", "1",
                    "-q:v", "3",
                    f1_path
                ]
                subprocess.run(cmd1, capture_output=True, timeout=8)

                # 2. Kareyi çıkar (t2 - hareket analizi için)
                cmd2 = [
                    self.ffmpeg_bin, "-y",
                    "-ss", f"{t2:.2f}",
                    "-i", video_path,
                    "-vframes", "1",
                    "-q:v", "3",
                    f2_path
                ]
                subprocess.run(cmd2, capture_output=True, timeout=8)

                if not os.path.exists(f1_path):
                    continue

                img1 = cv2.imread(f1_path) if _OPENCV_AVAILABLE else None
                img2 = cv2.imread(f2_path) if (_OPENCV_AVAILABLE and os.path.exists(f2_path)) else None

                if img1 is None:
                    continue

                h_f, w_f = img1.shape[:2]
                gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
                gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY) if img2 is not None else gray1

                w_crop = int(w_f * 0.38)
                h_crop = int(h_f * 0.45)

                corner_boxes = {
                    "top_left": (0, h_crop, 0, w_crop),
                    "top_right": (0, h_crop, w_f - w_crop, w_f),
                    "bottom_left": (h_f - h_crop, h_f, 0, w_crop),
                    "bottom_right": (h_f - h_crop, h_f, w_f - w_crop, w_f),
                }

                # 1. Aşama: Köşeler Arası Zamansal Hareket (Temporal Motion) Analizi
                motion_scores = {}
                for c_name, (y1, y2, x1, x2) in corner_boxes.items():
                    diff = cv2.absdiff(gray1[y1:y2, x1:x2], gray2[y1:y2, x1:x2])
                    m_val = float(np.mean(diff))
                    motion_scores[c_name] = m_val

                # 2. Aşama: OpenCV Haar Cascade Yüz Tespiti + Canlılık Doğrulaması
                face_found_in_frame = False
                if face_cascade is not None:
                    try:
                        faces = face_cascade.detectMultiScale(
                            gray1,
                            scaleFactor=1.1,
                            minNeighbors=5,
                            minSize=(int(h_f * 0.08), int(h_f * 0.08)),
                            maxSize=(int(h_f * 0.45), int(h_f * 0.45))
                        )
                        for (fx, fy, fw, fh) in faces:
                            # İnsan yüzü aspect ratio kontrolü (0.70 - 1.35 arası)
                            aspect = float(fw) / max(1.0, float(fh))
                            if aspect < 0.70 or aspect > 1.35:
                                continue

                            cx = fx + (fw / 2.0)
                            cy = fy + (fh / 2.0)
                            is_left = cx < (w_f * 0.5)
                            is_top = cy < (h_f * 0.5)

                            if is_left and not is_top:
                                corner = "bottom_left"
                            elif not is_left and not is_top:
                                corner = "bottom_right"
                            elif is_left and is_top:
                                corner = "top_left"
                            else:
                                corner = "top_right"

                            # Yüz bölgesindeki anlık hareket kontrolü (statik logoları ele)
                            face_diff = cv2.absdiff(
                                gray1[fy:fy+fh, fx:fx+fw],
                                gray2[fy:fy+fh, fx:fx+fw]
                            )
                            face_motion = float(np.mean(face_diff))
                            
                            # Eğer yüz bölgesinde hareket çok düşükse (< 1.6), bu sabit bir logodur (örn: MSI ejderhası)
                            if face_motion < 1.6 and motion_scores.get(corner, 0.0) < 2.0:
                                logger.info(f"[Facecam AI] Statik logo yüz sanıldı ve elendi ({corner}, hareket={face_motion:.2f})")
                                continue

                            weight = float(fw * fh) / float(w_f * h_f) * 1500.0
                            # Hareket çarpanı: Canlı konuşan/hareket eden yüze ekstra güven puanı
                            weight *= (1.0 + min(face_motion, 15.0) / 10.0)

                            dist_to_corner_x = min(cx, w_f - cx) / w_f
                            dist_to_corner_y = min(cy, h_f - cy) / h_f
                            if dist_to_corner_x < 0.35 and dist_to_corner_y < 0.40:
                                weight *= 2.0

                            votes[corner] += weight
                            face_found_in_frame = True
                    except Exception as ex_face:
                        logger.debug(f"Face cascade detection error on frame {t}: {ex_face}")

                # 3. Aşama: Cilt Tonu (Skin Color) & Doygun Renk (Logo) Elemesi
                if not face_found_in_frame:
                    for c_name, (y1, y2, x1, x2) in corner_boxes.items():
                        m_val = motion_scores.get(c_name, 0.0)
                        # STATİK KÖŞE ELEMESİ: Hareket < 2.2 ise kesinlikle yayıncı kamerası değil, logodur!
                        if m_val < 2.2:
                            continue

                        c_arr = img1[y1:y2, x1:x2]
                        if c_arr.size == 0:
                            continue

                        try:
                            # Cilt tonu tespiti
                            ycbcr = cv2.cvtColor(c_arr, cv2.COLOR_BGR2YCrCb)
                            cr = ycbcr[:, :, 1]
                            cb = ycbcr[:, :, 2]
                            skin_mask = (cb >= 77) & (cb <= 127) & (cr >= 133) & (cr <= 173)

                            # MSI / Sponsor logo koruması: Doygun parlak kırmızı pikselleri kesinlikle çıkar
                            r = c_arr[:, :, 2].astype(int)
                            g = c_arr[:, :, 1].astype(int)
                            b = c_arr[:, :, 0].astype(int)
                            saturated_red = (r > 150) & (g < 80) & (b < 80)
                            skin_mask = skin_mask & (~saturated_red)

                            skin_ratio = float(np.sum(skin_mask)) / float(c_arr.shape[0] * c_arr.shape[1])
                            if skin_ratio > 0.04:
                                # Yalnızca hareketli olan bölgeye cilt puanı ver
                                motion_factor = min(m_val / 3.0, 2.5)
                                votes[c_name] += (skin_ratio * 35.0 * motion_factor)
                        except Exception as ex_skin:
                            logger.debug(f"Skin analysis error on {c_name}: {ex_skin}")

            finally:
                for tmp_p in (f1_path, f2_path):
                    if os.path.exists(tmp_p):
                        try:
                            os.remove(tmp_p)
                        except Exception:
                            pass

        best_corner = max(votes, key=votes.get)
        if votes[best_corner] <= 0.0:
            # Fallback: En yaygın webcam konumu sol-alttır
            best_corner = "bottom_left"

        logger.info(
            f"Auto Facecam: '{os.path.basename(video_path)}' için webcam konumu "
            f"'{best_corner}' olarak otomatik tespit edildi (Oylar: {votes})"
        )
        return best_corner

    def generate_animated_ass_subtitles(
        self,
        audio_path: str,
        out_ass_path: str,
        highlight_color: str = "#FFD700",
        font_name: str = "Arial",
        font_size: int = 58
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
        fn = font_name or "Arial"
        fs = font_size or 58

        # ASS Altyazi Dosyasi Basligi (9:16 - 1080x1920 cozunurluk)
        # Font: Kullanici fontu, vurgu rengi sarimsi altin, kalin siyah outline
        ass_content = [
            "[Script Info]",
            "ScriptType: v4.00+",
            "PlayResX: 1080",
            "PlayResY: 1920",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            # Alignment=2 (Alt Orta), MarginV=450 (Shorts dugmelerinin ustunde guvenli bolge)
            f"Style: Default,{fn},{fs},&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,80,80,450,1",
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

    def _get_encoder_args(self) -> tuple[list[str], str]:
        """
        Sistemdeki donanıma ve kullanıcı tercihine göre uygun video encoder argümanlarını döner.
        GPU (NVIDIA NVENC) veya CPU (libx264).
        """
        hw_mode = config.app.get("hardware_mode", "auto")
        effective_codec = hardware_detector.get_effective_codec(hw_mode)
        if effective_codec == "h264_nvenc":
            return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "22"], "h264_nvenc"
        return ["-c:v", "libx264", "-preset", "fast", "-crf", "22"], "libx264"

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

        # 0. Video Boyutu ve Mevcut Altyazı Tespiti
        props = self.probe_video_properties(clip.raw_video_path)
        is_native_vertical = props.get("is_vertical", False)
        clip.is_vertical = is_native_vertical

        already_has_subtitles = props.get("has_subtitle_stream", False)
        if not already_has_subtitles and settings.skip_subtitles_if_detected:
            already_has_subtitles = self.check_video_has_subtitles(clip.raw_video_path)
        clip.has_subtitles = already_has_subtitles

        # 1. Kesme parametreleri
        has_cut = cut_end_sec > cut_start_sec and (cut_end_sec - cut_start_sec) >= 5.0
        time_params = []
        if has_cut:
            duration = cut_end_sec - cut_start_sec
            time_params = ["-ss", str(cut_start_sec), "-t", str(duration)]
            logger.info(f"Applying cut: {cut_start_sec:.1f}s - {cut_end_sec:.1f}s (duration: {duration:.1f}s)")

        # 2. Altyazi uretimi (opsiyonel & mükerrer korumalı)
        transcript_text = ""
        ass_available = False
        if settings.subtitle_enabled:
            if settings.skip_subtitles_if_detected and already_has_subtitles:
                logger.info(f"Video '{clip.title}' already has subtitles; skipping Whisper subtitle generation.")
                video_progress_tracker.update_stage(
                    stage="subtitles",
                    stage_label="Mevcut Altyazı Korundu (%60)",
                    percent=60.0,
                    detail="Videoda mevcut altyazı tespit edildi, mükerrer altyazı basılmadan devam ediliyor."
                )
            else:
                video_progress_tracker.update_stage(
                    stage="subtitles",
                    stage_label="Altyazı Üretiliyor (%60)",
                    percent=60.0,
                    detail="Whisper ile ses analizi ve hareketli kelime altyazıları oluşturuluyor..."
                )
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
                        highlight_color=settings.subtitle_color,
                        font_name=settings.subtitle_font,
                        font_size=settings.subtitle_font_size
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

        if is_native_vertical:
            # Girdi videosu zaten dikey / Shorts!
            # Bozulma veya yapay yarma yapmadan 1080x1920'ye tam oturt
            layout = "native_vertical"
            filter_str = (
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[base]"
            )
            final_v = "[base]"
        else:
            # Yatay video: Kullanıcı kuralı gereği asla yatay bırakılmaz.
            # Yayıncının webcam'i üste, zoomlanmış oyun/ekran alta yerleştirilir.
            layout = settings.video_layout or "streamer_facecam"

            if layout == "streamer_facecam" or layout not in ("center_crop", "split"):
                # OpenShorts Akilli Yayinci/Oyun Duzeni (Facecam + Gameplay)
                # Uste yayinci kamerasi (1080x780), alta ana oyun/ekran (1080x1140)
                face_x = "0"
                face_y = "ih*0.52"
                pos = getattr(settings, "facecam_position", "auto") or "auto"
                if pos == "auto":
                    pos = self.detect_facecam_position(clip.raw_video_path)
                    logger.info(f"Auto Facecam: '{clip.title[:30]}' kamera konumu otomatik olarak '{pos}' seçildi.")

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
            elif layout == "center_crop":
                # Merkeze odaklanip dikey tam ekran zoom & kirpma
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

        encoder_args, used_codec = self._get_encoder_args()

        cmd.extend([
            "-filter_complex", filter_str,
            "-map", map_v,
            "-map", "0:a?",
            *encoder_args,
            "-c:a", "aac",
            "-b:a", "192k",
            out_shorts_path
        ])

        video_progress_tracker.update_stage(
            stage="rendering",
            stage_label="9:16 Render (%75)",
            percent=75.0,
            detail=f"FFmpeg dikey video montajı yapılıyor ({used_codec}, layout={layout})..."
        )
        logger.info(f"Rendering 9:16 Shorts with FFmpeg to {out_shorts_path} (codec={used_codec}, layout={layout}, hook={hook_available})...")
        res = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=600)

        # GPU nvenc basarisiz olursa CPU (libx264) fallback ile tekrar dene
        if res.returncode != 0 and used_codec == "h264_nvenc":
            logger.warning(f"NVENC GPU render failed ({res.stderr[:200]}), falling back to CPU (libx264)...")
            fallback_cmd = []
            skip_next = False
            for idx, arg in enumerate(cmd):
                if skip_next:
                    skip_next = False
                    continue
                if arg == "-c:v" and idx + 1 < len(cmd) and cmd[idx + 1] == "h264_nvenc":
                    fallback_cmd.extend(["-c:v", "libx264", "-preset", "fast", "-crf", "22"])
                    skip_next = True
                elif arg in ("-preset", "-cq") and idx + 1 < len(cmd):
                    skip_next = True
                else:
                    fallback_cmd.append(arg)
            res = subprocess.run(fallback_cmd, capture_output=True, text=True, errors="replace", timeout=600)

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
            encoder_args, _ = self._get_encoder_args()
            # Kesme varsa yeniden kodla (senkron ve kare hizalamasi icin)
            cmd.extend([
                "-i", raw_path,
                *encoder_args,
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
