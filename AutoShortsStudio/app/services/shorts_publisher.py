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

    def _generate_llm_metadata(self, clip: ClipItem, platform_spec: str) -> Optional[Dict[str, Any]]:
        """LLM ile viral baslik/aciklama/etiket uretir; basarisizsa None doner."""
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
        """Klibin hedef formatina gore (Shorts / yatay video) YouTube'a yukler."""
        if clip.target_format == ClipTargetFormat.HORIZONTAL:
            return self.publish_clip_horizontal(clip, settings=settings)
        return self.publish_clip_to_shorts(clip, settings=settings)

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
            raise FileNotFoundError(f"Processed video not found: {clip.processed_video_path}")

        if settings is None:
            settings = clip_db.get_settings()

        # 1. Baslik ve aciklama hazirla (once kullanici tarafindan verilen, sonra LLM, sonra sablon)
        title = clip.youtube_title
        description = clip.youtube_description
        tags = clip.youtube_tags

        if not title and settings.ai_title_generation:
            metadata = self._generate_llm_metadata(clip, platform_spec="youtube_shorts")
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

        return self._upload(clip, title, description, tags, privacy, video_aspect="9:16")

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
            raise FileNotFoundError(f"Processed video not found: {clip.processed_video_path}")

        if settings is None:
            settings = clip_db.get_settings()

        title = clip.youtube_title
        description = clip.youtube_description
        tags = clip.youtube_tags

        if not title and settings.ai_title_generation:
            metadata = self._generate_llm_metadata(clip, platform_spec="youtube_video")
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

        return self._upload(clip, title, description, tags, privacy, video_aspect="16:9")

    def _upload(
        self,
        clip: ClipItem,
        title: str,
        description: str,
        tags,
        privacy: str,
        video_aspect: str
    ) -> Optional[str]:
        """Ortak YouTube yukleme ve durum guncelleme akisi."""
        logger.info(
            f"Uploading clip '{clip.clip_id}' to YouTube "
            f"({clip.target_format.value}, aspect={video_aspect}, privacy={privacy})..."
        )
        clip_db.update_clip_status(clip.clip_id, ClipStatus.UPLOADING)

        try:
            result = upload_to_youtube(
                video_path=clip.processed_video_path,
                title=title,
                description=description,
                tags=tags,
                privacy_status=privacy,
                video_aspect=video_aspect
            )

            # youtube_uploader ya dict dondurur ya da video_id string
            video_id = None
            if isinstance(result, dict):
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
                published_at=published_now
            )

            logger.info(f"Clip successfully published to YouTube: https://youtu.be/{video_id}")
            return video_id

        except Exception as e:
            err_msg = f"YouTube upload failed: {str(e)}"
            logger.error(err_msg)
            clip_db.update_clip_status(clip.clip_id, ClipStatus.FAILED, error_message=err_msg)
            raise


shorts_publisher = ShortsPublisherService()
