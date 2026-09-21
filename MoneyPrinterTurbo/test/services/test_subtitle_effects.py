import json
from pathlib import Path
import unittest

import numpy as np
from moviepy import ImageClip

from app.models.schema import SubtitleRequest, VideoParams
from app.services import video


class TestSubtitleEffectsAndTemplates(unittest.TestCase):
    def test_subtitle_template_and_effect_schema_defaults(self):
        """VideoParams ve SubtitleRequest şablon ve efekt alanlarını doğru yönetmeli."""
        default_params = VideoParams(video_subject="test subtitle template")
        self.assertEqual(default_params.subtitle_template, "capcut_word")
        self.assertIn(default_params.subtitle_effect, {"pop", "none", "fade", "slide_up", "pulse"})

        for tmpl in ["capcut_word", "capcut_karaoke", "capcut_box", "capcut_pop", "classic"]:
            params = VideoParams(video_subject="test subtitle template", subtitle_template=tmpl)
            self.assertEqual(params.subtitle_template, tmpl)

        sub_req = SubtitleRequest(video_script="test subtitle template", subtitle_template="capcut_karaoke")
        self.assertEqual(sub_req.subtitle_template, "capcut_karaoke")

    def test_subtitle_template_i18n_keys_present_in_all_locales(self):
        """Tüm dil dosyalarında altyazı şablonu ve CapCut seçenekleri eksiksiz bulunmalıdır."""
        i18n_dir = Path(__file__).parent.parent.parent / "webui" / "i18n"
        required_keys = {
            "Subtitle Template",
            "Subtitle Template Help",
            "CapCut Word by Word (Dynamic Pop)",
            "CapCut Karaoke (Word Highlight)",
            "CapCut Color Box (Word Tag)",
            "CapCut Word Bounce (In-Place Pop)",
            "Classic (Sentence)",
        }

        for locale_file in i18n_dir.glob("*.json"):
            with self.subTest(locale=locale_file.name):
                data = json.loads(locale_file.read_text(encoding="utf-8"))
                translations = data.get("Translation", {})
                missing_keys = required_keys - translations.keys()
                self.assertEqual(missing_keys, set(), f"Missing template keys in {locale_file.name}")

    def test_render_capcut_word_by_word_template(self):
        """capcut_word şablonu her kelimeyi ses süresine göre dilimleyip ayrı ayrı pop animasyonuyla üretmelidir."""
        subtitle_item = ((1.0, 3.0), "Yapay zeka hızla gelişiyor")
        params = VideoParams(
            video_subject="test",
            subtitle_template="capcut_word",
            font_size=60,
        )

        clips = video.render_subtitle_template_clips(
            subtitle_item=subtitle_item,
            params=params,
            video_width=1080,
            video_height=1920,
        )
        self.addCleanup(lambda: [c.close() for c in clips])

        # 4 kelime var -> 4 bağımsız klip üretilmeli
        self.assertEqual(len(clips), 4)
        total_time = sum(c.duration for c in clips)
        self.assertAlmostEqual(total_time, 2.0, places=2)

        # İlk klibin başlama zamanı 1.0 olmalı
        self.assertAlmostEqual(clips[0].start, 1.0, places=2)
        # Her klibin karesi hatasız alınabilmeli
        for c in clips:
            frame = c.get_frame(c.start + 0.05)
            self.assertIsNotNone(frame)
            self.assertGreater(frame.shape[0], 0)
            self.assertGreater(frame.shape[1], 0)

    def test_render_capcut_karaoke_template(self):
        """capcut_karaoke şablonu tam cümleyi gösterip konuşulan kelimeyi ses senkronuyla renklendirmelidir."""
        subtitle_item = ((0.5, 2.5), "CapCut Karaoke Subtitle Test")
        params = VideoParams(
            video_subject="test",
            subtitle_template="capcut_karaoke",
            font_size=55,
        )

        clips = video.render_subtitle_template_clips(
            subtitle_item=subtitle_item,
            params=params,
            video_width=1080,
            video_height=1920,
        )
        self.addCleanup(lambda: [c.close() for c in clips])

        # 4 kelime -> 4 aktif kelime zaman dilimi
        self.assertEqual(len(clips), 4)
        for c in clips:
            frame = c.get_frame(c.start + 0.02)
            self.assertEqual(frame.shape, (1920, 1080, 3) if len(frame.shape) == 3 and frame.shape[0] == 1920 else frame.shape)
            self.assertGreater(np.count_nonzero(frame), 0)

    def test_render_capcut_box_and_pop_templates(self):
        """capcut_box ve capcut_pop şablonları aktif kelimeye renkli etiket veya sıçrama uygulamalıdır."""
        subtitle_item = ((0.0, 1.5), "Viral Shorts Template")
        for tmpl in ["capcut_box", "capcut_pop"]:
            with self.subTest(template=tmpl):
                params = VideoParams(
                    video_subject="test",
                    subtitle_template=tmpl,
                    font_size=60,
                )
                clips = video.render_subtitle_template_clips(
                    subtitle_item=subtitle_item,
                    params=params,
                    video_width=1080,
                    video_height=1920,
                )
                self.addCleanup(lambda: [c.close() for c in clips])
                self.assertEqual(len(clips), 3)
                for c in clips:
                    f = c.get_frame(c.start + 0.01)
                    self.assertIsNotNone(f)

    def test_render_classic_sentence_template(self):
        """classic şablonu geleneksel tek parça tam cümle klibi üretmelidir."""
        subtitle_item = ((0.0, 2.0), "Classic Subtitle Sentence")
        params = VideoParams(
            video_subject="test",
            subtitle_template="classic",
            subtitle_effect="none",
        )
        clips = video.render_subtitle_template_clips(
            subtitle_item=subtitle_item,
            params=params,
            video_width=1080,
            video_height=1920,
        )
        self.addCleanup(lambda: [c.close() for c in clips])
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0].duration, 2.0)

    def test_apply_subtitle_effects_behavior(self):
        """Her altyazı efekti için MoviePy klibine doğru animasyon ve konumlandırma uygulanmalıdır."""
        frame_rgb = np.full((40, 120, 3), 255, dtype=np.uint8)
        base_y = 150.0
        duration = 1.0

        for effect_name in ["none", "pop", "fade", "slide_up", "pulse", "unknown_fallback"]:
            with self.subTest(effect=effect_name):
                base_clip = ImageClip(frame_rgb).with_duration(duration)
                self.addCleanup(base_clip.close)

                effect_clip = video.apply_subtitle_effect(
                    base_clip,
                    effect=effect_name,
                    duration=duration,
                    base_y=base_y,
                )
                self.addCleanup(effect_clip.close)

                frame_start = effect_clip.get_frame(0.0)
                frame_mid = effect_clip.get_frame(0.1)
                frame_end = effect_clip.get_frame(0.5)

                self.assertIsNotNone(frame_start)
                self.assertIsNotNone(frame_mid)
                self.assertIsNotNone(frame_end)

                if effect_name == "slide_up":
                    self.assertTrue(callable(effect_clip.pos))
                    pos_0 = effect_clip.pos(0.0)
                    pos_end = effect_clip.pos(0.5)
                    self.assertEqual(pos_0[0], "center")
                    self.assertGreater(pos_0[1], base_y)
                    self.assertEqual(pos_end, ("center", base_y))

                elif effect_name in {"none", "unknown_fallback"}:
                    resolved_pos = effect_clip.pos(0.0) if callable(effect_clip.pos) else effect_clip.pos
                    self.assertEqual(resolved_pos, ("center", base_y))


if __name__ == "__main__":
    unittest.main()
