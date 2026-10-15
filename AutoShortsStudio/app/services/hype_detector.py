import os
import re
import subprocess
from typing import Tuple, List, Optional
from loguru import logger

from app.utils import utils


class HypeDetectorService:
    def __init__(self):
        # FFmpeg binary yolu tespiti
        base_dir = os.path.dirname(utils.root_dir())
        ffmpeg_bin = os.path.join(base_dir, "lib", "ffmpeg", "ffmpeg-7.0-essentials_build", "ffmpeg.exe")
        if not os.path.exists(ffmpeg_bin):
            ffmpeg_bin = "ffmpeg"
        self.ffmpeg_bin = ffmpeg_bin

        ffprobe_bin = os.path.join(base_dir, "lib", "ffmpeg", "ffmpeg-7.0-essentials_build", "ffprobe.exe")
        if not os.path.exists(ffprobe_bin):
            ffprobe_bin = "ffprobe"
        self.ffprobe_bin = ffprobe_bin

    def get_media_duration(self, video_path: str) -> float:
        """Video suresini ffprobe ile saniye cinsinden alir."""
        cmd = [
            self.ffprobe_bin,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
            return float(res.stdout.strip())
        except Exception as e:
            logger.warning(f"Failed to get video duration: {e}")
            return 60.0

    def analyze_audio_volume_peaks(
        self,
        video_path: str,
        target_window_sec: float = 35.0,
        intro_padding_sec: float = 0.0,
        outro_padding_sec: float = 0.0
    ) -> Tuple[float, float, float]:
        """
        FFmpeg ebur128 kullanarak videodaki ses enerjisi tepe noktasini (hype) bulur.
        intro_padding_sec ve outro_padding_sec ile jenerik/bitis bolgeleri haric tutulur.
        Dondurur: (start_sec, end_sec, hype_score)
        """
        total_duration = self.get_media_duration(video_path)
        valid_start = max(0.0, intro_padding_sec)
        valid_end = max(valid_start + target_window_sec, total_duration - outro_padding_sec)

        if total_duration <= target_window_sec:
            return 0.0, total_duration, 1.0

        # Sadece sesi cozumle (-vn) -- uzun videolarda cok daha hizli
        cmd = [
            self.ffmpeg_bin,
            "-vn",
            "-i", video_path,
            "-af", "ebur128=metadata=1",
            "-f", "null",
            "-"
        ]

        analysis_timeout = max(120, int(total_duration * 0.5))
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=analysis_timeout)
            output = res.stderr

            time_energy: List[Tuple[float, float]] = []
            pattern = re.compile(r"t:\s*([\d\.]+)\s+.*?M:\s*([-\d\.]+)")
            for match in pattern.finditer(output):
                t = float(match.group(1))
                m = float(match.group(2))
                energy = max(0.0, m + 70.0)
                time_energy.append((t, energy))

            if not time_energy:
                start = max(valid_start, (total_duration - target_window_sec) / 2.0)
                return start, min(total_duration, start + target_window_sec), 0.5

            # Kayan pencere (Two-Pointer Sliding Window - O(N))
            # Sadece valid_start ve valid_end arasindaki ornekler uzerinde calisir (intro/outro kontaminasyonu onlenir)
            samples = [(t, e) for t, e in time_energy if valid_start <= t <= valid_end]
            if not samples:
                start = max(valid_start, (total_duration - target_window_sec) / 2.0)
                return start, min(total_duration, start + target_window_sec), 0.5

            best_start = valid_start
            best_avg_energy = -1.0

            n = len(samples)
            right = 0
            cur_sum = 0.0

            for left in range(n):
                start_t = samples[left][0]
                end_t = start_t + target_window_sec
                if end_t > valid_end:
                    break

                # Sag isaretciyi pencere sonuna kadar ilerlet
                while right < n and samples[right][0] <= end_t:
                    cur_sum += samples[right][1]
                    right += 1

                count = right - left
                if count > 0:
                    avg_energy = cur_sum / count
                    if avg_energy > best_avg_energy:
                        best_avg_energy = avg_energy
                        best_start = start_t

                # Sol eleman pencereden cikar
                cur_sum -= samples[left][1]

            best_end = min(total_duration, best_start + target_window_sec)
            normalized_score = min(1.0, max(0.0, best_avg_energy / 70.0))
            logger.info(f"Hype audio peak detected: {best_start:.1f}s - {best_end:.1f}s (Score: {normalized_score:.2f})")
            return round(best_start, 2), round(best_end, 2), round(normalized_score, 2)

        except Exception as e:
            logger.warning(f"Audio peak analysis fallback triggered: {e}")
            start = max(valid_start, (total_duration - target_window_sec) / 2.0)
            return start, min(total_duration, start + target_window_sec), 0.5

    def detect_intro_outro_bounds(
        self,
        video_path: str,
        head_window_sec: float = 120.0,
        tail_window_sec: float = 120.0,
        min_silence_sec: float = 2.5,
        intro_trim_sec: float = 0.0,
        outro_trim_sec: float = 0.0
    ) -> Tuple[float, float]:
        """
        Yatay videolar icin intro/outro kisimlarini tespit eder ve
        kalmasi gereken (start_sec, end_sec) araligini dondurur.
        Sabit kirpma (intro_trim_sec / outro_trim_sec) ile sessizlik/siyah ekran tespitini birlestirir.
        """
        total_duration = self.get_media_duration(video_path)
        try:
            cmd = [
                self.ffmpeg_bin,
                "-i", video_path,
                "-vf", f"blackdetect=d={min_silence_sec}:pic_th=0.98",
                "-af", f"silencedetect=noise=-35dB:d={min_silence_sec}",
                "-f", "null",
                "-"
            ]
            timeout = int(max(120, total_duration * 15))
            res = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)
            output = res.stderr
        except Exception as e:
            logger.warning(f"Intro/outro detection failed, keeping full video: {e}")
            trim_s = min(max(0.0, intro_trim_sec), total_duration)
            trim_e = max(trim_s, total_duration - max(0.0, outro_trim_sec))
            return round(trim_s, 2), round(trim_e, 2)

        # Sessizlik bloklari: (start, end)
        silence_blocks: List[Tuple[float, float]] = []
        silence_starts = [float(m.group(1)) for m in re.finditer(r"silence_start:\s*([\d\.]+)", output)]
        silence_ends = [float(m.group(1)) for m in re.finditer(r"silence_end:\s*([\d\.]+)", output)]
        for i, s_start in enumerate(silence_starts):
            s_end = silence_ends[i] if i < len(silence_ends) else total_duration
            silence_blocks.append((s_start, s_end))

        # Siyah kare bloklari: (start, end)
        black_blocks: List[Tuple[float, float]] = []
        for m in re.finditer(r"black_start:\s*([\d\.]+)\s+black_end:\s*([\d\.]+)", output):
            black_blocks.append((float(m.group(1)), float(m.group(2))))

        trim_start = max(0.0, intro_trim_sec)
        trim_end = max(trim_start, total_duration - max(0.0, outro_trim_sec))

        # Bastaki intro: dosyanin basindan baslayan sessizlik/siyah bloklari
        for s_start, s_end in silence_blocks + black_blocks:
            if s_start <= (intro_trim_sec + 1.0) and s_end <= head_window_sec:
                trim_start = max(trim_start, s_end)

        # Sondaki outro: dosyanin sonuna kadar suren sessizlik/siyah bloklari
        for s_start, s_end in silence_blocks + black_blocks:
            if s_end >= (total_duration - outro_trim_sec - 1.0) and (total_duration - s_start) <= tail_window_sec:
                trim_end = min(trim_end, s_start)

        # Guvenlik: kalan govde cok kisaldiysa sabit trim uygula
        if (trim_end - trim_start) < 10.0:
            logger.info("Intro/outro trim would leave too little content, falling back to base trims")
            trim_start = min(intro_trim_sec, total_duration / 2.0)
            trim_end = max(trim_start, total_duration - outro_trim_sec)

        logger.info(
            f"Intro/outro detected: trim {trim_start:.1f}s - {trim_end:.1f}s "
            f"(full: {total_duration:.1f}s)"
        )
        return round(trim_start, 2), round(trim_end, 2)

    def detect_viral_moments_llm(
        self,
        transcript_with_timestamps: str,
        total_duration: float,
        channel_name: str = "",
        video_title: str = "",
        target_window_sec: float = 40.0
    ) -> Optional[Tuple[float, float, str, str, int]]:
        """
        OpenShorts LLM viral moment tespiti.
        Transkripti analiz ederek en viral 25-55 saniyelik bolumu, kanca (hook) metnini,
        YouTube Shorts basligini ve viral puanini dondurur.
        LLM baglantisi yoksa veya basarisizsa None dondurur (ses hype analizi devreye girer).
        """
        from app.services import llm
        try:
            res = llm.generate_viral_moment_selection(
                transcript_with_timestamps=transcript_with_timestamps,
                total_duration=total_duration,
                channel_name=channel_name,
                video_title=video_title,
                min_window=max(20.0, target_window_sec - 15.0),
                max_window=min(total_duration, target_window_sec + 15.0)
            )
            if not res or res.get("is_fallback"):
                logger.warning("LLM response was not valid or was fallback, returning None for audio peak fallback")
                return None

            return (
                res["start_sec"],
                res["end_sec"],
                res["viral_hook_text"],
                res["youtube_title"],
                res["viral_score"]
            )
        except Exception as e:
            logger.warning(f"detect_viral_moments_llm error: {e}")
            return None

    def extract_thumbnail_frame(self, video_path: str, timestamp_sec: float, out_image_path: str) -> bool:
        """
        Videodaki belirli bir saniyeden yuksek kaliteli JPEG kapak gorseli (thumbnail) cikarir.
        """
        if not os.path.exists(video_path):
            return False
        os.makedirs(os.path.dirname(out_image_path), exist_ok=True)
        safe_time = max(0.0, float(timestamp_sec))
        cmd = [
            self.ffmpeg_bin, "-y",
            "-ss", str(safe_time),
            "-i", video_path,
            "-frames:v", "1",
            "-q:v", "2",
            out_image_path
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            return res.returncode == 0 and os.path.exists(out_image_path) and os.path.getsize(out_image_path) > 1024
        except Exception as e:
            logger.warning(f"Thumbnail extraction failed at {safe_time}s: {e}")
            return False


hype_detector = HypeDetectorService()

