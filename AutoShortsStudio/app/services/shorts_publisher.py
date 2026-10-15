import os
from datetime import datetime
from typing import Optional, Dict, Any
from loguru import logger

from app.models.clip_schema import ClipItem, ClipStatus, ClipPipelineSettings, ClipTargetFormat
from app.services.clip_db import clip_db
from app.services.youtube_uploader import youtube_uploader_service, upload_to_youtube
from app.services import llm


class ShortsPublisherService:
    def __init__(self):
        pass

    def is_youtube_ready(self) -> bool:
        """YouTube API kimlik dogrulamasi yapilmis mi kontrol eder."""
        token_file = youtube_uploader_service.credentials_file
        if not os.path.isabs(token_file):
            from app.utils import utils
            token_file = os.path.join(utils.root_dir(), token_file)
        return os.path.exists(token_file) and os.path.getsize(token_file) > 10

    # ================= Metadata Yardimcilari =================

    def _generate_llm_metadata(self, clip: ClipItem, platform_spec: str, settings: Optional[ClipPipelineSettings] = None) -> Optional[Dict[str, Any]]:
        """LLM ile viral baslik/aciklama/etiket uretir; basarisizsa None doner."""
        try:
            from app.services.clip_ai_seo import clip_ai_seo
            is_shorts = (platform_spec == "youtube_shorts")
            target_format = ClipTargetFormat.SHORTS if is_shorts else ClipTargetFormat.HORIZONTAL
            ai_pkg = clip_ai_seo.generate_monetization_metadata(clip, target_format, settings=settings)
            if ai_pkg and ai_pkg.get("title"):
                return {
                    "title": ai_pkg.get("title"),
                    "caption": ai_pkg.get("description"),
                    "hashtags": ai_pkg.get("tags") or []
                }
        except Exception as e:
            logger.warning(f"clip_ai_seo metadata generation failed, falling back to llm.generate_social_metadata: {e}")

        try:
            metadata = llm.generate_social_metadata(
                video_subject=clip.title,
                video_script=clip.transcript or "",
                language="tr",
                platform=platform_spec,
            )
            if metadata and metadata.get("title"):
                return metadata
        except Exception as e:
            logger.warning(f"LLM metadata generation failed, falling back to template: {e}")
        return None

    @staticmethod
    def _clean_hashtags(hashtags) -> list:
        """'#etiket' bicimini YouTube tags listesine uygun hale getirir."""
        result = []
        for tag in (hashtags or []):
            cleaned = str(tag).strip().lstrip("#").strip()
            if cleaned:
                result.append(cleaned)
        return result

    def _attribution_block(self, clip: ClipItem) -> str:
        return f"\n\nYayıncı: {clip.channel_name} ({clip.platform.value})\nOrijinal: {clip.url}"

    # ================= Yayinlama =================

    def publish_clip(
        self,
        clip: ClipItem,
        settings: Optional[ClipPipelineSettings] = None
    ) -> Optional[str]:
        """
        Klibin hedef formatina gore (Shorts / yatay video) YouTube'a yukler veya
        onceden taslak (private) olarak yuklenmisse aninda 'public' (Herkese Acik) yapar.
        """
        if settings is None:
            settings = clip_db.get_settings()

        # Eger video onceden YouTube'a taslak olarak yuklenmisse, yeni bir dosya upload'u yapma!
        # Dogrudan gizliligi 'public' yap (200ms icinde yayinda).
        target_privacy = (settings.privacy_status or "public").lower()
        if clip.youtube_video_id and str(clip.youtube_status).lower() in ("private", "unlisted", "draft"):
            logger.info(
                f"[InstantPublish] '{clip.title}' zaten YouTube'da taslak yuklu (ID: {clip.youtube_video_id}). "
                f"Gizlilik aninda '{target_privacy}' yapiliyor..."
            )
            update_res = youtube_uploader_service.update_video_privacy(clip.youtube_video_id, target_privacy)
            if update_res.get("success"):
                published_now = datetime.now()
                clip.youtube_status = target_privacy
                clip.status = ClipStatus.PUBLISHED
                clip.published_at = published_now
                clip_db.update_clip_status(
                    clip.clip_id,
                    ClipStatus.PUBLISHED,
                    youtube_status=target_privacy,
                    published_at=published_now
                )
                logger.success(f"[InstantPublish] Taslak video basariyla '{target_privacy}' yapildi: https://youtu.be/{clip.youtube_video_id}")
                return clip.youtube_video_id
            else:
                logger.warning(f"[InstantPublish] Taslak privacy guncelleme basarisiz ({update_res.get('error')}), normal yukleme deneniyor...")

        if clip.target_format == ClipTargetFormat.HORIZONTAL:
            return self.publish_clip_horizontal(clip, settings=settings)
        return self.publish_clip_to_shorts(clip, settings=settings)

    def pre_upload_draft(
        self,
        clip: ClipItem,
        settings: Optional[ClipPipelineSettings] = None
    ) -> Optional[str]:
        """
        Kurgulanmis READY videoyu YouTube'a tum metadata'si (baslik, aciklama, etiket, thumbnail)
        ile birlikte 'private' (taslak/gizli) olarak onceden yukler.
        Klip durumu READY olarak kalir ancak youtube_video_id ve youtube_status='private' atanir.
        """
        if not self.is_youtube_ready():
            return None
        if clip.youtube_video_id:
            return clip.youtube_video_id
        if not clip.processed_video_path or not os.path.exists(clip.processed_video_path):
            return None

        if settings is None:
            settings = clip_db.get_settings()

        title = clip.youtube_title or clip.title
        is_shorts = (clip.target_format == ClipTargetFormat.SHORTS)
        video_aspect = "9:16" if is_shorts else "16:9"

        if is_shorts and "#Shorts" not in title and "#shorts" not in title:
            title = f"{title[:85]} #Shorts"

        description = clip.youtube_description or (
            f"Klip: {clip.title}\nYayıncı: {clip.channel_name} ({clip.platform.value})\n"
            f"Orijinal Link: {clip.url}\n\n#Shorts #Twitch #Kick #Highlights"
        )
        tags = clip.youtube_tags or ["Shorts", clip.channel_name or "Streamer", "Gaming"]
        thumb_path = clip.custom_thumbnail_path
        category_id = clip.youtube_category_id or settings.youtube_category_id or "20"

        logger.info(f"[PreUploadDraft] '{clip.title}' YouTube'a taslak (private) olarak onceden yukleniyor...")
        try:
            paid_promo = getattr(clip, "has_paid_promotion", None)
            if paid_promo is None:
                paid_promo = getattr(settings, "has_paid_promotion", False)

            synth_media = getattr(clip, "contains_synthetic_media", None)
            if synth_media is None:
                synth_media = getattr(settings, "contains_synthetic_media", False)

            result = upload_to_youtube(
                video_path=clip.processed_video_path,
                title=title,
                description=description,
                tags=tags,
                privacy_status="private",
                video_aspect=video_aspect,
                thumbnail_path=thumb_path,
                category_id=category_id,
                made_for_kids=False,
                age_restricted=False,
                has_paid_promotion=bool(paid_promo),
                contains_synthetic_media=bool(synth_media),
            )

            video_id = None
            if isinstance(result, dict) and result.get("success"):
                video_id = result.get("video_id")
            elif isinstance(result, str):
                video_id = result

            if video_id:
                clip.youtube_video_id = video_id
                clip.youtube_status = "private"
                clip_db.update_clip_metadata(
                    clip.clip_id,
                    youtube_video_id=video_id,
                    youtube_status="private",
                    youtube_title=title,
                    youtube_description=description,
                    youtube_tags=tags
                )
                logger.success(f"[PreUploadDraft] Video basariyla taslak yuklendi! ID: {video_id} (Yayin saati geldiginde saniyesinde Herkese Acik yapilacak)")
                return video_id
        except Exception as e:
            logger.warning(f"[PreUploadDraft] Taslak yukleme hatasi: {e}")
        return None

    def publish_clip_to_shorts(
        self,
        clip: ClipItem,
        settings: Optional[ClipPipelineSettings] = None
    ) -> Optional[str]:
        """
        Islenmis 9:16 klibi YouTube Shorts olarak yukler.
        Basarili olursa YouTube Video ID dondurur.
        """
        if not clip.processed_video_path or not os.path.exists(clip.processed_video_path):
            err_msg = f"Processed video not found: {clip.processed_video_path}"
            logger.error(err_msg)
            clip_db.update_clip_status(clip.clip_id, ClipStatus.FAILED, error_message=err_msg)
            raise FileNotFoundError(err_msg)

        if settings is None:
            settings = clip_db.get_settings()

        # 1. Baslik ve aciklama hazirla (once kullanici tarafindan verilen, sonra LLM, sonra sablon)
        title = clip.youtube_title
        description = clip.youtube_description
        tags = clip.youtube_tags

        if not title and settings.ai_title_generation:
            metadata = self._generate_llm_metadata(clip, platform_spec="youtube_shorts", settings=settings)
            if metadata:
                title = metadata.get("title")
                if not description:
                    caption = metadata.get("caption") or ""
                    description = caption + self._attribution_block(clip)
                if not tags:
                    tags = self._clean_hashtags(metadata.get("hashtags"))

        if not title:
            title = clip.title
        if "#Shorts" not in title and "#shorts" not in title:
            title = f"{title[:85]} #Shorts"

        if not description:
            description = (
                f"Klip: {clip.title}\nYayıncı: {clip.channel_name} ({clip.platform.value})\n"
                f"Orijinal Link: {clip.url}\n\n#Shorts #Twitch #Kick #Highlights"
            )
        if not tags:
            tags = ["Shorts", "Twitch", "Kick", clip.channel_name, "Gaming"]
        privacy = settings.privacy_status or "private"

        return self._upload(clip, title, description, tags, privacy, video_aspect="9:16", settings=settings)

    def publish_clip_horizontal(
        self,
        clip: ClipItem,
        settings: Optional[ClipPipelineSettings] = None
    ) -> Optional[str]:
        """
        Islenmis yatay (16:9) videoyu normal YouTube videosu olarak yukler.
        Altyazi eklenmemis goruntuyu oldugu gibi kullanir; #Shorts etiketi kullanilmaz.
        """
        if not clip.processed_video_path or not os.path.exists(clip.processed_video_path):
            err_msg = f"Processed video not found: {clip.processed_video_path}"
            logger.error(err_msg)
            clip_db.update_clip_status(clip.clip_id, ClipStatus.FAILED, error_message=err_msg)
            raise FileNotFoundError(err_msg)

        if settings is None:
            settings = clip_db.get_settings()

        title = clip.youtube_title
        description = clip.youtube_description
        tags = clip.youtube_tags

        if not title and settings.ai_title_generation:
            metadata = self._generate_llm_metadata(clip, platform_spec="youtube_video", settings=settings)
            if metadata:
                title = metadata.get("title")
                if not description:
                    caption = metadata.get("caption") or ""
                    description = caption + self._attribution_block(clip)
                if not tags:
                    tags = self._clean_hashtags(metadata.get("hashtags"))

        if not title:
            title = f"{clip.title} | {clip.channel_name}"
        if not description:
            description = (
                f"{clip.title}{self._attribution_block(clip)}\n\n"
                f"#Twitch #Kick #Highlights"
            )
        if not tags:
            tags = [clip.channel_name, clip.platform.value.title(), "Highlights", "Gaming"]
        privacy = settings.privacy_status or "private"

        return self._upload(clip, title, description, tags, privacy, video_aspect="16:9", settings=settings)

    def _upload(
        self,
        clip: ClipItem,
        title: str,
        description: str,
        tags,
        privacy: str,
        video_aspect: str,
        settings: Optional[ClipPipelineSettings] = None
    ) -> Optional[str]:
        """Ortak YouTube yukleme ve durum guncelleme akisi."""
        from app.services.video_progress_tracker import video_progress_tracker
        from app.services.hype_detector import hype_detector

        if settings is None:
            settings = clip_db.get_settings()

        logger.info(
            f"Uploading clip '{clip.clip_id}' to YouTube "
            f"({clip.target_format.value}, aspect={video_aspect}, privacy={privacy})..."
        )
        clip_db.update_clip_status(clip.clip_id, ClipStatus.UPLOADING)

        if not video_progress_tracker.get_active_task():
            video_progress_tracker.start_task(
                clip_id=clip.clip_id,
                title=title,
                channel_name=clip.channel_name,
                target_format=clip.target_format.value,
                platform=clip.platform.value,
            )

        video_progress_tracker.update_stage(
            stage="uploading",
            stage_label="YouTube'a Yükleniyor (%95)",
            percent=95.0,
            detail=f"YouTube'a yükleniyor: {title[:45]}"
        )

        # Thumbnail secimi / otomatik uretimi
        thumb_path = clip.custom_thumbnail_path
        if (not thumb_path or not os.path.exists(thumb_path)) and settings.auto_generate_thumbnail:
            try:
                base_dir = os.path.dirname(clip.processed_video_path)
                stem = os.path.splitext(os.path.basename(clip.processed_video_path))[0]
                auto_thumb = os.path.join(base_dir, f"{stem}_thumb.jpg")
                sample_time = max(1.0, clip.cut_start_sec + 2.0 if clip.cut_start_sec else 2.0)
                if hype_detector.extract_thumbnail_frame(clip.processed_video_path, sample_time, auto_thumb):
                    thumb_path = auto_thumb
                    clip.custom_thumbnail_path = auto_thumb
            except Exception as thumb_err:
                logger.warning(f"Auto thumbnail generation failed: {thumb_err}")

        category_id = clip.youtube_category_id or settings.youtube_category_id or "20"
        # Para kazanma (monetization) ve yorumlar için kesinlikle Çocuklara Özel Değil (False)
        # ve 18+ Yaş Sınırı Devre Dışı (False)
        made_for_kids = False
        age_restricted = False

        paid_promo = getattr(clip, "has_paid_promotion", None)
        if paid_promo is None:
            paid_promo = getattr(settings, "has_paid_promotion", False)

        synth_media = getattr(clip, "contains_synthetic_media", None)
        if synth_media is None:
            synth_media = getattr(settings, "contains_synthetic_media", False)

        try:
            result = upload_to_youtube(
                video_path=clip.processed_video_path,
                title=title,
                description=description,
                tags=tags,
                privacy_status=privacy,
                video_aspect=video_aspect,
                thumbnail_path=thumb_path,
                category_id=category_id,
                made_for_kids=False,
                age_restricted=False,
                has_paid_promotion=bool(paid_promo),
                contains_synthetic_media=bool(synth_media),
            )

            # youtube_uploader ya dict dondurur ya da video_id string
            video_id = None
            if isinstance(result, dict):
                if result.get("success") is False or result.get("error"):
                    err_msg = result.get("error", "YouTube yükleme başarısız.")
                    raise RuntimeError(f"YouTube yükleme hatası: {err_msg}")
                video_id = result.get("id") or result.get("video_id")
            elif isinstance(result, str):
                video_id = result

            if not video_id:
                raise RuntimeError(f"YouTube upload did not return a valid video ID. Result: {result}")

            published_now = datetime.now()
            clip.youtube_video_id = video_id
            clip.youtube_title = title
            clip.youtube_description = description
            clip.youtube_tags = tags
            clip.youtube_status = privacy
            clip.status = ClipStatus.PUBLISHED
            clip.published_at = published_now

            clip_db.update_clip_status(
                clip.clip_id,
                ClipStatus.PUBLISHED,
                youtube_video_id=video_id,
                youtube_title=title,
                youtube_description=description,
                youtube_tags=tags,
                youtube_status=privacy,
                custom_thumbnail_path=thumb_path,
                published_at=published_now
            )

            logger.info(f"Clip successfully published to YouTube: https://youtu.be/{video_id}")
            video_progress_tracker.finish_task(
                clip.clip_id,
                success=True,
                result_path=f"https://youtu.be/{video_id}"
            )
            return video_id

        except Exception as e:
            err_msg = f"YouTube upload failed: {str(e)}"
            logger.error(err_msg)
            clip_db.update_clip_status(clip.clip_id, ClipStatus.FAILED, error_message=err_msg)
            video_progress_tracker.finish_task(clip.clip_id, success=False, error=err_msg)
            raise


shorts_publisher = ShortsPublisherService()
