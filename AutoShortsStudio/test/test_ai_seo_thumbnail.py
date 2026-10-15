import os
import unittest
from unittest.mock import patch, MagicMock
from PIL import Image

from app.models.clip_schema import ClipItem, ClipTargetFormat, PlatformType, ClipStatus, ClipPipelineSettings
from app.services.clip_ai_seo import clip_ai_seo, VIRAL_HOOK_TEMPLATES
from app.services.shorts_publisher import shorts_publisher
from app.services.clip_db import clip_db


class TestAISEOThumbnailAndMonetization(unittest.TestCase):

    def setUp(self):
        self.clip = ClipItem(
            clip_id="test_clip_seo_123",
            platform=PlatformType.TWITCH,
            channel_name="elraenn",
            title="elraenn izliyor komik anlar",
            url="https://twitch.tv/clip/123",
            duration=45.0,
            view_count=50000,
            target_format=ClipTargetFormat.SHORTS
        )

    def test_monetization_metadata_not_identical_to_original(self):
        """Orijinal başlık asla birebir kullanılmamalıdır (Tekrarlanan içerik koruması)."""
        meta = clip_ai_seo.generate_monetization_metadata(self.clip, ClipTargetFormat.SHORTS)
        self.assertIn("title", meta)
        self.assertIn("description", meta)
        self.assertIn("tags", meta)
        self.assertIn("category_id", meta)
        self.assertIn("hook_text", meta)

        # Orijinal başlıkla birebir aynı olamaz
        self.assertNotEqual(meta["title"].lower(), self.clip.title.lower())
        # Shorts için #Shorts içermeli
        self.assertTrue("#shorts" in meta["title"].lower() or "#Shorts" in meta["title"])
        # En az 5 etiket içermeli
        self.assertGreaterEqual(len(meta["tags"]), 5)
        # Kategori varsayılan 20 veya 24 olmalı
        self.assertIn(meta["category_id"], ["20", "22", "24"])

    def test_horizontal_monetization_metadata(self):
        """Yatay video için #Shorts içermeyen özgün başlık üretilmelidir."""
        horiz_clip = ClipItem(
            clip_id="test_horiz_seo_456",
            platform=PlatformType.YOUTUBE,
            channel_name="rraenee",
            title="rraenee gta 5 rp efsane anlar",
            url="https://youtube.com/watch?v=456",
            duration=350.0,
            view_count=120000,
            target_format=ClipTargetFormat.HORIZONTAL
        )
        meta = clip_ai_seo.generate_monetization_metadata(horiz_clip, ClipTargetFormat.HORIZONTAL)
        self.assertNotIn("#shorts", meta["title"].lower())
        self.assertIn("rraenee", meta["title"].lower())
        self.assertNotEqual(meta["title"].lower(), horiz_clip.title.lower())

    def test_clickbait_thumbnail_pillow_rendering(self):
        """Pillow ile kanca yazılı clickbait thumbnail üretimi testi."""
        test_dir = os.path.join(os.path.dirname(__file__), "..", "storage", "clips", "temp")
        os.makedirs(test_dir, exist_ok=True)
        dummy_video = os.path.join(test_dir, "dummy_video.mp4")
        with open(dummy_video, "wb") as f:
            f.write(b"fake_video_data")

        raw_frame = os.path.join(test_dir, "test_raw_base.jpg")
        out_thumb = os.path.join(test_dir, "test_clickbait_out.jpg")

        img = Image.new("RGB", (1920, 1080), color=(30, 40, 60))
        img.save(raw_frame)

        with patch("app.services.hype_detector.hype_detector.extract_thumbnail_frame") as mock_extract:
            def fake_extract(vpath, sec, out_f):
                img.save(out_f)
                return True
            mock_extract.side_effect = fake_extract

            res = clip_ai_seo.create_clickbait_thumbnail(
                video_path=dummy_video,
                hook_text="İNANILMAZ ANLAR!",
                target_format=ClipTargetFormat.HORIZONTAL,
                output_path=out_thumb,
                timestamp_sec=2.0
            )

            self.assertIsNotNone(res)
            self.assertTrue(os.path.exists(out_thumb))
            with Image.open(out_thumb) as saved_img:
                self.assertEqual(saved_img.size, (1280, 720))

        # Temizlik
        for f in [dummy_video, raw_frame, out_thumb]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass

    def test_kids_and_age_restriction_always_false(self):
        """Çocuklara özel ve 18+ yaş sınırı kesinlikle False olmalı."""
        item = ClipItem(
            clip_id="test_kids_check",
            platform=PlatformType.TWITCH,
            channel_name="test",
            title="test",
            url="test"
        )
        self.assertFalse(item.made_for_kids)
        self.assertFalse(item.age_restricted)

        settings = ClipPipelineSettings()
        self.assertFalse(settings.made_for_kids)
        self.assertFalse(settings.age_restricted)

    @patch("app.services.shorts_publisher.upload_to_youtube")
    def test_manual_publish_bypasses_prime_time(self, mock_upload):
        """Manuel yüklemede Prime Time veya kota kısıtlamasına bakılmaksızın upload çağrılmalı."""
        mock_upload.return_value = {"success": True, "video_id": "test_yt_manual_999"}

        temp_video_path = os.path.abspath("test_temp_manual_video.mp4")
        with open(temp_video_path, "wb") as f:
            f.write(b"dummy_mp4_bytes")

        clip = ClipItem(
            clip_id="test_manual_pub_clip",
            platform=PlatformType.TWITCH,
            channel_name="elraenn",
            title="Manuel Yükleme Test Klibi",
            url="https://twitch.tv/clip/manual",
            processed_video_path=temp_video_path,
            target_format=ClipTargetFormat.HORIZONTAL,
            status=ClipStatus.READY
        )
        clip_db.save_clip(clip)

        # shorts_publisher doğrudan çağrılır
        vid = shorts_publisher.publish_clip(clip)
        self.assertEqual(vid, "test_yt_manual_999")
        mock_upload.assert_called_once()
        call_kwargs = mock_upload.call_args[1]
        self.assertFalse(call_kwargs["made_for_kids"])
        self.assertFalse(call_kwargs["age_restricted"])

        # DB ve dosya temizliği
        clip_db.delete_clip(clip.clip_id)
        if os.path.exists(temp_video_path):
            try:
                os.remove(temp_video_path)
            except Exception:
                pass

    def test_custom_ai_seo_prompt_usage(self):
        """Kullanıcının belirlediği özel AI sistem promptunun kullanıldığını doğrular."""
        custom_prompt = "ÖZEL PROMPT: {streamer} yayınından harika klip! Format: {format}. Konu: {title}"
        settings = ClipPipelineSettings(ai_seo_system_prompt=custom_prompt)

        with patch("app.services.llm._generate_response") as mock_llm:
            mock_llm.return_value = '{"title": "Özel Başlık #Shorts", "description": "Özel Açıklama", "tags": ["özel"], "category_id": "20", "hook_text": "ÖZEL"}'
            meta = clip_ai_seo.generate_monetization_metadata(self.clip, ClipTargetFormat.SHORTS, settings=settings)

            self.assertTrue(mock_llm.called)
            called_prompt = mock_llm.call_args[0][0]
            self.assertIn("ÖZEL PROMPT: elraenn yayınından harika klip!", called_prompt)
            self.assertIn("Format: Dikey YouTube Shorts (9:16)", called_prompt)
            self.assertEqual(meta["title"], "Özel Başlık #Shorts")

    def test_default_ai_seo_prompt_when_not_set(self):
        """Özel prompt boş olduğunda varsayılan viral sistem promptunun kullanıldığını doğrular."""
        settings = ClipPipelineSettings(ai_seo_system_prompt="")

        with patch("app.services.llm._generate_response") as mock_llm:
            mock_llm.return_value = '{"title": "Varsayılan Başlık #Shorts", "description": "Açıklama", "tags": ["tag"], "category_id": "20", "hook_text": "HOOK"}'
            meta = clip_ai_seo.generate_monetization_metadata(self.clip, ClipTargetFormat.SHORTS, settings=settings)

            self.assertTrue(mock_llm.called)
            called_prompt = mock_llm.call_args[0][0]
            self.assertIn("Sen YouTube keşfet algoritması ve viral içerik uzmanısın", called_prompt)
            self.assertIn("elraenn", called_prompt)

    def test_pre_upload_draft(self):
        """Klibin önceden taslak (private) olarak YouTube'a yüklendiğini doğrular."""
        temp_video = os.path.abspath("test_draft_video.mp4")
        with open(temp_video, "wb") as f:
            f.write(b"fake_video_bytes")

        draft_clip = ClipItem(
            clip_id="test_draft_clip_1",
            platform=PlatformType.TWITCH,
            channel_name="elraenn",
            title="Taslak Ön Yükleme Testi",
            url="https://twitch.tv/clip/draft1",
            processed_video_path=temp_video,
            target_format=ClipTargetFormat.SHORTS,
            status=ClipStatus.READY
        )
        clip_db.save_clip(draft_clip)

        try:
            with patch("app.services.shorts_publisher.shorts_publisher.is_youtube_ready", return_value=True), \
                 patch("app.services.shorts_publisher.upload_to_youtube") as mock_upload:
                mock_upload.return_value = {"success": True, "video_id": "draft_yt_12345"}
                vid = shorts_publisher.pre_upload_draft(draft_clip)
                self.assertEqual(vid, "draft_yt_12345")
                self.assertEqual(draft_clip.youtube_video_id, "draft_yt_12345")
                self.assertEqual(draft_clip.youtube_status, "private")
                self.assertEqual(draft_clip.status, ClipStatus.READY)  # Hala hazır stokta kalır!

                # upload_to_youtube'a privacy_status='private' gittiğini doğrula
                mock_upload.assert_called_once()
                self.assertEqual(mock_upload.call_args[1].get("privacy_status"), "private")
        finally:
            clip_db.delete_clip(draft_clip.clip_id)
            if os.path.exists(temp_video):
                os.remove(temp_video)

    def test_instant_publish_for_preuploaded_draft(self):
        """Önceden taslak olarak yüklenmiş videonun yayın zamanında anında public yapıldığını doğrular."""
        temp_video = os.path.abspath("test_instant_video.mp4")
        with open(temp_video, "wb") as f:
            f.write(b"fake_video_bytes")

        instant_clip = ClipItem(
            clip_id="test_instant_clip_1",
            platform=PlatformType.TWITCH,
            channel_name="elraenn",
            title="Anında Yayınlama Testi",
            url="https://twitch.tv/clip/instant1",
            processed_video_path=temp_video,
            target_format=ClipTargetFormat.SHORTS,
            status=ClipStatus.READY,
            youtube_video_id="draft_yt_99999",
            youtube_status="private"
        )
        clip_db.save_clip(instant_clip)

        try:
            with patch("app.services.youtube_uploader.youtube_uploader_service.update_video_privacy") as mock_privacy:
                mock_privacy.return_value = {"success": True, "video_id": "draft_yt_99999", "privacy_status": "public"}
                settings = ClipPipelineSettings(privacy_status="public")
                vid = shorts_publisher.publish_clip(instant_clip, settings=settings)

                self.assertEqual(vid, "draft_yt_99999")
                mock_privacy.assert_called_once_with("draft_yt_99999", "public")
                self.assertEqual(instant_clip.status, ClipStatus.PUBLISHED)
                self.assertEqual(instant_clip.youtube_status, "public")
        finally:
            clip_db.delete_clip(instant_clip.clip_id)
            if os.path.exists(temp_video):
                os.remove(temp_video)

    def test_youtube_upload_declaration_defaults_false(self):
        """YouTube yükleme beyanında ücretli tanıtım ve AI sentetik medya sorularının Hayır (False) olarak işaretlendiğini doğrular."""
        temp_video = os.path.abspath("test_declaration_video.mp4")
        with open(temp_video, "wb") as f:
            f.write(b"fake_video_bytes")

        decl_clip = ClipItem(
            clip_id="test_decl_clip_1",
            platform=PlatformType.TWITCH,
            channel_name="elraenn",
            title="Beyan Testi",
            url="https://twitch.tv/clip/decl1",
            processed_video_path=temp_video,
            target_format=ClipTargetFormat.SHORTS,
            status=ClipStatus.READY,
        )
        clip_db.save_clip(decl_clip)

        try:
            with patch("app.services.shorts_publisher.shorts_publisher.is_youtube_ready", return_value=True), \
                 patch("app.services.shorts_publisher.upload_to_youtube") as mock_upload:
                mock_upload.return_value = {"success": True, "video_id": "decl_yt_123"}
                shorts_publisher._upload(
                    decl_clip,
                    title="Beyan Testi #Shorts",
                    description="Açıklama",
                    tags=["Shorts"],
                    privacy="private",
                    video_aspect="9:16"
                )
                mock_upload.assert_called_once()
                call_kwargs = mock_upload.call_args[1]
                self.assertEqual(call_kwargs.get("has_paid_promotion"), False)
                self.assertEqual(call_kwargs.get("contains_synthetic_media"), False)
                self.assertEqual(call_kwargs.get("made_for_kids"), False)

            # Şimdi doğrudan youtube_uploader.upload_video mock'u ile YouTube API insert body'sini doğrula
            from app.services.youtube_uploader import youtube_uploader_service
            with patch.object(youtube_uploader_service, "get_credentials") as mock_creds, \
                 patch("googleapiclient.discovery.build") as mock_build, \
                 patch("googleapiclient.http.MediaFileUpload") as mock_media:

                mock_creds.return_value = MagicMock()
                mock_youtube = MagicMock()
                mock_build.return_value = mock_youtube

                mock_insert_req = MagicMock()
                mock_youtube.videos().insert.return_value = mock_insert_req
                mock_insert_req.next_chunk.return_value = (None, {"id": "yt_success_123"})

                res = youtube_uploader_service.upload_video(
                    video_path=temp_video,
                    title="Otomatik Beyan Testi",
                    description="Test desc",
                    privacy_status="private",
                    has_paid_promotion=False,
                    contains_synthetic_media=False,
                )

                self.assertTrue(res.get("success"))
                mock_youtube.videos().insert.assert_called_once()
                insert_args = mock_youtube.videos().insert.call_args[1]

                parts_sent = insert_args.get("part", "")
                self.assertIn("paidProductPlacementDetails", parts_sent)
                self.assertIn("status", parts_sent)

                body_sent = insert_args.get("body", {})
                self.assertEqual(body_sent["paidProductPlacementDetails"]["hasPaidProductPlacement"], False)
                self.assertEqual(body_sent["status"]["containsSyntheticMedia"], False)
                self.assertEqual(body_sent["status"]["selfDeclaredMadeForKids"], False)

        finally:
            clip_db.delete_clip(decl_clip.clip_id)
            if os.path.exists(temp_video):
                os.remove(temp_video)


if __name__ == "__main__":
    unittest.main()

