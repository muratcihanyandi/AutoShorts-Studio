import os
import threading
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

PLATFORM_LABELS = {
    PlatformType.TWITCH: "🟣 Twitch",
    PlatformType.KICK: "🟢 Kick",
    PlatformType.YOUTUBE: "🔴 YouTube",
}

FORMAT_LABELS = {
    ClipTargetFormat.SHORTS: "📱 Dikey Shorts",
    ClipTargetFormat.HORIZONTAL: "🖥️ Yatay Video",
}


def _start_background(fn, *args, **kwargs):
    """Uzun suren islemleri (indirme/kurgu/yukleme) UI'yi dondurmadan baslatir."""
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


@st.fragment(run_every="5s")
def _render_log_panel():
    """Canli sistem loglari (otomatik yenilenir)."""
    st.subheader("📜 Canlı Sistem ve Motor Logları")
    logs = clip_scheduler.get_recent_logs(limit=18)
    log_box = ""
    for l in logs:
        color = "#4CAF50" if l["level"] == "info" else ("#FF9800" if l["level"] == "warning" else "#F44336")
        log_box += (
            f"<div style='font-family: monospace; font-size: 13px; margin-bottom: 4px;'>"
            f"<span style='color: #888;'>[{l['timestamp']}]</span> "
            f"<span style='color: {color}; font-weight: bold;'>[{l['level'].upper()}]</span> "
            f"{l['message']}</div>"
        )
    if not log_box:
        log_box = ("<div style='color: #888; font-family: monospace; font-size: 13px;'>"
                   "Henüz log kaydı yok. Motor başlatıldığında burada listelenecek.</div>")

    st.markdown(f"""
        <div style="background-color: #121218; border: 1px solid #333344; border-radius: 8px; padding: 12px; height: 200px; overflow-y: auto;">
            {log_box}
        </div>
    """, unsafe_allow_html=True)


def _render_stock_tab(target_format: ClipTargetFormat, settings: ClipPipelineSettings):
    """Stok sekmesi: bekleyen/hazır videolar ve eylem butonlari."""
    fmt_value = target_format.value
    clips = clip_db.list_clips(limit=25, target_format=fmt_value)
    quota = clip_publisher.get_quota_summary(settings)[fmt_value]

    st.markdown(
        f"**Bugün yüklenen:** {quota['published_today']} / {quota['daily_limit']} "
        f"| **Stok:** {quota['ready_in_stock']} hazır, {quota['discovered_in_stock']} bekliyor"
    )

    if not clips:
        st.info("Bu formatta henüz video yok. Yayıncı ekleyip motoru başlatın.")
        return

    status_labels = {
        ClipStatus.DISCOVERED: "🕐 Bekliyor",
        ClipStatus.DOWNLOADING: "⬇️ İndiriliyor",
        ClipStatus.DOWNLOADED: "📥 İndirildi",
        ClipStatus.ANALYZING: "🔍 Analiz",
        ClipStatus.EDITING: "✂️ Kurgulanıyor",
        ClipStatus.READY: "✅ Hazır",
        ClipStatus.UPLOADING: "📤 Yükleniyor",
        ClipStatus.PUBLISHED: "🎉 Yayında",
        ClipStatus.SPLIT: "🧩 Parçalandı",
        ClipStatus.FAILED: "❌ Hata",
        ClipStatus.SKIPPED: "⏭️ Atladı",
    }

    for clip in clips:
        with st.container(border=True):
            col_info, col_actions = st.columns([2.4, 1.6])

            with col_info:
                yt_link = f" · [İzle](https://youtu.be/{clip.youtube_video_id})" if clip.youtube_video_id else ""
                hook_badge = f"  \n🎣 **Kanca:** *{clip.hook_text}*" if clip.hook_text else ""
                viral_badge = f" · viral: {clip.viral_score}/10" if clip.viral_score else ""
                st.markdown(
                    f"**{clip.title[:70]}**{yt_link}  \n"
                    f"`{status_labels.get(clip.status, clip.status.value)}` · "
                    f"{clip.channel_name} · {clip.duration:.0f}sn · "
                    f"izlenme: {clip.view_count} · hype: {clip.hype_score}{viral_badge} · öncelik: {clip.priority}"
                    f"{hook_badge}"
                )
                if clip.error_message:
                    st.caption(f"⚠️ {clip.error_message[:140]}")

            with col_actions:
                btn_a, btn_b, btn_c = st.columns(3)
                with btn_a:
                    if clip.status in (ClipStatus.DISCOVERED, ClipStatus.FAILED, ClipStatus.SKIPPED):
                        if st.button("⚙️ İşle", key=f"proc_{clip.clip_id}", use_container_width=True):
                            _start_background(_process_clip_bg, clip.clip_id)
                            st.toast(f"İşleme alındı: {clip.title[:30]}")
                with btn_b:
                    if clip.status == ClipStatus.READY:
                        if st.button("📤 Yükle", key=f"pub_{clip.clip_id}", use_container_width=True):
                            _start_background(_publish_clip_bg, clip.clip_id)
                            st.toast(f"Yükleniyor: {clip.title[:30]}")
                with btn_c:
                    if st.button("🗑️", key=f"del_{clip.clip_id}", use_container_width=True, help="Stoktan sil"):
                        clip_db.delete_clip(clip.clip_id)
                        st.rerun()

                prio_a, prio_b = st.columns(2)
                with prio_a:
                    if st.button("⬆️ Öncelik", key=f"prio_up_{clip.clip_id}", use_container_width=True):
                        clip_db.update_clip_status(clip.clip_id, clip.status, priority=clip.priority + 10)
                        st.rerun()
                with prio_b:
                    if st.button("⬇️ Öncelik", key=f"prio_down_{clip.clip_id}", use_container_width=True):
                        clip_db.update_clip_status(clip.clip_id, clip.status, priority=clip.priority - 10)
                        st.rerun()

            video_path = clip.processed_video_path or clip.raw_video_path
            if video_path and os.path.exists(video_path):
                with st.expander("🎬 Önizleme", expanded=False):
                    st.video(video_path)


def render_clips_automation_tab():
    st.markdown("""
        <div style="background: linear-gradient(135deg, #1e1e2f 0%, #2a2b40 100%); padding: 18px 24px; border-radius: 12px; margin-bottom: 20px; border-left: 5px solid #9146FF;">
            <h2 style="margin: 0; color: #FFFFFF; font-size: 24px; display: flex; align-items: center; gap: 10px;">
                <span>⚡</span> Yayıncı Klip & Shorts Otomasyon Merkezi
            </h2>
            <p style="margin: 6px 0 0 0; color: #BBBBCC; font-size: 14px;">
                Twitch / Kick / YouTube kaynaklarını otomatik tarar: kliplerden altyazılı dikey Shorts,
                yayın geçmişi ve paylaşılmış videolardan intro/outro kırpılmış yatay videolar üretir.
                Stoktaki videoları günlük kotaya göre YouTube'a yükler.
            </p>
        </div>
    """, unsafe_allow_html=True)

    settings = clip_db.get_settings()
    quota = clip_publisher.get_quota_summary(settings)

    # 1. Bilgi & Durum Barlari
    col_stat1, col_stat2, col_stat3, col_stat4, col_stat5 = st.columns(5)

    with col_stat1:
        status_label = "🟢 ÇALIŞIYOR" if clip_scheduler.is_running else "⏸️ DURDURULDU"
        st.metric("Otonom Motor", status_label, help="Keşif + stok işleme + kotalı yayınlama döngüsü")

    with col_stat2:
        streamers = clip_db.list_streamers()
        st.metric("Takip Edilen", f"{len(streamers)} Kanal", help="Twitch / Kick / YouTube takip hedefleri")

    with col_stat3:
        shorts_q = quota[ClipTargetFormat.SHORTS.value]
        st.metric("Bugün Shorts", f"{shorts_q['published_today']}/{shorts_q['daily_limit']}",
                  help=f"Stokta {shorts_q['ready_in_stock']} hazır video bekliyor")

    with col_stat4:
        horiz_q = quota[ClipTargetFormat.HORIZONTAL.value]
        st.metric("Bugün Yatay", f"{horiz_q['published_today']}/{horiz_q['daily_limit']}",
                  help=f"Stokta {horiz_q['ready_in_stock']} hazır video bekliyor")

    with col_stat5:
        yt_label = "✅ BAĞLI" if shorts_publisher.is_youtube_ready() else "⚠️ TOKEN GEREKLİ"
        st.metric("YouTube", yt_label, help="YouTube Data API bağlantı ve token durumu")

    st.markdown("---")

    # 2. Motor Kontrolu
    col_ctrl, col_mode_info = st.columns([1.5, 2.5])

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
        st.caption(f"**Mevcut Durum:** {clip_scheduler.current_status}")
        window_note = "açık" if clip_publisher.is_within_publish_window(settings) else "kapalı"
        st.caption(
            f"Yayın penceresi: **{settings.publish_window_start_hour}:00 - {settings.publish_window_end_hour}:00** (şu an {window_note}) · "
            f"Oto-yükleme: **{'açık' if settings.auto_upload else 'kapalı'}**"
        )

    with col_mode_info:
        st.subheader("🌐 Çift Motor & AI Agent Portu")
        st.markdown("""
        - **1. Otonom Kural Modu:** Motoru başlatın; yayıncılar periyodik taranır, yeni içerikler stoğa eklenir,
          kurgulanır ve günlük kotaya göre YouTube'a yüklenir.
        - **2. Localhost AI Agent Portu:** Harici yapay zeka ajanları `http://127.0.0.1:8080/docs` üzerinden
          tüm stok/kota/keşif komutlarını kullanabilir.
        """)

    st.markdown("---")

    col_left, col_right = st.columns([1.1, 1.9])

    with col_left:
        # A. Yayinci Ekleme (URL ile)
        with st.expander("📺 Yayıncı Listesi & Yeni Ekle", expanded=True):
            with st.form("add_streamer_form", clear_on_submit=True):
                streamer_url = st.text_input(
                    "Yayıncı / Kesit Kanalı Adresi (URL)",
                    placeholder="https://youtube.com/@rraenekesitleri · https://youtube.com/@elraenn · https://twitch.tv/kanal",
                    help="Kanal adresini yapıştırın. Kaliteli ve güncel kesitler için doğrudan YouTube kesit kanalları önerilir."
                )
                fan_queries_input = st.text_input(
                    "YouTube Fan / Kesit Kanalı Aramaları (Virgülle ayırın, Opsiyonel)",
                    placeholder="Örn: rraenee kesitleri, rraenee klipleri",
                    help="Belirtilen yayıncının YouTube hayran/kesit kanallarındaki viral anları taramak için arama terimleri."
                )
                col_f1, col_f2 = st.columns(2)
                with col_f1:
                    want_shorts = st.checkbox("📱 Dikey Shorts", value=True, help="Kliplerden altyazılı 9:16 üret")
                with col_f2:
                    want_horizontal = st.checkbox("🖥️ Yatay Video", value=True, help="VOD/YouTube videolarından 16:9 üret")

                col_v, col_m = st.columns(2)
                with col_v:
                    min_views = st.number_input("Min İzlenme Eşiği", min_value=0, value=100, step=50)
                with col_m:
                    interval_mins = st.number_input("Tarama Aralığı (dk)", min_value=15, value=60, step=15)

                submitted = st.form_submit_button("➕ Yayıncıyı Ekle", use_container_width=True)
                if submitted:
                    formats = []
                    if want_shorts:
                        formats.append("shorts")
                    if want_horizontal:
                        formats.append("horizontal")
                    if not streamer_url.strip():
                        st.error("Lütfen yayıncı adresini girin.")
                    elif not formats:
                        st.error("En az bir çıktı formatı seçin (Shorts / Yatay).")
                    else:
                        try:
                            platform, channel_name = clip_downloader.parse_streamer_url(streamer_url)
                            fan_queries = [q.strip() for q in fan_queries_input.split(",") if q.strip()] if fan_queries_input else []
                            target = StreamerTarget(
                                platform=platform,
                                channel_name=channel_name,
                                display_name=channel_name,
                                formats=formats,
                                min_views=min_views,
                                check_interval_minutes=interval_mins,
                                fan_channel_queries=fan_queries
                            )
                            clip_db.add_streamer(target)
                            st.success(f"{PLATFORM_LABELS.get(platform, platform.value)} `{channel_name}` eklendi!")
                            st.rerun()
                        except ValueError as e:
                            st.error(str(e))

            if streamers:
                st.write("**Aktif Takip Edilen Yayıncılar:**")
                for s in streamers:
                    col_info, col_del = st.columns([3, 1])
                    with col_info:
                        fmt_badges = " ".join(FORMAT_LABELS.get(f, f.value) for f in s.formats)
                        fan_info = f" · Kesitler: `{', '.join(s.fan_channel_queries)}`" if s.fan_channel_queries else ""
                        st.markdown(
                            f"**{PLATFORM_LABELS.get(s.platform, s.platform.value)}** `{s.channel_name}`  \n"
                            f"{fmt_badges} · min {s.min_views} izlenme · tarama {s.check_interval_minutes} dk"
                            f"{fan_info}"
                        )
                    with col_del:
                        if st.button("🗑️ Sil", key=f"del_streamer_{s.id}"):
                            clip_db.delete_streamer(s.id)
                            st.rerun()
            else:
                st.info("Henüz eklenmiş bir yayıncı yok. Yukarıdan adres yapıştırın.")

        # B. Kurgu, Kota & YouTube Ayarlari
        with st.expander("⚙️ Kurgu, Kota & YouTube Ayarları", expanded=False):
            with st.form("pipeline_settings_form"):
                st.markdown("**📅 Günlük Yükleme Kontenjanı**")
                col_q1, col_q2 = st.columns(2)
                with col_q1:
                    daily_shorts = st.number_input("Günlük Shorts limiti", min_value=0, max_value=10,
                                                   value=settings.daily_shorts_limit, step=1)
                with col_q2:
                    daily_horizontal = st.number_input("Günlük Yatay limiti", min_value=0, max_value=10,
                                                       value=settings.daily_horizontal_limit, step=1)

                col_w1, col_w2, col_w3 = st.columns(3)
                with col_w1:
                    publish_interval = st.number_input("Yükleme kontrol aralığı (dk)", min_value=30, max_value=720,
                                                       value=settings.publish_interval_minutes, step=30)
                with col_w2:
                    window_start = st.number_input("Yayın penceresi başlangıç (saat)", min_value=0, max_value=23,
                                                   value=settings.publish_window_start_hour, step=1)
                with col_w3:
                    window_end = st.number_input("Yayın penceresi bitiş (saat)", min_value=0, max_value=24,
                                                 value=settings.publish_window_end_hour, step=1)

                st.markdown("**🎬 Kurgu Ayarları**")
                layout_opts = ["streamer_facecam", "blur_bg", "center_crop", "split"]
                video_layout = st.selectbox(
                    "Video Yerleşimi (9:16 Shorts)",
                    options=layout_opts,
                    format_func=lambda x: {
                        "streamer_facecam": "OpenShorts Akıllı Yayıncı Düzeni (Facecam + Oyun)",
                        "blur_bg": "Bulanık Arka Plan",
                        "center_crop": "Merkez Odaklı Kırpma",
                        "split": "Split Screen (Üst/Alt)"
                    }.get(x, x),
                    index=layout_opts.index(settings.video_layout) if settings.video_layout in layout_opts else 0
                )

                col_t1, col_t2 = st.columns(2)
                with col_t1:
                    intro_trim = st.number_input(
                        "Intro Kırpma (sn)", min_value=0.0, max_value=60.0,
                        value=float(settings.intro_trim_seconds), step=1.0,
                        help="Videonun başındaki kanal intro/jenerik ve abone ol ekranını keser"
                    )
                with col_t2:
                    outro_trim = st.number_input(
                        "Outro Kırpma (sn)", min_value=0.0, max_value=60.0,
                        value=float(settings.outro_trim_seconds), step=1.0,
                        help="Videonun sonundaki bitiş kartı ve outro ekranını keser"
                    )

                sub_enabled = st.checkbox(
                    "Shorts için dinamik altyazı ekle (faster-whisper)",
                    value=settings.subtitle_enabled,
                    help="Yatay (16:9) videolara kullanıcı tercihi gereği altyazı hiç eklenmez."
                )
                ai_titles = st.checkbox("LLM ile viral başlık/açıklama üret", value=settings.ai_title_generation)
                hook_overlay = st.checkbox(
                    "Viral Kanca (Hook) Başlık Çıkartması Ekle (OpenShorts)",
                    value=settings.hook_overlay_enabled,
                    help="OpenShorts stili yüksek kontrastlı kanca çıkartmasını videonun ilk 7 saniyesinde gösterir."
                )
                llm_moments = st.checkbox(
                    "OpenShorts LLM ile Akıllı Viral An & Kanca Tespiti",
                    value=settings.llm_moment_detection,
                    help="Whisper transkriptini LLM ile tarayarak en viral 20-55 saniyelik anı, vurucu kanca başlığını ve YouTube başlığını belirler."
                )

                st.markdown("**📅 Güncellik, Viralite & Başlık Filtresi**")
                col_age1, col_age2 = st.columns(2)
                with col_age1:
                    max_age = st.number_input(
                        "Maksimum Video Yaşı (Gün)", min_value=1, max_value=365,
                        value=int(settings.max_video_age_days), step=1,
                        help="Örn: 7 gün. Bu süreden eski videolar elenir (günceli takip eder)."
                    )
                with col_age2:
                    viral_baraj = st.number_input(
                        "Eski Videolar İçin Viral Barajı (İzlenme)", min_value=1000, max_value=10000000,
                        value=int(settings.viral_threshold_for_older), step=5000,
                        help="Eski videolar sadece bu izlenme sayısını aşmışsa viral kabul edilip alınır."
                    )

                blacklist_input = st.text_input(
                    "Başlık Kara Listesi (Negatif Kelimeler)",
                    value=settings.title_blacklist,
                    help="Virgülle ayırın. Başlığında bu kelimeler geçen (ör: açıklama, sorun, duyuru) videolar indirilmez."
                )

                st.markdown("**🌐 Kaynak Platform Seçimi & Tarama**")
                col_p1, col_p2 = st.columns(2)
                with col_p1:
                    enable_kick = st.checkbox(
                        "Kick Kliplerini Tara",
                        value=settings.enable_kick_discovery,
                        help="Kick'teki alakasız duyuru kliplerini engellemek için varsayılan kapalıdır."
                    )
                with col_p2:
                    enable_twitch = st.checkbox("Twitch Kliplerini Tara", value=settings.enable_twitch_discovery)

                yt_search = st.checkbox("YouTube'da yayıncı araması yap (kesit ve derlemeler)", value=settings.youtube_search_enabled)
                yt_suffix = st.text_input(
                    "YouTube Arama Eki",
                    value=settings.youtube_query_suffix,
                    help="Yayıncı adının yanına eklenecek anahtar kelimeler (örn: kesitleri klipleri)"
                )
                keep_raw = st.checkbox("Ham indirilen dosyaları sakla (disk kullanımı artar)", value=settings.keep_raw_files)

                col_s1, col_s2 = st.columns(2)
                with col_s1:
                    stock_cap = st.number_input("Stok üst sınırı (format başına hazır video)",
                                                min_value=1, max_value=100,
                                                value=settings.stock_max_ready_per_format, step=1)
                with col_s2:
                    vod_max = st.number_input("Maksimum VOD süresi (dk)", min_value=15, max_value=600,
                                              value=settings.vod_max_minutes, step=15)

                st.markdown("**📤 YouTube**")
                auto_yt = st.checkbox("Otomatik yükleme (günlük kota ile stoktan yayınla)", value=settings.auto_upload)
                privacy = st.selectbox(
                    "YouTube Varsayılan Gizlilik",
                    options=["private", "unlisted", "public"],
                    format_func=lambda x: {"private": "Gizli (Taslak)", "unlisted": "Liste Dışı", "public": "Herkese Açık"}.get(x, x),
                    index=["private", "unlisted", "public"].index(settings.privacy_status)
                )

                check_interval = st.slider("Genel Keşif Periyodu (Dakika)", min_value=15, max_value=240,
                                           value=settings.check_interval_minutes, step=15)

                save_settings_btn = st.form_submit_button("Kaydet", use_container_width=True)
                if save_settings_btn:
                    settings.video_layout = video_layout
                    settings.hook_overlay_enabled = hook_overlay
                    settings.llm_moment_detection = llm_moments
                    settings.intro_trim_seconds = float(intro_trim)
                    settings.outro_trim_seconds = float(outro_trim)
                    settings.subtitle_enabled = sub_enabled
                    settings.ai_title_generation = ai_titles
                    settings.max_video_age_days = int(max_age)
                    settings.viral_threshold_for_older = int(viral_baraj)
                    settings.title_blacklist = blacklist_input.strip()
                    settings.enable_kick_discovery = enable_kick
                    settings.enable_twitch_discovery = enable_twitch
                    settings.auto_upload = auto_yt
                    settings.privacy_status = privacy
                    settings.check_interval_minutes = check_interval
                    settings.daily_shorts_limit = int(daily_shorts)
                    settings.daily_horizontal_limit = int(daily_horizontal)
                    settings.publish_interval_minutes = int(publish_interval)
                    settings.publish_window_start_hour = int(window_start)
                    settings.publish_window_end_hour = int(window_end)
                    settings.youtube_search_enabled = yt_search
                    settings.youtube_query_suffix = yt_suffix.strip()
                    settings.keep_raw_files = keep_raw
                    settings.stock_max_ready_per_format = int(stock_cap)
                    settings.vod_max_minutes = int(vod_max)
                    clip_db.save_settings(settings)
                    st.success("Ayarlar başarıyla kaydedildi!")
                    st.rerun()

        # C. Manuel Klip Isleme
        with st.expander("🎯 Manuel URL ile Anlık Üret", expanded=False):
            with st.form("manual_clip_form"):
                clip_url = st.text_input("Video / Klip URL'si",
                                         placeholder="https://clips.twitch.tv/... veya https://kick.com/... veya YouTube linki")
                manual_format = st.radio("Çıktı formatı", options=["Dikey Shorts (altyazılı)", "Yatay Video (altyazısız)"],
                                         horizontal=True)
                col_cs, col_ce = st.columns(2)
                with col_cs:
                    manual_start = st.number_input("Başlangıç (sn, opsiyonel)", min_value=0.0, value=0.0, step=1.0)
                with col_ce:
                    manual_end = st.number_input("Bitiş (sn, opsiyonel, 0 = otomatik)", min_value=0.0, value=0.0, step=1.0)
                direct_upload = st.checkbox("İşlem bittiğinde hemen YouTube'a yükle (kotadan bağımsız)", value=False)

                run_btn = st.form_submit_button("⚡ Hemen İndir ve Kurgula", use_container_width=True)
                if run_btn:
                    if not clip_url.strip():
                        st.error("Lütfen bir URL girin.")
                    else:
                        url_value = clip_url.strip()
                        if "kick.com" in url_value:
                            plat = PlatformType.KICK
                        elif "youtube.com" in url_value or "youtu.be" in url_value:
                            plat = PlatformType.YOUTUBE
                        else:
                            plat = PlatformType.TWITCH

                        fmt = (ClipTargetFormat.SHORTS if manual_format.startswith("Dikey")
                               else ClipTargetFormat.HORIZONTAL)
                        clip_obj = ClipItem(
                            clip_id=f"manual_{abs(hash(url_value)) % 1000000}",
                            source_key=f"manual_{abs(hash(url_value)) % 1000000}",
                            platform=plat,
                            channel_name="manual",
                            title="Manuel Video",
                            url=url_value,
                            target_format=fmt,
                            status=ClipStatus.DISCOVERED
                        )
                        clip_db.save_clip(clip_obj)

                        def _manual_run(c=clip_obj, s=manual_start, e=manual_end, pub=direct_upload):
                            try:
                                clip_scheduler.run_single_pipeline_for_clip(
                                    c,
                                    custom_start_sec=s if s > 0 else None,
                                    custom_end_sec=e if e > 0 else None,
                                    publish_now=pub
                                )
                            except Exception as ex:
                                logger.error(f"Manual clip run failed: {ex}")

                        _start_background(_manual_run)
                        st.success("İşlem arka planda başlatıldı! İlerlemeyi canlı loglardan takip edebilirsiniz.")
                        st.rerun()

    with col_right:
        _render_log_panel()

        st.markdown("<br>", unsafe_allow_html=True)

        # Stok Tarayici
        st.subheader("📦 Stok & Önizleme")
        tab_h, tab_s = st.tabs(["🖥️ Yatay Stok", "📱 Shorts Stok"])
        with tab_h:
            _render_stock_tab(ClipTargetFormat.HORIZONTAL, settings)
        with tab_s:
            _render_stock_tab(ClipTargetFormat.SHORTS, settings)
