import os
import shutil
import subprocess
from functools import lru_cache
from typing import Dict, Any, List, Tuple
from loguru import logger

from app.utils import utils


class HardwareDetector:
    """
    Sistemdeki CPU ve GPU (NVIDIA, AMD, Intel) donanımını otomatik tespit eder.
    NVIDIA tespit edilirse GPU modunu (h264_nvenc), AMD veya dahili grafik tespit edilirse
    güvenli ve kararlı CPU modunu (libx264) önerir.
    """

    @classmethod
    @lru_cache(maxsize=1)
    def detect(cls) -> Dict[str, Any]:
        gpu_name = ""
        gpu_brand = "NONE"
        has_nvidia = False
        has_amd = False
        has_intel = False

        # 1. Yöntem: nvidia-smi (Hızlı ve en kesin NVIDIA tespiti)
        try:
            res = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if res.returncode == 0 and res.stdout.strip():
                gpu_name = res.stdout.strip().split(",")[0].strip()
                gpu_brand = "NVIDIA"
                has_nvidia = True
        except Exception:
            pass

        # 2. Yöntem: Windows WMI / CIM Controller Taraması (AMD, Intel ve diğerleri için)
        if not has_nvidia and os.name == "nt":
            try:
                cmd = ["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
                if res.returncode == 0 and res.stdout.strip():
                    lines = [line.strip() for line in res.stdout.strip().splitlines() if line.strip()]
                    for line in lines:
                        l_upper = line.upper()
                        if "NVIDIA" in l_upper:
                            gpu_name = line
                            gpu_brand = "NVIDIA"
                            has_nvidia = True
                            break
                        elif "AMD" in l_upper or "RADEON" in l_upper:
                            gpu_name = line
                            gpu_brand = "AMD"
                            has_amd = True
                        elif "INTEL" in l_upper and not gpu_name:
                            gpu_name = line
                            gpu_brand = "INTEL"
                            has_intel = True
            except Exception as e:
                logger.debug(f"CIM GPU detection skipped: {e}")

        # 3. Yöntem: Linux lspci fallback
        if not has_nvidia and not has_amd and os.name != "nt":
            try:
                res = subprocess.run(["lspci"], capture_output=True, text=True, timeout=2)
                if res.returncode == 0:
                    for line in res.stdout.splitlines():
                        if "VGA" in line or "3D" in line:
                            l_upper = line.upper()
                            if "NVIDIA" in l_upper:
                                gpu_name = line.split(":")[-1].strip()
                                gpu_brand = "NVIDIA"
                                has_nvidia = True
                                break
                            elif "AMD" in l_upper or "RADEON" in l_upper:
                                gpu_name = line.split(":")[-1].strip()
                                gpu_brand = "AMD"
                                has_amd = True
            except Exception:
                pass

        # FFmpeg Hardware Encoder Canlı Doğrulama
        base_dir = os.path.dirname(utils.root_dir())
        ffmpeg_bin = os.path.join(base_dir, "lib", "ffmpeg", "ffmpeg-7.0-essentials_build", "ffmpeg.exe")
        if not os.path.exists(ffmpeg_bin):
            ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"

        nvenc_working = False
        if has_nvidia:
            try:
                test_cmd = [
                    ffmpeg_bin, "-y",
                    "-f", "lavfi", "-i", "nullsrc=s=64x64:d=0.2",
                    "-c:v", "h264_nvenc",
                    "-f", "null", "-"
                ]
                chk = subprocess.run(test_cmd, capture_output=True, timeout=3)
                if chk.returncode == 0:
                    nvenc_working = True
            except Exception as ex:
                logger.debug(f"NVENC live probe failed: {ex}")

        # Kural:
        # NVIDIA varsa -> Otomatik GPU seçili gelir.
        # AMD veya diğerleri varsa -> Otomatik CPU seçili gelir.
        if has_nvidia and nvenc_working:
            recommended_mode = "gpu"
            recommended_codec = "h264_nvenc"
            status_text = f"NVIDIA GPU Aktif ({gpu_name or 'GeForce'})"
        elif has_amd:
            recommended_mode = "cpu"
            recommended_codec = "libx264"
            status_text = f"AMD Ekran Kartı ({gpu_name}) - Kararlı CPU Modu Önerildi"
        elif has_intel:
            recommended_mode = "cpu"
            recommended_codec = "libx264"
            status_text = f"Intel Dahili Grafik ({gpu_name}) - CPU Modu Önerildi"
        else:
            recommended_mode = "cpu"
            recommended_codec = "libx264"
            status_text = "Standart CPU (İşlemci) Modu"

        return {
            "has_nvidia": has_nvidia and nvenc_working,
            "has_amd": has_amd,
            "gpu_brand": gpu_brand,
            "gpu_name": gpu_name or "Tespit Edilemedi / Standart Donanım",
            "recommended_mode": recommended_mode,
            "recommended_codec": recommended_codec,
            "status_text": status_text,
        }

    @classmethod
    def get_effective_codec(cls, configured_mode: str = "auto") -> str:
        """
        Kullanıcı 'auto', 'gpu' veya 'cpu' seçtiğinde kullanılacak gerçek video codecini döner.
        """
        hw = cls.detect()
        mode = (configured_mode or "auto").lower()

        if mode == "gpu":
            if hw.get("has_nvidia"):
                return "h264_nvenc"
            return "libx264"
        elif mode == "cpu":
            return "libx264"
        else: # auto
            return hw.get("recommended_codec", "libx264")


hardware_detector = HardwareDetector()
