import os
import re
import json
import sys
import subprocess
import threading
import requests
from typing import List, Optional, Tuple
from datetime import datetime
from loguru import logger

from app.utils import utils
from app.config import config
from app.models.clip_schema import (
    PlatformType, ClipItem, ClipStatus, StreamerTarget,
    ClipTargetFormat, ClipSourceKind, ClipPipelineSettings,
)
from app.services.clip_db import clip_db


class ClipDownloaderService:
    def __init__(self):
        self.raw_dir = os.path.join(utils.root_dir(), "storage", "clips", "raw")
        os.makedirs(self.raw_dir, exist_ok=True)
        # yt-dlp binary yolu veya python modulu
        bundled_python = os.path.join(os.path.dirname(utils.root_dir()), "lib", "python", "python.exe")
        has_ytdlp_current = False
        try:
            import yt_dlp
            has_ytdlp_current = True
        except ImportError:
            pass

        if has_ytdlp_current and sys.executable and os.path.exists(sys.executable):
            self._python_exe = sys.executable
        elif os.path.exists(bundled_python):
            self._python_exe = bundled_python
        elif sys.executable and os.path.exists(sys.executable):
            self._python_exe = sys.executable
        else:
            self._python_exe = "python"

    # ================= Ortak Yardimcilar =================

    def _get_proxy(self) -> Optional[str]:
        proxy_cfg = getattr(config, "proxy", None) or {}
        return proxy_cfg.get("http") or proxy_cfg.get("https") or None

    def _base_ytdlp_cmd(self) -> List[str]:
        cmd = [self._python_exe, "-m", "yt_dlp", "--no-warnings"]
        proxy = self._get_proxy()
        if proxy:
            cmd += ["--proxy", proxy]
        return cmd

    @staticmethod
    def _pick_thumbnail(data: dict) -> Optional[str]:
        thumb = data.get("thumbnail")
        if thumb:
            return thumb
        thumbs = data.get("thumbnails")
        if isinstance(thumbs, list) and thumbs:
            first = thumbs[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict):
                return first.get("url")
        return None

    def _run_ytdlp_flat_json(self, url: str, playlist_end: int = 20, timeout: int = 90) -> List[dict]:
        """yt-dlp flat-playlist cikisini JSON satirlari olarak dondurur (hata toleransli)."""
        cmd = self._base_ytdlp_cmd() + [
            "--flat-playlist",
            "--dump-json",
            "--playlist-end", str(playlist_end),
            url
        ]
        entries: List[dict] = []
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout
            )
            for line in (res.stdout or "").strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"yt-dlp flat-playlist fetch failed for {url}: {e}")
        return entries

    @staticmethod
    def parse_streamer_url(url: str) -> Tuple[PlatformType, str]:
        """
        Yayinci adresini cozer. Ornekler:
          https://www.twitch.tv/elraenn  -> (TWITCH, "elraenn")
          https://kick.com/xqc           -> (KICK, "xqc")
          https://youtube.com/@raenee    -> (YOUTUBE, "@raenee")
          https://youtube.com/channel/UCxxxx -> (YOUTUBE, "UCxxxx")
        Basarisizsa ValueError firlatir.
        """
        value = (url or "").strip().rstrip("/")
        if not value:
            raise ValueError("Yayinci adresi bos olamaz.")

        # Protolsuz girisleri normalize et
        if "://" not in value:
            value = f"https://{value}"

        match = re.match(r"^https?://(?:www\.|m\.)?twitch\.tv/([A-Za-z0-9_]{3,25})(?:/.*)?$", value, re.IGNORECASE)
        if match:
            name = match.group(1).lower()
            if name in ("videos", "clips", "about", "schedule"):
                raise ValueError("Gecersiz Twitch adresi: kanal adi eksik.")
            return PlatformType.TWITCH, name

        match = re.match(r"^https?://(?:www\.)?kick\.com/([A-Za-z0-9_-]{2,40})(?:/.*)?$", value, re.IGNORECASE)
        if match:
            name = match.group(1).lower()
            if name in ("videos", "clips"):
                raise ValueError("Gecersiz Kick adresi: kanal adi eksik.")
            return PlatformType.KICK, name

        match = re.match(r"^https?://(?:www\.|m\.)?youtube\.com/(@[A-Za-z0-9._-]{3,30}|channel/[A-Za-z0-9_-]{10,40}|c/[A-Za-z0-9._-]{3,30})(?:/.*)?$", value, re.IGNORECASE)
        if match:
            ref = match.group(1)
            # /channel/UC... -> UC... olarak normalize et
            if ref.lower().startswith("channel/"):
                ref = ref[len("channel/"):]
            return PlatformType.YOUTUBE, ref

        raise ValueError(
            "Adres taninamadi. Ornekler: https://twitch.tv/elraenn, "
            "https://kick.com/xqc, https://youtube.com/@kanaladi"
        )

    # ================= Yayinci Besleme (Keşif Agregatori) =================

    @staticmethod
    def _is_title_blacklisted(title: str, blacklist_str: str) -> bool:
        if not blacklist_str or not title:
            return False
        title_l = title.lower()
        bad_words = [w.strip().lower() for w in blacklist_str.split(",") if w.strip()]
        return any(w in title_l for w in bad_words)

    @staticmethod
    def is_valid_horizontal_title(title: str, enforce_reaction_filter: bool = True) -> Tuple[bool, str]:
        """
        Yatay (16:9) videolar icin telif ve icerik ozgunluk kontrolu.
        - Duz 'funny moments', 'en komik anlar', 'derleme', 'montaj' videolari yayincinin izlemedigi 3. parti iceriklerdir (icerik hirsizligi).
        - EGER baslikta funny moments/derleme geciyorsa, ancak ayni zamanda yayincinin izledigini/tepki verdigini gosteren
          ifadeler ('izliyor', 'izledi', 'tepki', 'reaksiyon', 'reacts', 'watching' vb.) varsa KABUL EDILIR.
        - Duz funny moments icerip izleme/tepki icermiyorsa REDDEDILIR.
        """
        if not enforce_reaction_filter or not title:
            return True, ""

        tr_map = {"İ": "i", "I": "ı", "Ş": "ş", "Ğ": "ğ", "Ü": "ü", "Ö": "ö", "Ç": "ç"}
        t_clean = title
        for k, v in tr_map.items():
            t_clean = t_clean.replace(k, v)
        title_l = t_clean.lower().replace("\u0307", "")

        compilation_keywords = [
            "funny moments", "funny moment", "en komik anlar", "komik anlar", "montage", "montaj",
            "derleme", "derlemesi", "troll anlar", "best moments", "en iyi anlar", "epic moments",
            "fails", "fail derlemesi", "highlights"
        ]

        reaction_keywords = [
            "izliyor", "izledi", "izleme", "izlerken", "tepki", "reaksiyon", "reacts", "reacting",
            "reaction", "seyrediyor", "seyretti", "izleyip", "bakıyor", "baktı", "izliyoruz",
            "izlemiş", "gülmeme meydan okuması", "try not to laugh", "izlerse", "izle"
        ]

        has_compilation = any(kw in title_l for kw in compilation_keywords)
        has_reaction = any(rw in title_l for rw in reaction_keywords)

        if has_compilation and not has_reaction:
            reason = "Yayıncının izlemediği düz montaj/funny moments derlemesi (içerik hırsızlığı/telif koruması)."
            return False, reason

        return True, ""


    @staticmethod
    def _extract_upload_dt(data: dict) -> Optional[datetime]:
        """yt-dlp, Twitch ve Kick platformlarindan yukleme/yayin tarihini cozer."""
        if not isinstance(data, dict):
            return None
        upload_date_str = str(data.get("upload_date") or "").strip()
        if upload_date_str and len(upload_date_str) == 8 and upload_date_str.isdigit():
            try:
                return datetime.strptime(upload_date_str, "%Y%m%d")
            except Exception:
                pass
        ts = data.get("timestamp") or data.get("release_timestamp")
        if ts:
            try:
                return datetime.utcfromtimestamp(float(ts))
            except Exception:
                pass
        created_str = str(data.get("created_at") or data.get("datetime") or "").strip()
        if created_str:
            for fmt in (
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d"
            ):
                try:
                    return datetime.strptime(created_str[:19], fmt[:len(created_str[:19])])
                except Exception:
                    continue
        return None

    def fetch_video_upload_date(self, url_or_id: str) -> Optional[datetime]:
        """Bir videonun kesin yukleme tarihini yt-dlp ile hizlica cozer (yaklasik 1-2 saniye)."""
        if not url_or_id:
            return None
        url = str(url_or_id).strip()
        if not url.startswith("http"):
            url = f"https://www.youtube.com/watch?v={url}"
        cmd = self._base_ytdlp_cmd() + ["--print", "%(upload_date)s", url]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=12)
            d_str = (res.stdout or "").strip().split("\n")[0].strip()
            if len(d_str) == 8 and d_str.isdigit():
                return datetime.strptime(d_str, "%Y%m%d")
        except Exception as e:
            logger.debug(f"fetch_video_upload_date error for {url}: {e}")
        return None

    @classmethod
    def _is_video_fresh_or_viral(
        cls,
        data: dict,
        view_count: int,
        max_age_days: int,
        viral_threshold: int,
        enable_viral_hunter: bool = False
    ) -> bool:
        """
        Videoyu guncellik ve viralite kriterine gore filtreler.
        - enable_viral_hunter = False (Varsayilan):
            YALNIZCA son max_age_days icinde yuklenmis taze videolar kabul edilir.
            300k-500k izlenmis olsa bile eski videolar KESINLIKLE elenir.
        - enable_viral_hunter = True:
            max_age_days'den eski videolar da, view_count >= viral_threshold ise kabul edilir.
        """
        upload_dt = cls._extract_upload_dt(data)

        # Flat json'dan tarih donmediyse ve viral avi kapaliysa, yuksek izlenmeli videolari dogrula
        if not upload_dt and not enable_viral_hunter:
            v_ref = data.get("url") or data.get("webpage_url") or data.get("id")
            if v_ref and view_count >= 15000:
                try:
                    from app.services.clip_downloader import clip_downloader
                    upload_dt = clip_downloader.fetch_video_upload_date(str(v_ref))
                    if upload_dt:
                        data["upload_date"] = upload_dt.strftime("%Y%m%d")
                except Exception:
                    pass

        if upload_dt:
            age_days = (datetime.utcnow() - upload_dt).total_seconds() / 86400.0
            if age_days > max_age_days:
                if enable_viral_hunter and view_count >= viral_threshold:
                    logger.info(
                        f"Viral video avi devrede: Video kabul edildi "
                        f"(Yas: {age_days:.1f} gun > {max_age_days} gun, Izlenme: {view_count:,} >= {viral_threshold:,})"
                    )
                    return True
                logger.info(
                    f"Eski video elendi: Yas {age_days:.1f} gun > Limit {max_age_days} gun "
                    f"(Viral Avi: {'Acik' if enable_viral_hunter else 'Kapali'}, Izlenme: {view_count:,})"
                )
                return False
        else:
            # Tarih bulunamadi ve izlenme cok yuksekse (300k+ eski videolar gibi) viral avi kapaliyken elenir
            if not enable_viral_hunter and view_count >= viral_threshold:
                logger.info(f"Tarihsiz ve yuksek izlenmeli ({view_count:,}) supheli eski video elendi: {data.get('title')}")
                return False

        return True

    def fetch_streamer_feed(
        self,
        streamer: StreamerTarget,
        settings: Optional[ClipPipelineSettings] = None
    ) -> List[ClipItem]:
        """
        Bir yayinci icin tum kaynaklari (klip, VOD, YouTube aramasi) tarar ve
        hedef formatlara gore stok ogeleri uretir. Ayni klibin shorts/yatay
        versiyonlari source_key uzerinden tek indirmede paylasir.
        """
        if settings is None:
            settings = clip_db.get_settings()

        wants_shorts = ClipTargetFormat.SHORTS in streamer.formats
        wants_horizontal = ClipTargetFormat.HORIZONTAL in streamer.formats
        items: List[ClipItem] = []

        if streamer.platform == PlatformType.YOUTUBE:
            if wants_horizontal:
                items += self.fetch_youtube_channel_videos(
                    streamer.channel_name,
                    limit=10,
                    min_views=streamer.min_views,
                    max_duration_sec=settings.youtube_source_max_minutes * 60,
                    settings=settings
                )
            if streamer.fan_channel_queries:
                for fq in streamer.fan_channel_queries:
                    fq_clean = fq.strip()
                    if not fq_clean:
                        continue
                    fan_vids = self.fetch_youtube_videos(
                        fq_clean,
                        limit=10,
                        min_views=streamer.min_views,
                        max_duration_sec=settings.youtube_source_max_minutes * 60,
                        settings=settings
                    )
                    for f_clip in fan_vids:
                        if self._is_title_blacklisted(f_clip.title, settings.title_blacklist):
                            continue
                        source_key = f_clip.source_key or f_clip.clip_id
                        if wants_shorts:
                            s_twin = f_clip.model_copy(deep=True)
                            s_twin.clip_id = f"{source_key}__s" if wants_horizontal else source_key
                            s_twin.target_format = ClipTargetFormat.SHORTS
                            items.append(s_twin)
                        if wants_horizontal:
                            enforce_rf = getattr(settings, "filter_horizontal_reactions_only", True)
                            is_val, r_reason = self.is_valid_horizontal_title(f_clip.title, enforce_rf)
                            if not is_val:
                                logger.info(f"Yatay video elendi ({r_reason}): {f_clip.title}")
                            else:
                                h_twin = f_clip.model_copy(deep=True)
                                h_twin.clip_id = f"{source_key}__h" if wants_shorts else source_key
                                h_twin.target_format = ClipTargetFormat.HORIZONTAL
                                items.append(h_twin)
            seen_ids = set()
            deduped = []
            for it in items:
                if it.clip_id not in seen_ids:
                    seen_ids.add(it.clip_id)
                    deduped.append(it)
            return deduped

        # Twitch / Kick klipleri
        clips: List[ClipItem] = []
        if streamer.platform == PlatformType.TWITCH and settings.enable_twitch_discovery:
            clips = self.fetch_twitch_clips(streamer.channel_name, limit=10, min_views=streamer.min_views, settings=settings)
        elif streamer.platform == PlatformType.KICK and settings.enable_kick_discovery:
            clips = self.fetch_kick_clips(streamer.channel_name, limit=10, min_views=streamer.min_views, settings=settings)

        for clip in clips:
            if self._is_title_blacklisted(clip.title, settings.title_blacklist):
                logger.info(f"Clip skipped by title blacklist: {clip.title}")
                continue
            source_key = clip.source_key or clip.clip_id
            if wants_shorts:
                clip.target_format = ClipTargetFormat.SHORTS
                items.append(clip)
            if wants_horizontal:
                enforce_rf = getattr(settings, "filter_horizontal_reactions_only", True)
                is_val, r_reason = self.is_valid_horizontal_title(clip.title, enforce_rf)
                if not is_val:
                    logger.info(f"Yatay video elendi ({r_reason}): {clip.title}")
                else:
                    horizontal_twin = clip.model_copy(deep=True)
                    horizontal_twin.clip_id = f"{source_key}__h"
                    horizontal_twin.target_format = ClipTargetFormat.HORIZONTAL
                    items.append(horizontal_twin)

        if not wants_horizontal and not streamer.fan_channel_queries:
            return items

        # Yayin gecmisi (VOD)
        if wants_horizontal:
            vod_max_sec = settings.vod_max_minutes * 60
            vods: List[ClipItem] = []
            if streamer.platform == PlatformType.TWITCH and settings.enable_twitch_discovery:
                vods = self.fetch_twitch_vods(streamer.channel_name, limit=5, max_duration_sec=vod_max_sec, settings=settings)
            elif streamer.platform == PlatformType.KICK and settings.enable_kick_discovery:
                vods = self.fetch_kick_vods(streamer.channel_name, limit=5, max_duration_sec=vod_max_sec, settings=settings)
            for vod in vods:
                if self._is_title_blacklisted(vod.title, settings.title_blacklist):
                    continue
                source_key = vod.source_key or vod.clip_id
                items.append(vod)
                # Sadece yerel dikey shorts modu secili degilse VOD'dan shorts turetilmesine izin ver
                only_native_shorts = getattr(settings, "shorts_source_mode", "smart_hybrid") == "only_native_shorts"
                if wants_shorts and not only_native_shorts:
                    s_twin = vod.model_copy(deep=True)
                    s_twin.clip_id = f"{source_key}__s"
                    s_twin.target_format = ClipTargetFormat.SHORTS
                    items.append(s_twin)

        # YouTube'da yayinci klipleri aramasi (default query)
        if wants_horizontal and settings.youtube_search_enabled:
            display = streamer.display_name or streamer.channel_name
            only_native_shorts = getattr(settings, "shorts_source_mode", "smart_hybrid") == "only_native_shorts"
            query = f"{display} {settings.youtube_query_suffix}".strip()
            yt_items = self.fetch_youtube_videos(
                query,
                limit=10,
                min_views=streamer.min_views,
                max_duration_sec=settings.youtube_source_max_minutes * 60,
                settings=settings
            )
            for yt_clip in yt_items:
                if self._is_title_blacklisted(yt_clip.title, settings.title_blacklist):
                    continue
                source_key = yt_clip.source_key or yt_clip.clip_id
                enforce_rf = getattr(settings, "filter_horizontal_reactions_only", True)
                is_val, r_reason = self.is_valid_horizontal_title(yt_clip.title, enforce_rf)
                if not is_val:
                    logger.info(f"Yatay video elendi ({r_reason}): {yt_clip.title}")
                else:
                    h_twin = yt_clip.model_copy(deep=True)
                    h_twin.clip_id = f"{source_key}__h" if wants_shorts else source_key
                    h_twin.target_format = ClipTargetFormat.HORIZONTAL
                    items.append(h_twin)
                if wants_shorts and not only_native_shorts:
                    s_twin = yt_clip.model_copy(deep=True)
                    s_twin.clip_id = f"{source_key}__s"
                    s_twin.target_format = ClipTargetFormat.SHORTS
                    items.append(s_twin)

        # Ozel fan / kesit kanallari sorgulari (streamer.fan_channel_queries)
        if streamer.fan_channel_queries:
            for fan_query in streamer.fan_channel_queries:
                fq = fan_query.strip()
                if not fq:
                    continue
                fan_items = self.fetch_youtube_videos(
                    fq,
                    limit=10,
                    min_views=streamer.min_views,
                    max_duration_sec=settings.youtube_source_max_minutes * 60,
                    settings=settings
                )
                for fan_clip in fan_items:
                    if self._is_title_blacklisted(fan_clip.title, settings.title_blacklist):
                        continue
                    source_key = fan_clip.source_key or fan_clip.clip_id
                    if wants_shorts:
                        s_clip = fan_clip.model_copy(deep=True)
                        s_clip.clip_id = f"{source_key}__s" if wants_horizontal else source_key
                        s_clip.target_format = ClipTargetFormat.SHORTS
                        items.append(s_clip)
                    if wants_horizontal:
                        enforce_rf = getattr(settings, "filter_horizontal_reactions_only", True)
                        is_val, r_reason = self.is_valid_horizontal_title(fan_clip.title, enforce_rf)
                        if not is_val:
                            logger.info(f"Yatay video elendi ({r_reason}): {fan_clip.title}")
                        else:
                            h_twin = fan_clip.model_copy(deep=True)
                            h_twin.clip_id = f"{source_key}__h" if wants_shorts else source_key
                            h_twin.target_format = ClipTargetFormat.HORIZONTAL
                            items.append(h_twin)

        seen_ids = set()
        deduped = []
        for it in items:
            if it.clip_id not in seen_ids:
                seen_ids.add(it.clip_id)
                deduped.append(it)
        return deduped

    # ================= Kaynak: Klip Listeleri =================

    def fetch_streamer_clips(
        self,
        streamer: StreamerTarget,
        limit: int = 10,
        settings: Optional[ClipPipelineSettings] = None
    ) -> List[ClipItem]:
        """Belirtilen yayincinin en son/en populer kliplerini listeler."""
        channel = streamer.channel_name.strip().lower()
        if streamer.platform == PlatformType.TWITCH:
            return self.fetch_twitch_clips(channel, limit=limit, min_views=streamer.min_views, settings=settings)
        elif streamer.platform == PlatformType.KICK:
            return self.fetch_kick_clips(channel, limit=limit, min_views=streamer.min_views, settings=settings)
        return []

    def fetch_twitch_clips(
        self,
        channel_name: str,
        limit: int = 10,
        min_views: int = 0,
        settings: Optional[ClipPipelineSettings] = None
    ) -> List[ClipItem]:
        """Twitch uzerinden filtreye uygun klipleri yt-dlp ile sorgular."""
        clips: List[ClipItem] = []
        logger.info(f"Fetching Twitch clips for {channel_name}...")

        if settings and not settings.enable_viral_hunter:
            if settings.max_video_age_days <= 1:
                range_param = "24h"
            elif settings.max_video_age_days <= 7:
                range_param = "7d"
            elif settings.max_video_age_days <= 30:
                range_param = "30d"
            else:
                range_param = "all"
        elif settings and settings.enable_viral_hunter:
            range_param = "all"
        else:
            range_param = "7d"

        url = f"https://www.twitch.tv/{channel_name}/clips?filter=clips&range={range_param}"
        entries = self._run_ytdlp_flat_json(url, playlist_end=limit * 2, timeout=60)

        for data in entries:
            try:
                clip_id = data.get("id") or ""
                if not clip_id:
                    continue
                view_count = int(data.get("view_count") or 0)
                if view_count < min_views:
                    continue

                title = data.get("title") or f"{channel_name} clip {clip_id}"
                if settings:
                    if self._is_title_blacklisted(title, settings.title_blacklist):
                        continue
                    if not self._is_video_fresh_or_viral(
                        data, view_count, settings.max_video_age_days,
                        settings.viral_threshold_for_older, settings.enable_viral_hunter
                    ):
                        continue

                up_dt = self._extract_upload_dt(data)
                created_at_platform = up_dt.isoformat() if up_dt else None

                clips.append(ClipItem(
                    clip_id=f"twitch_{clip_id}",
                    source_key=f"twitch_{clip_id}",
                    platform=PlatformType.TWITCH,
                    channel_name=channel_name,
                    title=title,
                    url=data.get("url") or f"https://clips.twitch.tv/{clip_id}",
                    duration=float(data.get("duration") or 0.0),
                    view_count=view_count,
                    thumbnail_url=data.get("thumbnail") or "",
                    created_at_platform=created_at_platform,
                    target_format=ClipTargetFormat.SHORTS,
                    source_kind=ClipSourceKind.CLIP,
                    status=ClipStatus.DISCOVERED
                ))
                if len(clips) >= limit:
                    break
            except Exception as ex:
                logger.debug(f"Error parsing twitch clip entry: {ex}")

        logger.info(f"Discovered {len(clips)} Twitch clips for {channel_name}")
        return clips

    def fetch_kick_clips(
        self,
        channel_name: str,
        limit: int = 10,
        min_views: int = 0,
        settings: Optional[ClipPipelineSettings] = None
    ) -> List[ClipItem]:
        """Kick uzerinden en son/populer klipleri Kick public API veya yt-dlp ile sorgular."""
        clips: List[ClipItem] = []
        logger.info(f"Fetching Kick clips for {channel_name}...")

        if settings and not settings.enable_viral_hunter:
            if settings.max_video_age_days <= 1:
                kick_time = "day"
            elif settings.max_video_age_days <= 7:
                kick_time = "week"
            elif settings.max_video_age_days <= 30:
                kick_time = "month"
            else:
                kick_time = "all"
        else:
            kick_time = "all"

        # 1. Kick Public API Denemesi
        try:
            api_url = f"https://kick.com/api/v2/channels/{channel_name}/clips?sort=view&time={kick_time}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "application/json"
            }
            resp = requests.get(api_url, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                raw_clips = data.get("clips", [])
                for item in raw_clips:
                    clip_id = str(item.get("id", ""))
                    if not clip_id:
                        continue
                    views = int(item.get("views", 0))
                    if views < min_views:
                        continue

                    title = item.get("title", f"Kick Clip {clip_id}")
                    if settings:
                        if self._is_title_blacklisted(title, settings.title_blacklist):
                            continue
                        if not self._is_video_fresh_or_viral(
                            item, views, settings.max_video_age_days,
                            settings.viral_threshold_for_older, settings.enable_viral_hunter
                        ):
                            continue

                    up_dt = self._extract_upload_dt(item)
                    created_at_platform = up_dt.isoformat() if up_dt else None

                    clips.append(ClipItem(
                        clip_id=f"kick_{clip_id}",
                        source_key=f"kick_{clip_id}",
                        platform=PlatformType.KICK,
                        channel_name=channel_name,
                        title=title,
                        url=item.get("clip_url") or f"https://kick.com/{channel_name}/clips/{clip_id}",
                        duration=float(item.get("duration", 0)),
                        view_count=views,
                        thumbnail_url=item.get("thumbnail_url"),
                        created_at_platform=created_at_platform,
                        target_format=ClipTargetFormat.SHORTS,
                        source_kind=ClipSourceKind.CLIP,
                        status=ClipStatus.DISCOVERED
                    ))
                    if len(clips) >= limit:
                        break
        except Exception as e:
            logger.warning(f"Kick API request failed, falling back to yt-dlp: {e}")

        # 2. Eger API bos donerse veya hata verirse yt-dlp ile dene
        if not clips:
            kick_url = f"https://kick.com/{channel_name}/clips"
            entries = self._run_ytdlp_flat_json(kick_url, playlist_end=limit * 2, timeout=60)
            for data in entries:
                try:
                    clip_id = data.get("id") or ""
                    if not clip_id:
                        continue
                    view_count = int(data.get("view_count") or 0)
                    if view_count < min_views:
                        continue
                    title = data.get("title") or f"{channel_name} Kick Clip"
                    if settings:
                        if self._is_title_blacklisted(title, settings.title_blacklist):
                            continue
                        if not self._is_video_fresh_or_viral(
                            data, view_count, settings.max_video_age_days,
                            settings.viral_threshold_for_older, settings.enable_viral_hunter
                        ):
                            continue

                    up_dt = self._extract_upload_dt(data)
                    created_at_platform = up_dt.isoformat() if up_dt else None

                    clips.append(ClipItem(
                        clip_id=f"kick_{clip_id}",
                        source_key=f"kick_{clip_id}",
                        platform=PlatformType.KICK,
                        channel_name=channel_name,
                        title=title,
                        url=data.get("url") or f"https://kick.com/{channel_name}/clips/{clip_id}",
                        duration=float(data.get("duration") or 0.0),
                        view_count=view_count,
                        thumbnail_url=data.get("thumbnail"),
                        created_at_platform=created_at_platform,
                        target_format=ClipTargetFormat.SHORTS,
                        source_kind=ClipSourceKind.CLIP,
                        status=ClipStatus.DISCOVERED
                    ))
                    if len(clips) >= limit:
                        break
                except Exception:
                    pass

        logger.info(f"Discovered {len(clips)} Kick clips for {channel_name}")
        return clips

    # ================= Kaynak: VOD (Yayin Gecmisi) =================

    def fetch_twitch_vods(
        self,
        channel_name: str,
        limit: int = 5,
        max_duration_sec: int = 0,
        settings: Optional[ClipPipelineSettings] = None
    ) -> List[ClipItem]:
        """Twitch yayin gecmisindeki (arsiv) VOD'lari listeler."""
        return self._fetch_vods(
            platform=PlatformType.TWITCH,
            channel_name=channel_name,
            url=f"https://www.twitch.tv/{channel_name}/videos?filter=archives",
            id_prefix="twitch_vod",
            limit=limit,
            max_duration_sec=max_duration_sec,
            settings=settings
        )

    def fetch_kick_vods(
        self,
        channel_name: str,
        limit: int = 5,
        max_duration_sec: int = 0,
        settings: Optional[ClipPipelineSettings] = None
    ) -> List[ClipItem]:
        """Kick yayin gecmisindeki VOD'lari listeler."""
        return self._fetch_vods(
            platform=PlatformType.KICK,
            channel_name=channel_name,
            url=f"https://kick.com/{channel_name}/videos",
            id_prefix="kick_vod",
            limit=limit,
            max_duration_sec=max_duration_sec,
            settings=settings
        )

    def _fetch_vods(
        self,
        platform: PlatformType,
        channel_name: str,
        url: str,
        id_prefix: str,
        limit: int,
        max_duration_sec: int,
        settings: Optional[ClipPipelineSettings] = None
    ) -> List[ClipItem]:
        vods: List[ClipItem] = []
        logger.info(f"Fetching {platform.value} VODs for {channel_name}...")
        entries = self._run_ytdlp_flat_json(url, playlist_end=limit * 2, timeout=90)

        for data in entries:
            try:
                vod_id = str(data.get("id") or "")
                if not vod_id:
                    continue
                duration = float(data.get("duration") or 0.0)
                if max_duration_sec > 0 and duration > max_duration_sec:
                    continue
                view_count = int(data.get("view_count") or 0)
                title = data.get("title") or f"{channel_name} VOD {vod_id}"

                if settings:
                    if self._is_title_blacklisted(title, settings.title_blacklist):
                        continue
                    if not self._is_video_fresh_or_viral(
                        data, view_count, settings.max_video_age_days,
                        settings.viral_threshold_for_older, settings.enable_viral_hunter
                    ):
                        continue

                up_dt = self._extract_upload_dt(data)
                created_at_platform = up_dt.isoformat() if up_dt else None

                vods.append(ClipItem(
                    clip_id=f"{id_prefix}_{vod_id}",
                    source_key=f"{id_prefix}_{vod_id}",
                    platform=platform,
                    channel_name=channel_name,
                    title=title,
                    url=data.get("url") or data.get("webpage_url") or f"https://www.twitch.tv/videos/{vod_id}",
                    duration=duration,
                    view_count=view_count,
                    thumbnail_url=self._pick_thumbnail(data),
                    created_at_platform=created_at_platform,
                    target_format=ClipTargetFormat.HORIZONTAL,
                    source_kind=ClipSourceKind.VOD,
                    status=ClipStatus.DISCOVERED
                ))
                if len(vods) >= limit:
                    break
            except Exception as ex:
                logger.debug(f"Error parsing {platform.value} VOD entry: {ex}")

        logger.info(f"Discovered {len(vods)} {platform.value} VODs for {channel_name}")
        return vods

    # ================= Kaynak: YouTube =================

    def fetch_youtube_videos(
        self,
        query: str,
        limit: int = 10,
        min_views: int = 0,
        max_duration_sec: int = 1800,
        settings: Optional[ClipPipelineSettings] = None
    ) -> List[ClipItem]:
        """YouTube aramasiyla yayinci klipleri/derlemeleri bulur (yt-dlp ytsearch)."""
        import urllib.parse
        items: List[ClipItem] = []
        if settings and not settings.enable_viral_hunter:
            if settings.max_video_age_days <= 1:
                sp_code = "CAISBAgCEAE%253D"  # Son 24 saat (Today)
            elif settings.max_video_age_days <= 7:
                sp_code = "CAISBAgDEAE%253D"  # Son 1 hafta (This week)
            elif settings.max_video_age_days <= 30:
                sp_code = "CAISBAgEEAE%253D"  # Son 1 ay (This month)
            else:
                sp_code = "CAISBAgFEAE%253D"  # Son 1 yil (This year)
            encoded_q = urllib.parse.quote_plus(query)
            search_target = f"https://www.youtube.com/results?search_query={encoded_q}&sp={sp_code}"
            logger.info(f"Searching YouTube (Fresh upload filter sp={sp_code}, max_age={settings.max_video_age_days}d): {query}")
        else:
            search_target = f"ytsearch{limit * 3}:{query}"
            logger.info(f"Searching YouTube (Viral hunter / all-time mode): {query}")

        entries = self._run_ytdlp_flat_json(search_target, playlist_end=limit * 3, timeout=90)

        for data in entries:
            try:
                video_id = data.get("id") or ""
                if not video_id:
                    continue
                view_count = int(data.get("view_count") or 0)
                duration = float(data.get("duration") or 0.0)
                title = data.get("title") or f"YouTube Video {video_id}"

                if settings:
                    if self._is_title_blacklisted(title, settings.title_blacklist):
                        logger.info(f"YouTube video skipped by title blacklist: {title}")
                        continue
                    if not self._is_video_fresh_or_viral(
                        data, view_count, settings.max_video_age_days,
                        settings.viral_threshold_for_older, settings.enable_viral_hunter
                    ):
                        continue

                if view_count < min_views:
                    continue
                # Cok kisa (intro gibi) ve cok uzun videolari ele
                if duration < 30.0:
                    continue
                if max_duration_sec > 0 and duration > max_duration_sec:
                    continue

                up_dt = self._extract_upload_dt(data)
                created_at_platform = up_dt.isoformat() if up_dt else None

                items.append(ClipItem(
                    clip_id=f"youtube_{video_id}",
                    source_key=f"youtube_{video_id}",
                    platform=PlatformType.YOUTUBE,
                    channel_name=(data.get("channel") or data.get("uploader") or query).strip(),
                    title=title,
                    url=data.get("url") or data.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}",
                    duration=duration,
                    view_count=view_count,
                    thumbnail_url=self._pick_thumbnail(data),
                    created_at_platform=created_at_platform,
                    target_format=ClipTargetFormat.HORIZONTAL,
                    source_kind=ClipSourceKind.YOUTUBE_VIDEO,
                    status=ClipStatus.DISCOVERED
                ))
                if len(items) >= limit:
                    break
            except Exception as ex:
                logger.debug(f"Error parsing YouTube search entry: {ex}")

        logger.info(f"Discovered {len(items)} YouTube videos for query: {query}")
        return items

    def fetch_youtube_channel_videos(
        self,
        channel_ref: str,
        limit: int = 10,
        min_views: int = 0,
        max_duration_sec: int = 1800,
        settings: Optional[ClipPipelineSettings] = None
    ) -> List[ClipItem]:
        """Bir YouTube kanalinin videolarini listeler (@handle, UC... veya c/isim)."""
        ref = (channel_ref or "").strip().rstrip("/")
        if ref.startswith("@"):
            channel_url = f"https://www.youtube.com/{ref}/videos"
        elif ref.startswith("UC"):
            channel_url = f"https://www.youtube.com/channel/{ref}/videos"
        elif ref.startswith("c/") or ref.startswith("user/"):
            channel_url = f"https://www.youtube.com/{ref}/videos"
        else:
            channel_url = f"https://www.youtube.com/@{ref}/videos"

        items: List[ClipItem] = []
        logger.info(f"Fetching YouTube channel videos: {channel_url}")
        entries = self._run_ytdlp_flat_json(channel_url, playlist_end=limit * 2, timeout=120)

        for data in entries:
            try:
                video_id = data.get("id") or ""
                if not video_id or len(str(video_id)) > 20:
                    continue
                view_count = int(data.get("view_count") or 0)
                duration = float(data.get("duration") or 0.0)
                title = data.get("title") or f"YouTube Video {video_id}"

                if settings:
                    if self._is_title_blacklisted(title, settings.title_blacklist):
                        logger.info(f"YouTube channel video skipped by title blacklist: {title}")
                        continue
                    if not self._is_video_fresh_or_viral(
                        data, view_count, settings.max_video_age_days,
                        settings.viral_threshold_for_older, settings.enable_viral_hunter
                    ):
                        continue

                if view_count < min_views:
                    continue
                if duration < 30.0:
                    continue
                if max_duration_sec > 0 and duration > max_duration_sec:
                    continue

                up_dt = self._extract_upload_dt(data)
                created_at_platform = up_dt.isoformat() if up_dt else None

                items.append(ClipItem(
                    clip_id=f"youtube_{video_id}",
                    source_key=f"youtube_{video_id}",
                    platform=PlatformType.YOUTUBE,
                    channel_name=(data.get("channel") or data.get("uploader") or ref).strip(),
                    title=title,
                    url=data.get("url") or data.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}",
                    duration=duration,
                    view_count=view_count,
                    thumbnail_url=data.get("thumbnail"),
                    created_at_platform=created_at_platform,
                    target_format=ClipTargetFormat.HORIZONTAL,
                    source_kind=ClipSourceKind.YOUTUBE_VIDEO,
                    status=ClipStatus.DISCOVERED
                ))
                if len(items) >= limit:
                    break
            except Exception as ex:
                logger.debug(f"Error parsing YouTube channel entry: {ex}")

        logger.info(f"Discovered {len(items)} YouTube channel videos for {ref}")
        return items

    # ================= Indirme =================

    def download_clip(self, clip: ClipItem, settings: Optional[ClipPipelineSettings] = None) -> str:
        """
        Kaynagi en iyi kalitede MP4 olarak storage/clips/raw/ altina indirir.
        Ayni source_key'e sahip ogeler (shorts/yatay ikizleri) tek dosyayi paylasir.
        settings.download_rate_limit_mb ile indirme hiz limiti (bant genisligi korumasi) uygulanir.
        """
        if settings is None:
            settings = clip_db.get_settings()

        file_stem = clip.source_key or clip.clip_id
        out_path = os.path.join(self.raw_dir, f"{file_stem}.mp4")

        if os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
            logger.info(f"Clip already downloaded at: {out_path}")
            clip.raw_video_path = out_path
            clip.status = ClipStatus.DOWNLOADED
            clip_db.update_clip_status(clip.clip_id, ClipStatus.DOWNLOADED, raw_video_path=out_path)
            try:
                from app.services.video_progress_tracker import video_progress_tracker
                video_progress_tracker.update_download_progress(
                    percent=100.0,
                    speed="Önbellek",
                    eta="00:00",
                    detail=f"Zaten indirilmiş dosya kullanılıyor ({os.path.basename(out_path)})"
                )
            except Exception:
                pass
            return out_path

        clip_db.update_clip_status(clip.clip_id, ClipStatus.DOWNLOADING)
        logger.info(f"Downloading {clip.clip_id} from {clip.url} to {out_path}...")

        # Uzun VOD'larda dosya boyutunu sinirlamak icin 1080p kap
        is_vod = clip.source_kind == ClipSourceKind.VOD
        if is_vod:
            fmt = "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best"
        else:
            fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"

        cmd = self._base_ytdlp_cmd() + [
            "--no-playlist",
            "--newline",
            "-f", fmt,
            "--merge-output-format", "mp4",
            "--retries", "3",
            "-o", out_path,
        ]

        # Hiz Limiti (Bant Genisligi Sinirlandirici - Internetin yavaslamamasi icin)
        rate_limit = getattr(settings, "download_rate_limit_mb", 0.0) or 0.0
        if rate_limit > 0:
            rate_str = f"{rate_limit:g}M"
            cmd += ["--limit-rate", rate_str]
            logger.info(f"Bandwidth throttling active: download limited to {rate_str}/s for {clip.clip_id}")

        cmd.append(clip.url)

        # Sureye bagli indirme zamani butcesi (VOD'lar buyuktur)
        timeout = 300
        if clip.duration and clip.duration > 300:
            timeout = int(max(300, clip.duration * 20))
        if rate_limit > 0:
            timeout = max(timeout, 900)

        progress_re = re.compile(
            r"\[download\]\s+(\d+(?:\.\d+)?)%\s+of\s+~?\s*([\d\.]+\s*[kKmMgGtT]i?B)(?:\s+at\s+([\d\.]+\s*\S+))?(?:\s+ETA\s+(\S+))?"
        )

        try:
            import threading
            from app.services.video_progress_tracker import video_progress_tracker

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace"
            )

            # Streaming okuma icin sert zaman asimi koruyucusu (S11)
            timer = threading.Timer(timeout, lambda: proc.kill())
            timer.daemon = True
            timer.start()

            try:
                last_output_lines = []
                for line in proc.stdout:
                    line_str = line.strip()
                    if not line_str:
                        continue
                    last_output_lines.append(line_str)
                    if len(last_output_lines) > 20:
                        last_output_lines.pop(0)

                    m = progress_re.search(line_str)
                    if m:
                        pct = float(m.group(1))
                        total_size = m.group(2) or ""
                        speed = m.group(3) or ""
                        eta = m.group(4) or ""
                        clean_speed = "" if "unknown" in speed.lower() else speed
                        clean_eta = "" if "unknown" in eta.lower() else eta
                        detail_msg = f"İndiriliyor: %{pct:.1f}"
                        if total_size:
                            detail_msg += f" / {total_size}"
                        if clean_speed:
                            detail_msg += f" ({clean_speed})"
                        if clean_eta:
                            detail_msg += f" - Kalan: {clean_eta}"

                        video_progress_tracker.update_download_progress(
                            percent=pct,
                            speed=clean_speed,
                            eta=clean_eta,
                            detail=detail_msg
                        )

                proc.wait()
            finally:
                timer.cancel()

            if proc.returncode != 0:
                err_summary = "\n".join(last_output_lines[-5:])
                raise RuntimeError(f"yt-dlp failed (code {proc.returncode}): {err_summary}")

            if not os.path.exists(out_path):
                # Eger yt-dlp uzanti degistirdiyse kontrol et
                base_without_ext = os.path.splitext(out_path)[0]
                for ext in [".mp4", ".mkv", ".webm"]:
                    candidate = base_without_ext + ext
                    if os.path.exists(candidate):
                        out_path = candidate
                        break

            if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
                raise FileNotFoundError(f"Downloaded file not found or empty: {out_path}")

            clip.raw_video_path = out_path
            clip.status = ClipStatus.DOWNLOADED
            try:
                from app.services.clip_editor import clip_editor
                vprops = clip_editor.probe_video_properties(out_path)
                clip.is_vertical = vprops.get("is_vertical", False)
            except Exception:
                pass
            clip_db.update_clip_status(clip.clip_id, ClipStatus.DOWNLOADED, raw_video_path=out_path)
            logger.info(f"Clip successfully downloaded: {out_path} ({os.path.getsize(out_path)} bytes)")
            video_progress_tracker.update_stage(
                stage="downloaded",
                stage_label="İndirme Tamamlandı",
                percent=35.0,
                detail=f"İndirme tamamlandı: {os.path.basename(out_path)}"
            )
            return out_path

        except Exception as e:
            err_msg = f"Download error for {clip.clip_id}: {str(e)}"
            logger.error(err_msg)
            clip_db.update_clip_status(clip.clip_id, ClipStatus.FAILED, error_message=err_msg)
            raise


clip_downloader = ClipDownloaderService()
