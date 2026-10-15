import os
import time
import subprocess
import threading
from datetime import datetime
from typing import List, Optional, Dict, Any
from loguru import logger

from app.models.clip_schema import (
    PlatformType, ClipItem, ClipStatus, StreamerTarget, ClipPipelineSettings,
    ClipTargetFormat,
)
from app.services.clip_db import clip_db
from app.services.clip_downloader import clip_downloader
from app.services.hype_detector import hype_detector
from app.services.clip_editor import clip_editor
from app.services.shorts_publisher import shorts_publisher
from app.services.clip_publisher import clip_publisher
from app.services.clip_ai_seo import clip_ai_seo


class ClipSchedulerService:
    """
    Otonom motor. Uc asamali dongu:
      1. Kesif  : yayincilari tarar (klip + VOD + YouTube aramasi), yeni icerikleri stoga yazar.
      2. Islem  : stoktaki DISCOVERED ogeleri isleyip (kesim + kurgu) READY yapar.
      3. Yayin  : gunluk kota ve yayin penceresi dahilinde stoktan YouTube'a yukler.
    """

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._is_running = False
        self._lock = threading.Lock()
        self._pipeline_lock = threading.RLock()
        self._logs: List[Dict[str, Any]] = []
        self._current_task_info = "Beklemede (Idle)"

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def current_status(self) -> str:
        return self._current_task_info

    def get_recent_logs(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._logs[-limit:])

    def _add_log(self, message: str, level: str = "info"):
        log_entry = {
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "message": message,
            "level": level
        }
        with self._lock:
            self._logs.append(log_entry)
            if len(self._logs) > 200:
                self._logs.pop(0)
        if level == "error":
            logger.error(f"[Scheduler] {message}")
        elif level == "warning":
            logger.warning(f"[Scheduler] {message}")
        else:
            logger.info(f"[Scheduler] {message}")

    def start(self):
        """Zamanlayiciyi baslatir."""
        if self._is_running:
            logger.warning("Scheduler already running")
            return

        # Onceki calismadan yarim kalan (downloading, editing, uploading) islemleri kurtar
        recovery_stats = clip_db.recover_interrupted_clips()
        total_recovered = sum(recovery_stats.values())
        if total_recovered > 0:
            self._add_log(
                f"Yarim kalan {total_recovered} klip kurtarildi: "
                f"{recovery_stats.get('recovered_ready', 0)} hazir, "
                f"{recovery_stats.get('recovered_downloaded', 0)} indirilmis olarak devam ediyor, "
                f"{recovery_stats.get('recovered_discovered', 0)} yeniden baslatilacak.",
                "info"
            )

        self._stop_event.clear()
        self._is_running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="ClipSchedulerThread")
        self._thread.start()
        self._add_log("Otonom Klip Zamanlayicisi baslatildi.", "info")

    def stop(self):
        """Zamanlayiciyi durdurur."""
        if not self._is_running:
            return
        self._add_log("Zamanlayici durduruluyor...", "info")
        self._stop_event.set()
        self._is_running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._current_task_info = "Durduruldu"
        self._add_log("Zamanlayici durduruldu.", "info")

    # ================= Tekli Pipeline (Manuel / AI Agent) =================

    def run_single_pipeline_for_clip(
        self,
        clip: ClipItem,
        settings: Optional[ClipPipelineSettings] = None,
        custom_start_sec: Optional[float] = None,
        custom_end_sec: Optional[float] = None,
        publish_now: bool = False
    ) -> ClipItem:
        """
        Tek bir klip icin tum adimlari calistirir:
        Indir -> Kesim noktasi tespiti -> Kurgu (format bazli) -> (istege bagli) hemen YouTube'a yukle.
        publish_now=False ise sonuc READY olarak stokta bekler; gunluk kota yayinlayicisi yukler.
        """
        if settings is None:
            settings = clip_db.get_settings()

        with self._pipeline_lock:
            from app.services.video_progress_tracker import video_progress_tracker
            video_progress_tracker.start_task(
                clip_id=clip.clip_id,
                title=clip.title,
                channel_name=clip.channel_name,
                target_format=clip.target_format.value,
                platform=clip.platform.value,
                thumbnail_url=clip.thumbnail_url,
                duration=clip.duration,
            )
            try:
                fmt_label = "Yatay" if clip.target_format == ClipTargetFormat.HORIZONTAL else "Shorts"
                self._add_log(f"İşleniyor ({fmt_label}): '{clip.title}' ({clip.channel_name} - {clip.platform.value})", "info")
                clip_db.save_clip(clip)

                # 1. Indirme (onceden inmis ise indirme atlanir, ayni kaynagin ikizleri tek dosyayi paylasir)
                raw_path = clip.raw_video_path
                if raw_path and os.path.exists(raw_path) and os.path.getsize(raw_path) > 1024:
                    self._add_log(f"Zaten indirilmis video kullaniliyor (indirme atlandi): {os.path.basename(raw_path)}", "info")
                    video_progress_tracker.update_stage(
                        stage="downloaded",
                        stage_label="Önbellekten Alındı (%35)",
                        percent=35.0,
                        detail="Önceden indirilmiş yerel video dosyası kullanılıyor."
                    )
                else:
                    self._current_task_info = f"İndiriliyor: {clip.title[:30]}"
                    self._add_log(f"Video indiriliyor: {clip.url}", "info")
                    raw_path = clip_downloader.download_clip(clip, settings=settings)

                if clip.target_format == ClipTargetFormat.HORIZONTAL:
                    publish_target = self._process_horizontal(clip, raw_path, settings,
                                                              custom_start_sec, custom_end_sec)
                else:
                    publish_target = self._process_shorts(clip, raw_path, settings,
                                                          custom_start_sec, custom_end_sec)

                # Ham dosya temizligi (ayni kaynagin bekleyen ikizi yoksa)
                self._cleanup_raw(raw_path, clip, settings)

                # 2. Hemen yukleme (yalnizca acik istek uzerine; normalde kota yayinlayicisi yukler)
                if publish_now:
                    if shorts_publisher.is_youtube_ready():
                        self._current_task_info = f"YouTube'a yükleniyor: {clip.title[:30]}"
                        self._add_log(f"YouTube'a yukleniyor ({publish_target.target_format.value})...", "info")
                        video_id = shorts_publisher.publish_clip(publish_target, settings=settings)
                        self._add_log(f"Video YouTube'a yuklendi! ID: {video_id}", "info")
                    else:
                        self._add_log("YouTube token bulunamadi veya bagli degil, video READY olarak bekletiliyor.", "warning")
                        video_progress_tracker.finish_task(
                            clip.clip_id,
                            success=True,
                            result_path=publish_target.processed_video_path or ""
                        )
                else:
                    self._add_log("Video READY olarak stokta bekliyor; gunluk kota ile otomatik yayinlanacak.", "info")
                    video_progress_tracker.finish_task(
                        clip.clip_id,
                        success=True,
                        result_path=publish_target.processed_video_path or ""
                    )

                self._current_task_info = "Beklemede"
                return clip

            except Exception as e:
                err = f"Pipeline hatasi ({clip.clip_id}): {str(e)}"
                self._add_log(err, "error")
                self._current_task_info = f"Hata: {str(e)[:30]}"
                clip_db.update_clip_status(clip.clip_id, ClipStatus.FAILED, error_message=err)
                video_progress_tracker.finish_task(clip.clip_id, success=False, error=err)
                raise

    def _process_shorts(
        self,
        clip: ClipItem,
        raw_path: str,
        settings: ClipPipelineSettings,
        custom_start_sec: Optional[float],
        custom_end_sec: Optional[float]
    ) -> ClipItem:
        """Shorts hatti: LLM viral an / ses tepe noktasi (hype) penceresi + hook + 9:16 + altyazi."""
        from app.services.video_progress_tracker import video_progress_tracker

        video_progress_tracker.update_stage(
            stage="analyzing",
            stage_label="Viral An & Hype Analizi (%45)",
            percent=45.0,
            detail="LLM ve ses seviye analizi ile en vurucu 30-50sn taranıyor..."
        )

        if (custom_start_sec is not None and custom_end_sec is not None
                and custom_end_sec > custom_start_sec):
            start_sec, end_sec = custom_start_sec, custom_end_sec
            self._add_log(f"Manuel kesim araligi kullaniliyor: {start_sec:.1f}s - {end_sec:.1f}s", "info")
            if not clip.hook_text:
                clip.hook_text = clip.title
        else:
            total_duration = hype_detector.get_media_duration(raw_path)
            used_llm = False
            if settings.llm_moment_detection:
                self._current_task_info = f"LLM Moment Analizi: {clip.title[:30]}"
                self._add_log("LLM ile viral an, kanca (hook) ve baslik tespit ediliyor...", "info")
                transcript_str = clip.transcript or ""
                if not transcript_str:
                    temp_audio = os.path.join(clip_editor.temp_dir, f"{clip.clip_id}_detect.wav")
                    try:
                        extract_cmd = [
                            clip_editor.ffmpeg_bin, "-y",
                            "-i", raw_path,
                            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                            temp_audio
                        ]
                        if total_duration > 900:
                            extract_cmd[4:4] = ["-t", "900"]
                        subprocess.run(extract_cmd, capture_output=True, text=True, errors="replace", timeout=60)
                        if os.path.exists(temp_audio):
                            from app.services import subtitle
                            sub_file = f"{temp_audio}.srt"
                            subtitle.create(temp_audio, sub_file)
                            if os.path.exists(sub_file):
                                with open(sub_file, "r", encoding="utf-8", errors="replace") as sf:
                                    transcript_str = sf.read()
                                try:
                                    os.remove(sub_file)
                                    os.remove(temp_audio)
                                except Exception:
                                    pass
                    except Exception as ex:
                        logger.warning(f"Fast transcription failed for LLM detection: {ex}")
                    finally:
                        if os.path.exists(temp_audio):
                            try:
                                os.remove(temp_audio)
                            except Exception:
                                pass

                if transcript_str.strip():
                    try:
                        detected = hype_detector.detect_viral_moments_llm(
                            transcript_with_timestamps=transcript_str,
                            total_duration=total_duration,
                            channel_name=clip.channel_name,
                            video_title=clip.title,
                            target_window_sec=settings.shorts_window_sec
                        )
                        if detected is not None:
                            start_sec, end_sec, hook_text, yt_title, viral_score = detected
                            clip.hook_text = hook_text
                            clip.viral_score = viral_score
                            if yt_title:
                                clip.youtube_title = yt_title
                            clip.hype_score = round(viral_score / 10.0, 2)
                            used_llm = True
                            self._add_log(f"LLM viral an secildi: {start_sec:.1f}s - {end_sec:.1f}s (Hook: '{hook_text}', Score: {viral_score}/10)", "info")
                        else:
                            self._add_log("LLM erisilemedi veya kapali (Ollama/API hatasi). Ses tepe noktasi (hype) analizine geciliyor...", "warning")
                    except Exception as ex:
                        logger.warning(f"detect_viral_moments_llm failed: {ex}")
                        self._add_log(f"LLM hatasi ({ex}). Ses tepe noktasi analizine geciliyor...", "warning")

            if not used_llm:
                self._current_task_info = f"Hype analizi: {clip.title[:30]}"
                self._add_log("Ses tepe noktalari ve hype analiz ediliyor...", "info")
                start_sec, end_sec, score = hype_detector.analyze_audio_volume_peaks(
                    raw_path,
                    target_window_sec=settings.shorts_window_sec,
                    intro_padding_sec=settings.intro_trim_seconds,
                    outro_padding_sec=settings.outro_trim_seconds
                )
                clip.hype_score = score
                if not clip.hook_text:
                    clip.hook_text = clip.title

        clip.cut_start_sec = start_sec
        clip.cut_end_sec = end_sec
        clip_db.update_clip_status(
            clip.clip_id, ClipStatus.ANALYZING,
            cut_start_sec=start_sec, cut_end_sec=end_sec,
            hype_score=clip.hype_score, hook_text=clip.hook_text,
            viral_score=clip.viral_score
        )

        self._current_task_info = f"9:16 Kurgu ve Altyazı: {clip.title[:30]}"
        self._add_log(f"9:16 dikey donusturme ve altyazi isleniyor ({start_sec:.1f}s - {end_sec:.1f}s)...", "info")
        video_progress_tracker.update_stage(
            stage="rendering",
            stage_label="9:16 Kurgu & Altyazı Render (%70)",
            percent=70.0,
            detail=f"Dikey yerleşim ve altyazılar işleniyor ({start_sec:.1f}s - {end_sec:.1f}s)..."
        )
        clip_editor.process_clip_to_shorts(
            clip,
            settings=settings,
            cut_start_sec=start_sec,
            cut_end_sec=end_sec
        )

        # AI Destekli Özgün Paket (Başlık, Açıklama, Etiketler, Kategori, Clickbait Thumbnail)
        video_progress_tracker.update_stage(
            stage="thumbnail",
            stage_label="AI Başlık, SEO & Kapak Görseli (%90)",
            percent=90.0,
            detail="Seçili AI modeli ile özgün viral başlık, SEO açıklaması ve kapak görseli hazırlanıyor..."
        )
        try:
            ai_meta = clip_ai_seo.generate_monetization_metadata(clip, ClipTargetFormat.SHORTS, settings=settings)
            if not clip.youtube_title:
                clip.youtube_title = ai_meta["title"]
            clip.youtube_description = ai_meta["description"]
            clip.youtube_tags = ai_meta["tags"]
            clip.youtube_category_id = ai_meta["category_id"]
            if not clip.hook_text and ai_meta.get("hook_text"):
                clip.hook_text = ai_meta["hook_text"]
            clip.made_for_kids = False
            clip.age_restricted = False
            self._add_log(f"AI Özgün Başlık: '{clip.youtube_title}' (Kategori: {clip.youtube_category_id})", "info")
        except Exception as ai_err:
            logger.warning(f"AI monetization metadata failed: {ai_err}")

        # Clickbait Thumbnail Studio (Kalın Sarı Metinli Profesyonel Kapak)
        if settings.auto_generate_thumbnail and clip.processed_video_path and os.path.exists(clip.processed_video_path):
            base_dir = os.path.dirname(clip.processed_video_path)
            stem = os.path.splitext(os.path.basename(clip.processed_video_path))[0]
            thumb_file = os.path.join(base_dir, f"{stem}_thumb.jpg")
            sample_time = max(1.0, (end_sec - start_sec) * 0.3)
            created_thumb = clip_ai_seo.create_clickbait_thumbnail(
                video_path=clip.processed_video_path,
                hook_text=clip.hook_text or "İNANILMAZ ANLAR!",
                target_format=ClipTargetFormat.SHORTS,
                output_path=thumb_file,
                timestamp_sec=sample_time
            )
            if created_thumb and os.path.exists(created_thumb):
                clip.custom_thumbnail_path = created_thumb

        # DB'ye tüm metadata'yı kaydet
        clip_db.update_clip_metadata(
            clip.clip_id,
            youtube_title=clip.youtube_title,
            youtube_description=clip.youtube_description,
            youtube_tags=clip.youtube_tags,
            youtube_category_id=clip.youtube_category_id,
            hook_text=clip.hook_text,
            made_for_kids=False,
            age_restricted=False,
            custom_thumbnail_path=clip.custom_thumbnail_path
        )

        video_progress_tracker.update_stage(
            stage="ready",
            stage_label="Stokta Hazırlandı (%100)",
            percent=100.0,
            detail="Shorts kurgusu ve AI paketi tamamlandı, stok havuzuna eklendi."
        )

        # Taslak olarak onceden YouTube'a yukleme kontrolu
        if getattr(settings, "pre_upload_as_draft", True) and shorts_publisher.is_youtube_ready():
            try:
                self._add_log(f"Shorts YouTube'a taslak olarak yukleniyor (yayin saati geldiginde saniyesinde herkese acik yapilacak)...", "info")
                draft_id = shorts_publisher.pre_upload_draft(clip, settings=settings)
                if draft_id:
                    self._add_log(f"Taslak YouTube'da hazir! Video ID: {draft_id}", "info")
            except Exception as pre_err:
                logger.warning(f"Shorts pre-upload draft failed: {pre_err}")

        return clip

    def _process_horizontal(
        self,
        clip: ClipItem,
        raw_path: str,
        settings: ClipPipelineSettings,
        custom_start_sec: Optional[float],
        custom_end_sec: Optional[float]
    ) -> ClipItem:
        """
        Yatay hat: intro/outro kirpma, govde korunur, altyazi eklenmez.
        Uzun VOD'lar parcalara bolunur; ilk parca aninda yuklenebilir hedef olur.
        """
        from app.services.video_progress_tracker import video_progress_tracker

        video_progress_tracker.update_stage(
            stage="analyzing",
            stage_label="Intro / Outro Kırpma Analizi (%45)",
            percent=45.0,
            detail=f"Başlangıçtan {settings.intro_trim_seconds}s, sondan {settings.outro_trim_seconds}s analiz ediliyor..."
        )

        if custom_start_sec is not None or custom_end_sec is not None:
            start_sec = custom_start_sec or 0.0
            end_sec = custom_end_sec or 0.0
            self._add_log(f"Manuel kesim araligi kullaniliyor: {start_sec:.1f}s - {end_sec:.1f}s", "info")
        else:
            self._current_task_info = f"Intro/Outro analizi: {clip.title[:30]}"
            self._add_log("Intro/outro (bas/son) tespiti yapiliyor...", "info")
            start_sec, end_sec = hype_detector.detect_intro_outro_bounds(
                raw_path,
                intro_trim_sec=settings.intro_trim_seconds,
                outro_trim_sec=settings.outro_trim_seconds
            )

        clip.cut_start_sec = start_sec
        clip.cut_end_sec = end_sec
        clip_db.update_clip_status(clip.clip_id, ClipStatus.ANALYZING, cut_start_sec=start_sec, cut_end_sec=end_sec)

        self._current_task_info = f"Yatay kurgu: {clip.title[:30]}"
        self._add_log(f"Yatay (16:9) kurgu yapiliyor ({start_sec:.1f}s - {end_sec:.1f}s), altyazi eklenmiyor...", "info")
        video_progress_tracker.update_stage(
            stage="rendering",
            stage_label="16:9 Video Kurgulanıyor (%75)",
            percent=75.0,
            detail=f"16:9 yatay video jenerikler kırpılarak paketleniyor ({start_sec:.1f}s - {end_sec:.1f}s)..."
        )
        ready_items = clip_editor.process_clip_horizontal(
            clip,
            settings=settings,
            cut_start_sec=start_sec,
            cut_end_sec=end_sec
        )

        ready_item = ready_items[0] if ready_items else clip

        # AI Destekli Özgün Paket (Başlık, Açıklama, Etiketler, Kategori, Clickbait Thumbnail)
        video_progress_tracker.update_stage(
            stage="thumbnail",
            stage_label="AI Başlık, SEO & Kapak Görseli (%90)",
            percent=90.0,
            detail="Yatay video için seçili AI modeliyle özgün başlık, SEO açıklaması ve kapak görseli hazırlanıyor..."
        )
        try:
            ai_meta = clip_ai_seo.generate_monetization_metadata(ready_item, ClipTargetFormat.HORIZONTAL, settings=settings)
            if not ready_item.youtube_title:
                ready_item.youtube_title = ai_meta["title"]
            ready_item.youtube_description = ai_meta["description"]
            ready_item.youtube_tags = ai_meta["tags"]
            ready_item.youtube_category_id = ai_meta["category_id"]
            if not ready_item.hook_text and ai_meta.get("hook_text"):
                ready_item.hook_text = ai_meta["hook_text"]
            ready_item.made_for_kids = False
            ready_item.age_restricted = False
            self._add_log(f"AI Özgün Başlık (Yatay): '{ready_item.youtube_title}' (Kategori: {ready_item.youtube_category_id})", "info")
        except Exception as ai_err:
            logger.warning(f"AI monetization metadata failed for horizontal video: {ai_err}")

        # Clickbait Thumbnail Studio (16:9 Kalın Sarı Metinli YouTube Kapağı)
        if settings.auto_generate_thumbnail and ready_item.processed_video_path and os.path.exists(ready_item.processed_video_path):
            base_dir = os.path.dirname(ready_item.processed_video_path)
            stem = os.path.splitext(os.path.basename(ready_item.processed_video_path))[0]
            thumb_file = os.path.join(base_dir, f"{stem}_thumb.jpg")
            sample_time = max(5.0, min(30.0, ready_item.duration * 0.2 if ready_item.duration else 10.0))
            created_thumb = clip_ai_seo.create_clickbait_thumbnail(
                video_path=ready_item.processed_video_path,
                hook_text=ready_item.hook_text or "İNANILMAZ ANLAR!",
                target_format=ClipTargetFormat.HORIZONTAL,
                output_path=thumb_file,
                timestamp_sec=sample_time
            )
            if created_thumb and os.path.exists(created_thumb):
                ready_item.custom_thumbnail_path = created_thumb

        # DB'ye tüm metadata'yı kaydet
        clip_db.update_clip_metadata(
            ready_item.clip_id,
            youtube_title=ready_item.youtube_title,
            youtube_description=ready_item.youtube_description,
            youtube_tags=ready_item.youtube_tags,
            youtube_category_id=ready_item.youtube_category_id,
            hook_text=ready_item.hook_text,
            made_for_kids=False,
            age_restricted=False,
            custom_thumbnail_path=ready_item.custom_thumbnail_path
        )

        video_progress_tracker.update_stage(
            stage="ready",
            stage_label="Stokta Hazırlandı (%100)",
            percent=100.0,
            detail="Yatay video kurgusu ve AI paketi tamamlandı, stok havuzuna eklendi."
        )

        # Taslak olarak onceden YouTube'a yukleme kontrolu
        if getattr(settings, "pre_upload_as_draft", True) and shorts_publisher.is_youtube_ready():
            try:
                self._add_log(f"Yatay video YouTube'a taslak olarak yukleniyor (yayin saati geldiginde saniyesinde herkese acik yapilacak)...", "info")
                draft_id = shorts_publisher.pre_upload_draft(ready_item, settings=settings)
                if draft_id:
                    self._add_log(f"Yatay taslak YouTube'da hazir! Video ID: {draft_id}", "info")
            except Exception as pre_err:
                logger.warning(f"Horizontal pre-upload draft failed: {pre_err}")

        if len(ready_items) > 1:
            self._add_log(
                f"VOD {len(ready_items)} parcaya bolundu; parcalar stokta READY olarak bekliyor.",
                "info"
            )
        return ready_item

    def _cleanup_raw(self, raw_path: str, clip: ClipItem, settings: ClipPipelineSettings):
        """keep_raw_files kapaliysa ve ayni kaynagin bekleyen ikizi yoksa ham dosyayi siler."""
        if settings.keep_raw_files or not raw_path or not os.path.exists(raw_path):
            return
        source_key = clip.source_key or clip.clip_id
        if clip_db.source_siblings_pending(source_key, clip.clip_id):
            self._add_log(
                f"Ham dosya korunuyor (ayni kaynagin bekleyen ikizi var): {os.path.basename(raw_path)}",
                "info"
            )
            return
        try:
            os.remove(raw_path)
            self._add_log(f"Ham dosya silindi: {os.path.basename(raw_path)}", "info")
        except Exception as e:
            logger.warning(f"Could not remove raw file {raw_path}: {e}")

    # ================= Arka Plan Dongusu =================

    def _run_loop(self):
        """Kesif -> Stok isleme -> Kotali yayinlama dongusu."""
        last_discovery_ts = 0.0
        last_publish_ts = 0.0

        while not self._stop_event.is_set():
            try:
                settings = clip_db.get_settings()
                now = time.time()

                # Durdurma istendi ise yeni faz baslatma (ozellikle yukleme baslamasin)
                if self._stop_event.is_set():
                    break

                # 1. Kesif
                discovery_interval = max(60, settings.check_interval_minutes * 60)
                if now - last_discovery_ts >= discovery_interval:
                    self._current_task_info = "Keşif: yayincilar taraniyor"
                    self._discover_all(settings)
                    last_discovery_ts = now

                if self._stop_event.is_set():
                    break

                # 2. Stok isleme
                self._current_task_info = "Stok isleniyor"
                self._process_stock(settings)

                if self._stop_event.is_set():
                    break

                # 3. Kotali yayinlama
                publish_interval = max(60, settings.publish_interval_minutes * 60)
                if now - last_publish_ts >= publish_interval:
                    self._current_task_info = "Kota kontrolu ve yayinlama"
                    self._publish_tick(settings)
                    last_publish_ts = now

                self._current_task_info = "Beklemede (Idle)"

                # Saniyeler bazinda kontrol ederek bekle (durdurma sinyalini hemen algilar)
                for _ in range(10):
                    if self._stop_event.is_set():
                        break
                    time.sleep(1)

            except Exception as e:
                self._add_log(f"Zamanlayici dongu hatasi: {str(e)}", "error")
                time.sleep(10)

        self._is_running = False

    def _discover_all(self, settings: ClipPipelineSettings):
        """Aktif yayincilari tarar ve yeni icerikleri stoga (DISCOVERED) yazar."""
        streamers = clip_db.list_streamers(active_only=True)

        if not streamers:
            self._current_task_info = "Yayinci listesi bos. Yayinci bekleniyor."
            self._add_log("Takip edilecek aktif yayinci bulunamadi.", "warning")
            return

        self._add_log(f"{len(streamers)} aktif yayinci taraniyor...", "info")
        for streamer in streamers:
            if self._stop_event.is_set():
                break

            # Yayinci bazli tarama araligi (last_checked_at uzerinden)
            if streamer.last_checked_at:
                elapsed_min = (datetime.utcnow() - streamer.last_checked_at).total_seconds() / 60.0
                if elapsed_min < max(5, streamer.check_interval_minutes):
                    continue

            self._current_task_info = f"{streamer.channel_name} taraniyor..."
            self._add_log(f"Taraniyor: {streamer.platform.value} / {streamer.channel_name}", "info")

            try:
                items = clip_downloader.fetch_streamer_feed(streamer, settings)
                new_items = [i for i in items if not clip_db.is_clip_processed(i.clip_id)]

                added = 0
                for item in new_items:
                    # Kalici olarak basarisiz olanlari (son 6 saat) yeniden stoka ekleme
                    if clip_db.clip_failed_recently(item.clip_id, hours=6):
                        continue
                    clip_db.save_clip(item)
                    added += 1

                if added:
                    shorts_ct = sum(1 for i in new_items if i.target_format == ClipTargetFormat.SHORTS)
                    horiz_ct = sum(1 for i in new_items if i.target_format == ClipTargetFormat.HORIZONTAL)
                    self._add_log(
                        f"{streamer.channel_name}: {added} yeni icerik stoga eklendi "
                        f"(shorts: {shorts_ct}, yatay: {horiz_ct})",
                        "info"
                    )
                else:
                    self._add_log(f"{streamer.channel_name}: yeni icerik yok.", "info")

                clip_db.update_streamer_checked(streamer.platform.value, streamer.channel_name)

            except Exception as ex:
                self._add_log(f"{streamer.channel_name} tarama hatasi: {str(ex)[:150]}", "error")

    def _process_stock(self, settings: ClipPipelineSettings):
        """
        Stok hedeflerine gore (Yatay: 3, Shorts: 6) sirali olarak 1'er 1'er video indirip isler.
        Ayni anda birden fazla video indirilmez veya kurgulanmaz.
        """
        target_h = max(1, settings.target_stock_horizontal)
        target_s = max(1, settings.target_stock_shorts)

        ready_h = clip_db.count_clips(status=ClipStatus.READY.value, target_format=ClipTargetFormat.HORIZONTAL.value)
        ready_s = clip_db.count_clips(status=ClipStatus.READY.value, target_format=ClipTargetFormat.SHORTS.value)

        # 1. Yatay stok oncelikli kontrolu (hedef: 3 hazir video)
        if ready_h < target_h and not self._stop_event.is_set():
            next_h = clip_db.next_discovered_clip(ClipTargetFormat.HORIZONTAL.value, settings=settings)
            if next_h:
                self._add_log(
                    f"Yatay stok eksik ({ready_h}/{target_h}). 1 video sirayla indiriliyor ve isleniyor: '{next_h.title[:45]}'",
                    "info"
                )
                try:
                    self.run_single_pipeline_for_clip(next_h, settings)
                except Exception as ex:
                    self._add_log(f"Yatay stok isleme hatasi ({next_h.clip_id}): {str(ex)[:120]}", "error")
                return  # 1 video tamamlandi, diger videolara sirayla gecmek uzere donguye don

        # 2. Shorts stok kontrolu (hedef: 6 hazir video)
        if ready_s < target_s and not self._stop_event.is_set():
            next_s = clip_db.next_discovered_clip(ClipTargetFormat.SHORTS.value, settings=settings)
            if next_s:
                self._add_log(
                    f"Shorts stok eksik ({ready_s}/{target_s}). 1 video sirayla indiriliyor ve isleniyor: '{next_s.title[:45]}'",
                    "info"
                )
                try:
                    self.run_single_pipeline_for_clip(next_s, settings)
                except Exception as ex:
                    self._add_log(f"Shorts stok isleme hatasi ({next_s.clip_id}): {str(ex)[:120]}", "error")
                return  # 1 video tamamlandi, diger videolara sirayla gecmek uzere donguye don

    def _publish_tick(self, settings: ClipPipelineSettings):
        """Gunluk kota ve yayin penceresi dahilinde stoktan YouTube'a yukler."""
        if self._stop_event.is_set():
            return
        with self._pipeline_lock:
            try:
                summary = clip_publisher.run_publish_tick(settings)
            except Exception as e:
                self._add_log(f"Yayinlama hatasi: {str(e)[:150]}", "error")
                return

        published = summary.get("published", [])
        if published:
            for info in published:
                self._add_log(
                    f"YouTube'a yuklendi: {info['title']} -> https://youtu.be/{info['youtube_video_id']}",
                    "info"
                )
        else:
            skipped = summary.get("skipped", {})
            reason = skipped.get("reason")
            if not reason:
                reason = ", ".join(f"{k}: {v}" for k, v in skipped.items()) if skipped else "uygun video yok"
            self._add_log(f"Yayinlama atlandi ({reason}).", "info")


clip_scheduler = ClipSchedulerService()
