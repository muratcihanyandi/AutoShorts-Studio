import os
import threading
from datetime import datetime
import streamlit as st
from loguru import logger

from app.models.clip_schema import (
    PlatformType, StreamerTarget, ClipStatus, ClipPipelineSettings, ClipItem,
    ClipTargetFormat,
)
from app.services.clip_db import clip_db
from app.services.clip_scheduler import clip_scheduler
from app.services.shorts_publisher import shorts_publisher
from app.services.clip_publisher import clip_publisher
from app.services.clip_downloader import clip_downloader
from app.services.video_progress_tracker import video_progress_tracker

PLATFORM_LABELS = {
    PlatformType.TWITCH: "🟣 Twitch",
    PlatformType.KICK: "🟢 Kick",
    PlatformType.YOUTUBE: "🔴 YouTube",
}

FORMAT_LABELS = {
    ClipTargetFormat.SHORTS: "📱 Dikey Shorts",
    ClipTargetFormat.HORIZONTAL: "🖥️ Yatay Video",
}

STATUS_LABELS = {
    ClipStatus.DISCOVERED: "🕐 Sırada (Keşfedildi)",
    ClipStatus.DOWNLOADING: "⬇️ İndiriliyor",
    ClipStatus.DOWNLOADED: "📥 İndirildi (Ham)",
    ClipStatus.ANALYZING: "🔍 Analiz Ediliyor",
    ClipStatus.EDITING: "✂️ Kurgulanıyor",
    ClipStatus.READY: "✅ Hazır (Stokta)",
    ClipStatus.UPLOADING: "📤 YouTube'a Yükleniyor",
    ClipStatus.PUBLISHED: "🎉 Yayında",
    ClipStatus.SPLIT: "🧩 Parçalara Bölündü",
    ClipStatus.FAILED: "❌ Hata Aldı",
    ClipStatus.SKIPPED: "⏭️ Atlandı",
}

YOUTUBE_CATEGORIES = {
    "20": "🎮 Oyun (Gaming)",
    "22": "👤 İnsanlar ve Bloglar (People & Blogs)",
    "24": "🎭 Eğlence (Entertainment)",
    "23": "😂 Komedi (Comedy)",
    "17": "⚽ Spor (Sports)",
    "1": "🎬 Film ve Animasyon (Film & Animation)",
    "28": "🔬 Bilim ve Teknoloji (Science & Technology)",
}


def _start_background(fn, *args, **kwargs):
    """Uzun süren işlemleri UI'yi dondurmadan arka planda başlatır."""
    thread = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
    thread.start()
    return thread


def _process_clip_bg(clip_id: str, publish_now: bool = False):
    clip = clip_db.get_clip_by_id(clip_id)
    if not clip:
        return
    try:
        clip_scheduler.run_single_pipeline_for_clip(clip, publish_now=publish_now)
    except Exception as e:
        logger.error(f"Manual process failed for {clip_id}: {e}")


def _publish_clip_bg(clip_id: str):
    clip = clip_db.get_clip_by_id(clip_id)
    if not clip:
        return
    try:
        shorts_publisher.publish_clip(clip)
    except Exception as e:
        logger.error(f"Manual publish failed for {clip_id}: {e}")


# ================= CANLI İLERLEME & MOTOR LOGLARI =================

@st.fragment(run_every="2s")
def _render_live_pipeline_panel():
    """Video başına detaylı indirme/kurgu ilerlemesi ve canlı durum paneli."""
    task = video_progress_tracker.get_active_task()

    if task:
        # Aktif işlenen video kartı
        progress_val = min(1.0, max(0.0, float(task.get("progress_percent", 0.0)) / 100.0))
        pct_display = f"{task.get('progress_percent', 0.0):.1f}%"
        stage_label = task.get("stage_label", "İşleniyor")
        title = task.get("title", "Video")
        channel = task.get("channel_name", "")
        fmt = "📱 Shorts" if task.get("target_format") == "shorts" else "🖥️ Yatay"
        speed = task.get("speed_str") or "--"
        eta = task.get("eta_str") or "--"
        elapsed = task.get("elapsed_str") or "00:00"
        detail = task.get("detail_message", "")

        st.markdown(f"""
            <div style="background: linear-gradient(135deg, #161824 0%, #1e2238 100%); border: 1px solid #3d466e; border-left: 6px solid #00E676; border-radius: 10px; padding: 16px; margin-bottom: 12px; box-shadow: 0 4px 14px rgba(0,0,0,0.3);">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span style="display: inline-block; width: 10px; height: 10px; background-color: #00E676; border-radius: 50%; box-shadow: 0 0 8px #00E676;"></span>
                        <span style="font-weight: bold; color: #FFFFFF; font-size: 15px; letter-spacing: 0.5px;">CANLI İŞLENEN VİDEO</span>
                        <span style="background: #2a3152; color: #9fa8da; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: 600;">{fmt}</span>
                    </div>
                    <span style="font-weight: 800; font-size: 18px; color: #00E676; font-family: monospace;">{pct_display}</span>
                </div>
                <div style="font-size: 16px; font-weight: 600; color: #FFFFFF; margin-bottom: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                    {title}
                </div>
                <div style="font-size: 13px; color: #9fa8da; margin-bottom: 12px;">
                    Yayıncı: <strong style="color: #e0e0e0;">{channel}</strong> · Aşama: <strong style="color: #80d8ff;">{stage_label}</strong>
                </div>
            </div>
        """, unsafe_allow_html=True)

        st.progress(progress_val)

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("İşlem Aşaması", stage_label)
        with c2:
            st.metric("İndirme / İşleme Hızı", speed)
        with c3:
            st.metric("Tahmini Kalan (ETA)", eta)
        with c4:
            st.metric("Geçen Süre", elapsed)

        if detail:
            st.caption(f"ℹ️ **Detay:** {detail}")

    else:
        # Motor beklemede (Idle)
        settings = clip_db.get_settings()
        ready_h = clip_db.count_clips(status="ready", target_format="horizontal")
        ready_s = clip_db.count_clips(status="ready", target_format="shorts")
        target_h = settings.target_stock_horizontal
        target_s = settings.target_stock_shorts

        status_msg = (
            f"🟢 **Motor Çalışıyor (Boşta):** Stok hedefleri takip ediliyor. "
            f"(Yatay: {ready_h}/{target_h} hazır · Shorts: {ready_s}/{target_s} hazır)"
            if clip_scheduler.is_running else
            f"⏸️ **Motor Durduruldu:** Yeni videoları 1'er 1'er indirip stokları doldurmak için motoru başlatın."
        )

        st.markdown(f"""
            <div style="background: #14161f; border: 1px solid #282c3f; border-left: 5px solid #5c6bc0; border-radius: 8px; padding: 14px 18px; margin-bottom: 12px;">
                <div style="color: #c5cae9; font-size: 14px;">
                    {status_msg}
                </div>
            </div>
        """, unsafe_allow_html=True)

    # Canlı Motor Logları (Altında kompakt akış)
    with st.expander("📜 Canlı Motor ve Keşif Logları", expanded=False):
        logs = clip_scheduler.get_recent_logs(limit=25)
        log_box = ""
        for l in logs:
            color = "#00E676" if l["level"] == "info" else ("#FFB74D" if l["level"] == "warning" else "#FF5252")
            log_box += (
                f"<div style='font-family: monospace; font-size: 12px; margin-bottom: 3px; line-height: 1.4;'>"
                f"<span style='color: #7986cb;'>[{l['timestamp']}]</span> "
                f"<span style='color: {color}; font-weight: bold;'>[{l['level'].upper()}]</span> "
                f"<span style='color: #e0e0e0;'>{l['message']}</span></div>"
            )
        if not log_box:
            log_box = "<div style='color: #888; font-family: monospace; font-size: 12px;'>Henüz log kaydı yok.</div>"

        st.markdown(f"""
            <div style="background-color: #0d0e15; border: 1px solid #222538; border-radius: 6px; padding: 10px; max-height: 180px; overflow-y: auto;">
                {log_box}
            </div>
        """, unsafe_allow_html=True)


# ================= MEDYA HAVUZU & STOK KARTLARI =================

def _render_video_card(clip: ClipItem, settings: ClipPipelineSettings, is_stock: bool = True):
    """Her video için video oynatıcılı, yayın saatli ve YouTube ayarlı modern kart."""
    with st.container(border=True):
        col_main, col_side = st.columns([2.5, 1.5])

        with col_main:
            # Planlanan yayın zamanı rozeti (Varsa)
            sched_badge = ""
            if clip.scheduled_publish_time:
                sched_dt = clip.scheduled_publish_time
                now = datetime.now()
                if sched_dt.date() == now.date():
                    sched_text = f"Bugün {sched_dt.strftime('%H:%M')} (Prime Time)"
                elif sched_dt.date() == (now.date() + timedelta(days=1)):
                    sched_text = f"Yarın {sched_dt.strftime('%H:%M')} (Prime Time)"
                else:
                    sched_text = f"{sched_dt.strftime('%d.%m %H:%M')} (Prime Time)"
                sched_badge = f"<span style='background: linear-gradient(90deg, #7b1fa2, #e91e63); color: #fff; padding: 3px 9px; border-radius: 12px; font-size: 12px; font-weight: 600; margin-right: 8px;'>⏰ Otomasyon Yayını: {sched_text}</span>"

            # Format ve Durum rozetleri
            fmt_icon = "📱 Shorts" if clip.target_format == ClipTargetFormat.SHORTS else "🖥️ Yatay"
            status_txt = STATUS_LABELS.get(clip.status, clip.status.value)
            st.markdown(f"{sched_badge}<span style='background: #25283d; color: #b0bec5; padding: 2px 8px; border-radius: 4px; font-size: 12px;'>{fmt_icon}</span> <span style='background: #1e3a2b; color: #81c784; padding: 2px 8px; border-radius: 4px; font-size: 12px;'>{status_txt}</span>", unsafe_allow_html=True)

            yt_link = f" · [YouTube'da Aç](https://youtu.be/{clip.youtube_video_id})" if clip.youtube_video_id else ""
            st.markdown(f"### {clip.title[:80]}{yt_link}")

            meta_info = (
                f"👤 **{clip.channel_name}** ({clip.platform.value}) · "
                f"⏱️ {clip.duration:.0f}sn · "
                f"👁️ {clip.view_count:,} izlenme"
            )
            if clip.viral_score:
                meta_info += f" · 🔥 **Viral: {clip.viral_score}/10**"
            if clip.priority:
                meta_info += f" · ⬆️ Öncelik: {clip.priority}"

            st.markdown(meta_info)

            if clip.hook_text:
                st.info(f"🎣 **Kanca (Hook Başlık):** *{clip.hook_text}*")

            if clip.error_message:
                st.error(f"⚠️ {clip.error_message}")

        with col_side:
            # Aksiyon Butonları
            if clip.status == ClipStatus.READY:
                if st.button("🚀 YouTube'a Şimdi Yükle", key=f"pub_btn_{clip.clip_id}", use_container_width=True, type="primary"):
                    _start_background(_publish_clip_bg, clip.clip_id)
                    st.toast(f"YouTube'a yükleme başlatıldı: {clip.title[:30]}")
                    st.rerun()

            elif clip.status in (ClipStatus.DOWNLOADED, ClipStatus.DISCOVERED, ClipStatus.FAILED):
                if st.button("⚙️ Hemen Kurgula", key=f"proc_btn_{clip.clip_id}", use_container_width=True):
                    _start_background(_process_clip_bg, clip.clip_id)
                    st.toast(f"İşleme alındı: {clip.title[:30]}")
                    st.rerun()

            c_p1, c_p2, c_del = st.columns([1, 1, 1])
            with c_p1:
                if st.button("⬆️", key=f"p_up_{clip.clip_id}", help="Önceliği artır (Önce yayınlansın)", use_container_width=True):
                    clip_db.update_clip_metadata(clip.clip_id, priority=clip.priority + 10)
                    clip_publisher.update_scheduled_times_in_db(settings)
                    st.rerun()
            with c_p2:
                if st.button("⬇️", key=f"p_dn_{clip.clip_id}", help="Önceliği düşür", use_container_width=True):
                    clip_db.update_clip_metadata(clip.clip_id, priority=max(0, clip.priority - 10))
                    clip_publisher.update_scheduled_times_in_db(settings)
                    st.rerun()
            with c_del:
                if st.button("🗑️", key=f"del_btn_{clip.clip_id}", help="Stoktan ve diskten sil", use_container_width=True):
                    clip_db.delete_clip(clip.clip_id)
                    st.toast(f"Silindi: {clip.title[:25]}")
                    st.rerun()

        # Video Oynatıcı & Thumbnail Önizleme
        video_file = clip.processed_video_path or clip.raw_video_path
        has_video = video_file and os.path.exists(video_file)

        col_pv, col_edit = st.columns([1.8, 2.2])

        with col_pv:
            if has_video:
                with st.expander("🎬 Videoyu Siteden İzle", expanded=False):
                    st.video(video_file)
            elif clip.thumbnail_url:
                st.image(clip.thumbnail_url, use_container_width=True)

        with col_edit:
            with st.expander("✏️ YouTube Yayın Ayarlarını & Filtrelerini Düzenle", expanded=False):
                with st.form(f"edit_meta_{clip.clip_id}"):
                    new_title = st.text_input("YouTube Başlığı", value=clip.youtube_title or clip.title)
                    new_desc = st.text_area("Açıklama", value=clip.youtube_description or "", height=80)
                    tags_str = ", ".join(clip.youtube_tags or [])
                    new_tags_input = st.text_input("Etiketler (Virgülle ayırın)", value=tags_str)

                    cat_keys = list(YOUTUBE_CATEGORIES.keys())
                    current_cat = clip.youtube_category_id or settings.youtube_category_id or "20"
                    cat_idx = cat_keys.index(current_cat) if current_cat in cat_keys else 0
                    new_cat = st.selectbox(
                        "YouTube Kategorisi",
                        options=cat_keys,
                        format_func=lambda k: YOUTUBE_CATEGORIES.get(k, k),
                        index=cat_idx
                    )

                    c_k1, c_k2 = st.columns(2)
                    with c_k1:
                        new_kids = st.checkbox("Çocuklara Özel", value=clip.made_for_kids)
                    with c_k2:
                        new_age = st.checkbox("18+ Yaş Sınırı", value=clip.age_restricted)

                    thumb_input = st.text_input("Özel Kapak Görseli (Thumbnail Dosya Yolu)", value=clip.custom_thumbnail_path or "")

                    save_meta = st.form_submit_button("Kaydet ve Güncelle", use_container_width=True)
                    if save_meta:
                        parsed_tags = [t.strip().lstrip("#") for t in new_tags_input.split(",") if t.strip()]
                        clip_db.update_clip_metadata(
                            clip.clip_id,
                            youtube_title=new_title.strip(),
                            youtube_description=new_desc.strip(),
                            youtube_tags=parsed_tags,
                            youtube_category_id=new_cat,
                            made_for_kids=new_kids,
                            age_restricted=new_age,
                            custom_thumbnail_path=thumb_input.strip() or None
                        )
                        st.success("YouTube yayın filtreleri güncellendi!")
                        st.rerun()


# ================= ANA SEKMELER & SAYFA GÖRÜNÜMÜ =================

def render_clips_automation_tab():
    settings = clip_db.get_settings()
    # Stok yayın zamanlamasını canlı güncelle
    try:
        clip_publisher.update_scheduled_times_in_db(settings)
    except Exception:
        pass

    quota = clip_publisher.get_quota_summary(settings)

    # Başlık & Banner
    st.markdown("""
        <div style="background: linear-gradient(135deg, #181a29 0%, #252844 100%); padding: 20px 24px; border-radius: 12px; margin-bottom: 20px; border-left: 6px solid #7C4DFF; box-shadow: 0 4px 18px rgba(0,0,0,0.25);">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <h2 style="margin: 0; color: #FFFFFF; font-size: 25px; display: flex; align-items: center; gap: 10px;">
                        <span>⚡</span> AutoShorts Studio — Klip & Shorts Otomasyon Merkezi
                    </h2>
                    <p style="margin: 6px 0 0 0; color: #B0BEC5; font-size: 14px;">
                        YouTube ve Twitch yayıncılarını otonom tarar. Videoları <strong>sırayla (1'er 1'er)</strong> indirir ve kurgular.
                        <strong>3 Yatay ve 6 Shorts</strong> stok tutar; videoları Prime Time saatlerinde otomatik yayınlar.
                    </p>
                </div>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # 1. Metrik Barları
    c_m1, c_m2, c_m3, c_m4, c_m5 = st.columns(5)

    with c_m1:
        st_label = "🟢 ÇALIŞIYOR" if clip_scheduler.is_running else "⏸️ DURDURULDU"
        st.metric("Otonom Motor", st_label, help="1'er 1'er indirme, kurgu ve prime time kotasını yönetir")

    with c_m2:
        streamers = clip_db.list_streamers(active_only=True)
        st.metric("Aktif Kanallar", f"{len(streamers)} Kanal", help="Takip edilen YouTube / Twitch / Kick kaynakları")

    with c_m3:
        sq = quota[ClipTargetFormat.SHORTS.value]
        st.metric(
            "Shorts Durumu",
            f"{sq['ready_in_stock']} / {sq['target_stock']} Stok",
            delta=f"Bugün: {sq['published_today']}/{sq['daily_limit']} Yüklendi"
        )

    with c_m4:
        hq = quota[ClipTargetFormat.HORIZONTAL.value]
        st.metric(
            "Yatay Video",
            f"{hq['ready_in_stock']} / {hq['target_stock']} Stok",
            delta=f"Bugün: {hq['published_today']}/{hq['daily_limit']} Yüklendi"
        )

    with c_m5:
        is_yt = shorts_publisher.is_youtube_ready()
        yt_badge = "✅ BAĞLI" if is_yt else "⚠️ TOKEN GEREKLİ"
        st.metric("YouTube Durumu", yt_badge, help="YouTube OAuth kimlik doğrulaması")

    st.markdown("<hr style='margin: 15px 0; border: none; border-top: 1px solid #282c3f;'>", unsafe_allow_html=True)

    # 2. Canlı İlerleme ve Motor Kontrolü
    col_ctrl, col_prog = st.columns([1.1, 2.9])

    with col_ctrl:
        st.subheader("🎮 Motor Kontrolü")
        if not clip_scheduler.is_running:
            if st.button("🚀 Otonom Motoru Başlat", use_container_width=True, type="primary"):
                clip_scheduler.start()
                st.rerun()
        else:
            if st.button("🛑 Motoru Durdur", use_container_width=True):
                clip_scheduler.stop()
                st.rerun()

        is_prime, prime_slot = clip_publisher.is_prime_time_now(settings)
        prime_badge = f"<span style='color: #00E676; font-weight: bold;'>Evet ({prime_slot})</span>" if is_prime else "<span style='color: #9fa8da;'>Hayır</span>"

        st.markdown(f"""
            <div style="font-size: 13px; color: #b0bec5; line-height: 1.6; background: #161824; padding: 10px; border-radius: 8px; border: 1px solid #292d42;">
                <div>🔥 <strong>Prime Time Saatleri:</strong> {settings.prime_time_slots}</div>
                <div>⏰ <strong>Şu An Prime Time mı:</strong> {prime_badge}</div>
                <div>📦 <strong>Hedef Stok:</strong> 3 Yatay · 6 Shorts</div>
                <div>⚙️ <strong>İşleme Modu:</strong> Sıralı (1'er 1'er)</div>
                <div>📤 <strong>Oto-Yükleme:</strong> {'Açık' if settings.auto_upload else 'Kapalı (Manuel)'}</div>
            </div>
        """, unsafe_allow_html=True)

    with col_prog:
        st.subheader("⚡ Canlı İşlem Hattı & İlerleme")
        _render_live_pipeline_panel()

    st.markdown("<hr style='margin: 15px 0; border: none; border-top: 1px solid #282c3f;'>", unsafe_allow_html=True)

    # 3. Medya Havuzu, Stok Yönetimi ve Ayarlar Sekmeleri
    tab_ready_h, tab_ready_s, tab_downloaded, tab_published, tab_settings, tab_streamers = st.tabs([
        f"🖥️ Yatay Stok ({quota['horizontal']['ready_in_stock']}/{quota['horizontal']['target_stock']})",
        f"📱 Shorts Stok ({quota['shorts']['ready_in_stock']}/{quota['shorts']['target_stock']})",
        f"📥 İndirilen Ham Dosyalar",
        f"🎉 Yayınlananlar",
        f"⚙️ Kurgu, Prime Time & YouTube Filtreleri",
        f"📺 Yayıncı Listesi"
    ])

    # A. YATAY STOK SEKMESİ
    with tab_ready_h:
        st.markdown(f"#### 🖥️ Yatay Video Stok Havuzu (Hedef: {settings.target_stock_horizontal} Video)")
        st.caption("Bu videolardan her gün Prime Time saatlerinde 3 tanesi YouTube'a yüklenir. Kalan 3 video yarının yedek stoğu olarak hazır tutulur.")

        ready_horizontal = clip_db.list_ready_clips(target_format=ClipTargetFormat.HORIZONTAL.value, limit=30)
        if ready_horizontal:
            for c in ready_horizontal:
                _render_video_card(c, settings, is_stock=True)
        else:
            st.info("Yatay stokta hazır video bulunmuyor. Motor çalıştığında yayın geçmişlerinden 1'er 1'er indirilip hazırlanacaktır.")

    # B. SHORTS STOK SEKMESİ
    with tab_ready_s:
        st.markdown(f"#### 📱 Dikey Shorts Stok Havuzu (Hedef: {settings.target_stock_shorts} Video)")
        st.caption("Kliplerden OpenShorts algoritması ile altyazılı üretilmiş 9:16 Shorts videoları. Günde 3 tane Prime Time saatlerinde yayınlanır, 6 tane stok hazır tutulur.")

        ready_shorts = clip_db.list_ready_clips(target_format=ClipTargetFormat.SHORTS.value, limit=30)
        if ready_shorts:
            for c in ready_shorts:
                _render_video_card(c, settings, is_stock=True)
        else:
            st.info("Shorts stoğunda hazır video bulunmuyor. Motor çalıştığında klipler taranıp 1'er 1'er indirilip kurgulanacaktır.")

    # C. İNDİRİLEN HAM DOSYALAR (RAW ARCHIVE)
    with tab_downloaded:
        st.markdown("#### 📥 İndirilen Ham Dosyalar (Kurgu Bekleyenler)")
        st.caption("Diske indirilmiş fakat henüz kurgulanmamış videolar. İsterseniz buradan doğrudan 'Hemen Kurgula' butonuna basabilirsiniz.")

        downloaded_clips = clip_db.list_downloaded_clips(limit=25)
        if downloaded_clips:
            for c in downloaded_clips:
                _render_video_card(c, settings, is_stock=False)
        else:
            st.info("Şu an diskte kurgu bekleyen ham video bulunmuyor.")

    # D. YAYINLANANLAR
    with tab_published:
        st.markdown("#### 🎉 YouTube'da Başarıyla Yayınlanan Videolar")
        published_clips = clip_db.list_clips_by_status(status=ClipStatus.PUBLISHED, limit=30)
        if published_clips:
            for c in published_clips:
                with st.container(border=True):
                    col_p1, col_p2 = st.columns([3, 1])
                    with col_p1:
                        pub_time = c.published_at.strftime("%d.%m.%Y %H:%M") if c.published_at else "Bugün"
                        fmt_icon = "📱 Shorts" if c.target_format == ClipTargetFormat.SHORTS else "🖥️ Yatay"
                        st.markdown(f"**{c.title}**  \n`{fmt_icon}` · Yayınlanma: **{pub_time}** · Yayıncı: {c.channel_name}")
                    with col_p2:
                        if c.youtube_video_id:
                            st.link_button("▶️ YouTube'da İzle", f"https://youtu.be/{c.youtube_video_id}", use_container_width=True)
        else:
            st.info("Henüz YouTube'da yayınlanan video kaydı yok.")

    # E. GELİŞMİŞ AYARLAR & YOUTUBE FİLTRELERİ
    with tab_settings:
        st.markdown("#### ⚙️ Kurgu, Kota, Prime Time & YouTube Filtreleri")

        with st.form("advanced_pipeline_settings_form"):
            st.markdown("##### 📅 Prime Time & Stok Kontenjanları")
            c_s1, c_s2 = st.columns(2)
            with c_s1:
                target_stock_h = st.number_input("Yatay Stok Hedefi (Hazır Video)", min_value=1, max_value=20, value=settings.target_stock_horizontal)
                daily_limit_h = st.number_input("Günlük Yatay Yayın Kotası", min_value=1, max_value=10, value=settings.daily_horizontal_limit)
            with c_s2:
                target_stock_s = st.number_input("Shorts Stok Hedefi (Hazır Video)", min_value=1, max_value=30, value=settings.target_stock_shorts)
                daily_limit_s = st.number_input("Günlük Shorts Yayın Kotası", min_value=1, max_value=10, value=settings.daily_shorts_limit)

            prime_slots_input = st.text_input(
                "🔥 Prime Time Yayın Slotları (Saat:Dakika, virgülle ayırın)",
                value=settings.prime_time_slots,
                help="Günün en çok izlenme alan saatleri. Örn: 18:00, 20:00, 22:00. Sıradaki en viral videolar bu saatlerde yayınlanır."
            )

            st.markdown("##### 📤 YouTube Yayın & Kategori Filtreleri")
            c_y1, c_y2 = st.columns(2)
            with c_y1:
                auto_upload_toggle = st.checkbox("YouTube Otomatik Yayınlama Açık", value=settings.auto_upload)
                privacy_opt = st.selectbox(
                    "Varsayılan Gizlilik",
                    options=["private", "unlisted", "public"],
                    format_func=lambda x: {"private": "Gizli (Taslak)", "unlisted": "Liste Dışı", "public": "Herkese Açık (Canlı)"}.get(x, x),
                    index=["private", "unlisted", "public"].index(settings.privacy_status)
                )
                auto_thumb_toggle = st.checkbox("Peak Hype Karelerinden Otomatik Kapak (Thumbnail) Üret", value=settings.auto_generate_thumbnail)

            with c_y2:
                cat_keys = list(YOUTUBE_CATEGORIES.keys())
                cur_cat = settings.youtube_category_id or "20"
                c_idx = cat_keys.index(cur_cat) if cur_cat in cat_keys else 0
                cat_select = st.selectbox(
                    "Varsayılan YouTube Video Kategorisi",
                    options=cat_keys,
                    format_func=lambda k: YOUTUBE_CATEGORIES.get(k, k),
                    index=c_idx
                )
                made_kids_toggle = st.checkbox("Çocuklara Özel İçerik (Made for Kids)", value=settings.made_for_kids)
                age_restrict_toggle = st.checkbox("18+ Yaş Sınırı Uygula", value=settings.age_restricted)

            st.markdown("##### 🎬 Kurgu & Intro/Outro Kesim")
            c_k1, c_k2 = st.columns(2)
            with c_k1:
                intro_trim_val = st.number_input("Intro / Jenerik Kesme (sn)", min_value=0.0, max_value=60.0, value=float(settings.intro_trim_seconds), step=1.0)
                sub_toggle = st.checkbox("Shorts için faster-whisper altyazı ekle", value=settings.subtitle_enabled)
                llm_moment_toggle = st.checkbox("LLM ile Akıllı Viral An ve Kanca Tespiti", value=settings.llm_moment_detection)
            with c_k2:
                outro_trim_val = st.number_input("Outro / Bitiş Kartı Kesme (sn)", min_value=0.0, max_value=60.0, value=float(settings.outro_trim_seconds), step=1.0)
                hook_toggle = st.checkbox("OpenShorts Kanca Başlık Çıkartması Ekle", value=settings.hook_overlay_enabled)
                ai_title_toggle = st.checkbox("LLM ile YouTube Başlığı ve Etiketleri Üret", value=settings.ai_title_generation)

            st.markdown("##### 🔍 Güncellik & Negatif Filtreler")
            c_f1, c_f2 = st.columns(2)
            with c_f1:
                max_age_val = st.number_input("Maksimum Video Yaşı (Gün)", min_value=1, max_value=365, value=int(settings.max_video_age_days))
                blacklist_str = st.text_input("Başlık Kara Listesi (Negatif Kelimeler)", value=settings.title_blacklist)
            with c_f2:
                viral_baraj_val = st.number_input("Eski Videolar İçin Viralite Barajı (İzlenme)", min_value=1000, max_value=5000000, value=int(settings.viral_threshold_for_older), step=5000)
                yt_search_toggle = st.checkbox("YouTube kesit araması yap", value=settings.youtube_search_enabled)

            save_btn = st.form_submit_button("💾 Tüm Ayarları Kaydet", use_container_width=True, type="primary")
            if save_btn:
                settings.target_stock_horizontal = int(target_stock_h)
                settings.target_stock_shorts = int(target_stock_s)
                settings.daily_horizontal_limit = int(daily_limit_h)
                settings.daily_shorts_limit = int(daily_limit_s)
                settings.prime_time_slots = prime_slots_input.strip()
                settings.auto_upload = auto_upload_toggle
                settings.privacy_status = privacy_opt
                settings.auto_generate_thumbnail = auto_thumb_toggle
                settings.youtube_category_id = cat_select
                settings.made_for_kids = made_kids_toggle
                settings.age_restricted = age_restrict_toggle
                settings.intro_trim_seconds = float(intro_trim_val)
                settings.outro_trim_seconds = float(outro_trim_val)
                settings.subtitle_enabled = sub_toggle
                settings.hook_overlay_enabled = hook_toggle
                settings.llm_moment_detection = llm_moment_toggle
                settings.ai_title_generation = ai_title_toggle
                settings.max_video_age_days = int(max_age_val)
                settings.viral_threshold_for_older = int(viral_baraj_val)
                settings.title_blacklist = blacklist_str.strip()
                settings.youtube_search_enabled = yt_search_toggle

                clip_db.save_settings(settings)
                clip_publisher.update_scheduled_times_in_db(settings)
                st.success("Tüm ayarlar başarıyla güncellendi!")
                st.rerun()

    # F. YAYINCI LİSTESİ
    with tab_streamers:
        st.markdown("#### 📺 Takip Edilen Yayıncılar ve Kesit Kanalları")

        with st.expander("➕ Yeni Yayıncı / Kesit Kanalı Ekle", expanded=True):
            with st.form("add_streamer_form", clear_on_submit=True):
                s_url = st.text_input(
                    "Kanal / Yayıncı Adresi (URL)",
                    placeholder="https://youtube.com/@rraenekesitleri · https://youtube.com/@elraenn · https://twitch.tv/kanal"
                )
                s_queries = st.text_input(
                    "Fan / Kesit Arama Terimleri (Virgülle ayırın, Opsiyonel)",
                    placeholder="Örn: rraenee kesitleri, rraenee klipleri"
                )
                c_sf1, c_sf2 = st.columns(2)
                with c_sf1:
                    w_shorts = st.checkbox("📱 Dikey Shorts Üret", value=True)
                with c_sf2:
                    w_horiz = st.checkbox("🖥️ Yatay Video Üret", value=True)

                submit_s = st.form_submit_button("Yayıncıyı Kaydet", use_container_width=True)
                if submit_s:
                    if not s_url.strip():
                        st.error("Lütfen bir URL girin.")
                    else:
                        try:
                            plat, cname = clip_downloader.parse_streamer_url(s_url)
                            fmts = []
                            if w_shorts:
                                fmts.append("shorts")
                            if w_horiz:
                                fmts.append("horizontal")
                            f_list = [q.strip() for q in s_queries.split(",") if q.strip()]
                            target = StreamerTarget(
                                platform=plat,
                                channel_name=cname,
                                display_name=cname,
                                formats=fmts,
                                fan_channel_queries=f_list
                            )
                            clip_db.add_streamer(target)
                            st.success(f"{PLATFORM_LABELS.get(plat, plat.value)} `{cname}` eklendi!")
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))

        all_streamers = clip_db.list_streamers()
        if all_streamers:
            for s in all_streamers:
                with st.container(border=True):
                    c_info, c_act = st.columns([3, 1])
                    with c_info:
                        f_badges = " ".join(FORMAT_LABELS.get(f, f.value) for f in s.formats)
                        st.markdown(f"**{PLATFORM_LABELS.get(s.platform, s.platform.value)}** `{s.channel_name}`  \n{f_badges}")
                        if s.fan_channel_queries:
                            st.caption(f"Kesit aramaları: `{', '.join(s.fan_channel_queries)}`")
                    with c_act:
                        if st.button("🗑️ Sil", key=f"del_streamer_{s.id}", use_container_width=True):
                            clip_db.delete_streamer(s.id)
                            st.rerun()
        else:
            st.info("Kayıtlı yayıncı bulunmuyor.")
