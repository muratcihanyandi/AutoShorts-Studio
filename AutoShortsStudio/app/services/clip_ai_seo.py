import os
import re
import json
import random
from typing import Dict, Any, Optional, List
from PIL import Image, ImageDraw, ImageFont
from loguru import logger

from app.models.clip_schema import ClipItem, ClipTargetFormat, ClipPipelineSettings
from app.services import llm
from app.services.hype_detector import hype_detector


VIRAL_HOOK_TEMPLATES = [
    "İNANILMAZ ANLAR! 😱",
    "GÜLMEKTEN YARILDIK! 😂",
    "YOK ARTIK DEDİRTTİ! 🔥",
    "CANLI YAYINDA ŞOK! ⚡",
    "KİMSE BUNU BEKLEMİYORDU! 🤯",
    "TARİHE GEÇEN SAHNE! 💥",
    "YAYININ EN İYİ ANI! 👑",
    "ÇILDIRDI! 🚀",
]

TITLE_TEMPLATES_HORIZONTAL = [
    "{streamer} YAYININDA OLAY ANLAR! - {hook} 😱",
    "{streamer} BUNU İZLEYİNCE ÇILDIRDI! (Efsane Kesit) 🔥",
    "{streamer} CANLI YAYINDA ŞOK OLDU! ({subject})",
    "YAYIN TARİHİNİN EN İYİ ANI! {streamer} - {subject} 💥",
    "{streamer} İLE EN KOMİK DAKİKALAR! ({hook}) 😂",
]

TITLE_TEMPLATES_SHORTS = [
    "{streamer} Çıldırdı! 😱 #Shorts",
    "Bunu Beklemiyordu! 😂 #Shorts",
    "{streamer} Canlı Yayında Şok! 🔥 #Shorts",
    "En Komik An! 💥 #Shorts",
    "Yok Artık Dedirtti! 🤯 #Shorts",
]


DEFAULT_AI_SEO_PROMPT_TEMPLATE = """Sen YouTube keşfet algoritması ve viral içerik uzmanısın. Amacımız bu videonun YouTube'da maksimum izlenme almasını ve para kazanmasını sağlamak.
YouTube 'Tekrarlanan İçerik (Reused Content)' cezasından kaçınmak için ORİJİNAL BAŞLIĞI KESİNLİKLE BİREBİR KULLANMA.

Video Detayları:
- Yayıncı / Kanal: {streamer} ({platform})
- Orijinal Konu / Başlık: {title}
- Transkript / Konuşmalar: {transcript}
- Video Formatı: {format}

GÖREVLER:
1. "title": Türkçe, son derece merak uyandıran (curiosity gap), tıklanma oranı yüksek viral bir başlık.
   - ÖZEL REAKSİYON / İZLEME KURALI: Eğer videoda yayıncı bir şey izliyorsa, bir edite, videoya veya olaya tepki veriyorsa başlığı MUTLAKA "{streamer} [İzlenen Şeyin Konusu] İZLİYOR" şablonunda yaz (Örn: "RRAENEE PUMMEL PARTY EDİTİ İZLİYOR", "ELRAENN EN KOMİK TİKTOKLARI İZLİYOR").
   - İzleme videosu değil doğrudan oyun anı veya sohbet ise merak uyandıran özgün bir başlık koy.
   - {format_rule}
   - Orijinal başlıkla AYNI OLMAMALIDIR!
2. "description": 2-3 paragraflık ilgi çekici video açıklaması. İzleyicilere videodaki anı merak ettir, bir soru sor ve 'Kanala abone olmayı ve videoyu beğenmeyi unutmayın!' şeklinde etkileşim çağrısı yap. Yayıncı atıfını ({streamer}) ekle.
3. "tags": Virgülle ayrılmış 12-15 adet yüksek arama hacimli popüler YouTube etiketi (örn: {streamer}, twitch kesitleri, komik anlar, oyun, eğlence, trend).
4. "category_id": Oyun içeriği ise "20", genel sohbet/eğlence ise "24" yaz.
5. "hook_text": Thumbnail (Kapak) görselinin üzerine büyük harflerle yazılacak en vurucu 2-4 kelimelik kanca metni (Örn: İNANILMAZ ANLAR!, ŞOK OLDU!, BUNU BEKLEMİYORDU!).

ÇIKTI FORMATI:
Yalnızca geçerli bir JSON objesi döndür:
{
  "title": "...",
  "description": "...",
  "tags": ["etiket1", "etiket2", "etiket3"],
  "category_id": "20",
  "hook_text": "..."
}""".strip()


def build_ai_seo_prompt(
    clip: ClipItem,
    is_shorts: bool,
    custom_template: Optional[str] = None
) -> str:
    """Yatay ve dikey videolar için özelleştirilebilir AI SEO ve metadata sistem promptu oluşturur."""
    template = (custom_template or "").strip()
    if not template:
        template = DEFAULT_AI_SEO_PROMPT_TEMPLATE

    streamer = clip.channel_name or "Yayıncı"
    platform = clip.platform.value if hasattr(clip.platform, "value") else str(clip.platform)
    orig_title = clip.title or ""
    transcript = (clip.transcript or "")[:500]
    format_label = "Dikey YouTube Shorts (9:16)" if is_shorts else "Yatay YouTube Videosu (16:9)"
    format_rule = "Sonuna #Shorts ekle." if is_shorts else "Asla #shorts ekleme."

    prompt = template
    prompt = prompt.replace("{streamer}", streamer)
    prompt = prompt.replace("{platform}", platform)
    prompt = prompt.replace("{title}", orig_title)
    prompt = prompt.replace("{transcript}", transcript)
    prompt = prompt.replace("{format}", format_label)
    prompt = prompt.replace("{format_rule}", format_rule)

    if orig_title and orig_title not in prompt and "{title}" not in template:
        prompt += f"\n\nVideo Bilgileri:\nYayıncı: {streamer} ({platform})\nKonu: {orig_title}\nFormat: {format_label}\nTranskript: {transcript}"

    return prompt


class ClipAISEOService:
    """
    YouTube para kazanma (monetization) ve keşfet algoritması için:
    1. %100 Özgün Viral Başlık (Orijinal başlık asla birebir kullanılmaz).
    2. Zengin SEO Açıklaması ve Çağrılar (Subscribe, Like, Comment).
    3. 12-15 Hedef YouTube Etiketi.
    4. Otomatik Kategori Tespiti (Gaming / Entertainment).
    5. Pillow ile Kontrastlı Clickbait Thumbnail Studio (Hype karesi + kalın kanca metni).
    """

    def generate_monetization_metadata(
        self,
        clip: ClipItem,
        target_format: ClipTargetFormat,
        settings: Optional[ClipPipelineSettings] = None
    ) -> Dict[str, Any]:
        """Ayarlarda tanımlı aktif AI API'sini kullanarak videoya özel viral paket üretir."""
        is_shorts = (target_format == ClipTargetFormat.SHORTS)
        platform_key = "youtube_shorts" if is_shorts else "youtube_video"

        # 1. LLM ile Üretim Denemesi
        llm_metadata = self._call_llm_for_metadata(clip, is_shorts, settings=settings)
        if llm_metadata:
            return llm_metadata

        # 2. LLM kapalı veya yanıt veremezse -> Zeki Deterministik Fallback
        return self._generate_fallback_package(clip, is_shorts)

    def _call_llm_for_metadata(
        self,
        clip: ClipItem,
        is_shorts: bool,
        settings: Optional[ClipPipelineSettings] = None
    ) -> Optional[Dict[str, Any]]:
        """Kullanıcının yapılandırdığı LLM sağlayıcısı üzerinden doğrudan çağrı yapar."""
        try:
            streamer = clip.channel_name or "Yayıncı"
            orig_title = clip.title or ""
            custom_template = getattr(settings, "ai_seo_system_prompt", "") if settings else ""
            prompt = build_ai_seo_prompt(clip, is_shorts, custom_template=custom_template)

            resp = llm._generate_response(prompt)
            data = None
            try:
                data = json.loads(llm._strip_code_fence(resp))
            except Exception:
                match = re.search(r"\{.*\}", resp or "", re.DOTALL)
                if match:
                    data = json.loads(match.group())

            if data and data.get("title"):
                tags = data.get("tags") or []
                if isinstance(tags, str):
                    tags = [t.strip().lstrip("#") for t in tags.split(",") if t.strip()]
                else:
                    tags = [str(t).strip().lstrip("#") for t in tags if str(t).strip()]

                title = str(data.get("title")).strip()
                # Orijinal başlıkla aynıysa değiştir
                if title.lower() == orig_title.lower():
                    title = f"{streamer} - {title} 😱"

                if is_shorts and "#Shorts" not in title and "#shorts" not in title:
                    title = f"{title[:85]} #Shorts"

                return {
                    "title": title,
                    "description": str(data.get("description", "")).strip(),
                    "tags": tags[:15],
                    "category_id": str(data.get("category_id") or "20"),
                    "hook_text": str(data.get("hook_text") or random.choice(VIRAL_HOOK_TEMPLATES)).strip()
                }

        except Exception as e:
            logger.warning(f"AI metadata generation error: {e}")
        return None

    def _generate_fallback_package(self, clip: ClipItem, is_shorts: bool) -> Dict[str, Any]:
        """LLM yoksa orijinal başlığı dönüştüren akıllı deterministik motor."""
        streamer = clip.channel_name or "Yayıncı"
        clean_subj = re.sub(r"[#|\[\]]", "", clip.title or "").strip()
        hook = clip.hook_text or random.choice(VIRAL_HOOK_TEMPLATES)

        if is_shorts:
            tmpl = random.choice(TITLE_TEMPLATES_SHORTS)
            title = tmpl.format(streamer=streamer, hook=hook, subject=clean_subj[:35])
        else:
            tmpl = random.choice(TITLE_TEMPLATES_HORIZONTAL)
            title = tmpl.format(streamer=streamer, hook=hook, subject=clean_subj[:35])

        desc = (
            f"{streamer} yayınından en unutulmaz ve heyecanlı anlar!\n\n"
            f"Siz bu an hakkında ne düşünüyorsunuz? Yorumlarda belirtmeyi, "
            f"videoyu beğenmeyi ve yeni videolardan anında haberdar olmak için kanala ABONE OLMAYI unutmayın! 🔔\n\n"
            f"👤 Yayıncı: {streamer} ({clip.platform.value})\n"
            f"🔗 Kaynak: {clip.url}\n\n"
            f"#Gaming #Twitch #Kick #Highlights"
        )

        tags = [
            streamer,
            f"{streamer} izliyor",
            f"{streamer} komik anlar",
            f"{streamer} kesitleri",
            "twitch türkiye",
            "en komik anlar",
            "türkçe gaming",
            "canlı yayın kesitleri",
            "trend",
            "viral video",
            "oyun",
            "eğlence"
        ]
        if is_shorts:
            tags.extend(["shorts", "youtubeshorts"])

        return {
            "title": title[:100],
            "description": desc,
            "tags": tags[:15],
            "category_id": "20",
            "hook_text": hook
        }

    # ================= CLICKBAIT THUMBNAIL STUDIO =================

    def create_clickbait_thumbnail(
        self,
        video_path: str,
        hook_text: str,
        target_format: ClipTargetFormat,
        output_path: str,
        timestamp_sec: float = 2.0
    ) -> Optional[str]:
        """
        Videonun en hareketli anından yüksek çözünürlüklü kare alır ve üzerine
        Pillow ile kalın, yüksek kontrastlı (Sarı/Beyaz, kalın siyah konturlu)
        profesyonel YouTube Clickbait metni bindirir.
        """
        if not video_path or not os.path.exists(video_path):
            return None

        temp_frame = f"{output_path}_raw.jpg"
        try:
            # 1. FFmpeg ile net bir kare çıkar
            if not hype_detector.extract_thumbnail_frame(video_path, timestamp_sec, temp_frame):
                return None

            if not os.path.exists(temp_frame):
                return None

            # 2. PIL ile aç ve hedef boyuta göre düzenle
            with Image.open(temp_frame) as img:
                img = img.convert("RGBA")
                w, h = img.size

                # Shorts için 1080x1920, Yatay için 1280x720 / 1920x1080
                target_w, target_h = (1080, 1920) if target_format == ClipTargetFormat.SHORTS else (1280, 720)
                img = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
                w, h = target_w, target_h

                overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                draw = ImageDraw.Draw(overlay)

                # Üst ve alt karartma gradyanı (Metinlerin her sahnede %100 net okunması için)
                gradient_height = int(h * 0.28)
                for y in range(gradient_height):
                    alpha = int(170 * (1.0 - (y / float(gradient_height))))
                    draw.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))
                    draw.line([(0, h - 1 - y), (w, h - 1 - y)], fill=(0, 0, 0, alpha))

                # Kanca metni (Hook Text)
                clean_hook = (hook_text or "İNANILMAZ ANLAR!").upper().strip()
                # Font bulma (Windows arial / segoe / impact fallback)
                font_size = int(h * 0.075) if target_format == ClipTargetFormat.SHORTS else int(h * 0.12)
                font = self._get_bold_font(font_size)

                # Metni çok satırlıysa düzenle (kelime bazlı bölme)
                words = clean_hook.split()
                lines = []
                current_line = []
                for word in words:
                    current_line.append(word)
                    test_str = " ".join(current_line)
                    bbox = draw.textbbox((0, 0), test_str, font=font)
                    if (bbox[2] - bbox[0]) > (w * 0.88):
                        if len(current_line) > 1:
                            current_line.pop()
                            lines.append(" ".join(current_line))
                            current_line = [word]
                        else:
                            lines.append(test_str)
                            current_line = []
                if current_line:
                    lines.append(" ".join(current_line))

                # Metin dikey konumu (Shorts için üst kısım, Yatay için alt/orta)
                total_text_h = len(lines) * int(font_size * 1.25)
                start_y = int(h * 0.14) if target_format == ClipTargetFormat.SHORTS else int(h * 0.72 - total_text_h / 2)

                # Metinleri kalın sarı (#FFE600) ve kalın siyah konturla çiz
                cur_y = start_y
                for line in lines:
                    bbox = draw.textbbox((0, 0), line, font=font)
                    text_w = bbox[2] - bbox[0]
                    text_x = (w - text_w) // 2

                    stroke_w = max(4, int(font_size * 0.10))
                    # Çift katmanlı gölge efekti
                    draw.text((text_x + 4, cur_y + 4), line, font=font, fill=(0, 0, 0, 220))
                    # Siyah konturlu parlak sarı yazı
                    draw.text(
                        (text_x, cur_y),
                        line,
                        font=font,
                        fill="#FFE600",
                        stroke_width=stroke_w,
                        stroke_fill="#000000"
                    )
                    cur_y += int(font_size * 1.25)

                final_img = Image.alpha_composite(img, overlay).convert("RGB")
                os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
                final_img.save(output_path, "JPEG", quality=92)
                logger.info(f"Clickbait thumbnail created: {output_path}")
                return output_path

        except Exception as e:
            logger.warning(f"Clickbait thumbnail generation failed: {e}")
            if os.path.exists(temp_frame):
                return temp_frame
            return None
        finally:
            if os.path.exists(temp_frame):
                try:
                    os.remove(temp_frame)
                except Exception:
                    pass

    @staticmethod
    def _get_bold_font(font_size: int) -> ImageFont.FreeTypeFont:
        """Sistemdeki en kalın ve okunaklı fontu yükler."""
        font_candidates = [
            "C:\\Windows\\Fonts\\impact.ttf",
            "C:\\Windows\\Fonts\\arialbd.ttf",
            "C:\\Windows\\Fonts\\segoeprb.ttf",
            "C:\\Windows\\Fonts\\calibrib.ttf",
            "impact.ttf",
            "arialbd.ttf"
        ]
        for fpath in font_candidates:
            if os.path.exists(fpath):
                try:
                    return ImageFont.truetype(fpath, font_size)
                except Exception:
                    continue
        return ImageFont.load_default()


clip_ai_seo = ClipAISEOService()
