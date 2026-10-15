import os
import threading
from datetime import datetime, timedelta
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
from app.config import config
from app.services import llm

PLATFORM_LABELS = {
    PlatformType.TWITCH: "🟣 Twitch",
    PlatformType.KICK: "🟢 Kick",
    PlatformType.YOUTUBE: "🔴 YouTube",
}

FORMAT_LABELS = {
    ClipTargetFormat.SHORTS: "📱 Shorts",
    ClipTargetFormat.HORIZONTAL: "🖥️ Yatay",
}

STATUS_LABELS = {
    ClipStatus.DISCOVERED: "🕐 Keşfedildi",
    ClipStatus.DOWNLOADING: "⬇️ İndiriliyor",
    ClipStatus.DOWNLOADED: "📥 Ham Video",
    ClipStatus.ANALYZING: "🔍 Analiz",
    ClipStatus.EDITING: "✂️ Kurgulanıyor",
    ClipStatus.READY: "✅ Stokta Hazır",
    ClipStatus.UPLOADING: "📤 Yükleniyor",
    ClipStatus.PUBLISHED: "🎉 Yayında",
    ClipStatus.SPLIT: "🧩 Bölündü",
    ClipStatus.FAILED: "❌ Hata",
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


def _inject_compact_custom_css():
    """Tüm arayüzü kompakt, ergonomik ve yüksek bilgi yoğunluklu hale getiren stil tanımları."""
    st.markdown("""
        <style>
        /* Kompakt Sayfa Düzeni */
        .block-container {
            padding-top: 0.8rem !important;
            padding-bottom: 1.5rem !important;
            padding-left: 1.2rem !important;
            padding-right: 1.2rem !important;
            max-width: 98% !important;
        }

        /* Başlıklar ve Tipografi */
        h1, h2, h3, h4, h5, h6 {
            margin-top: 0.15rem !important;
            margin-bottom: 0.35rem !important;
            font-weight: 700 !important;
            line-height: 1.2 !important;
        }
        h2 { font-size: 1.25rem !important; }
        h3 { font-size: 1.05rem !important; }
        h4 { font-size: 0.95rem !important; }

        /* Kompakt Metrik Kutuları */
        [data-testid="stMetric"] {
            background: linear-gradient(145deg, #131622 0%, #1a1e30 100%) !important;
            border: 1px solid #23273e !important;
            border-radius: 8px !important;
            padding: 6px 12px !important;
            box-shadow: 0 2px 6px rgba(0,0,0,0.2) !important;
        }
        [data-testid="stMetricLabel"] {
            font-size: 11px !important;
            color: #8f9bb3 !important;
            margin-bottom: 1px !important;
            line-height: 1.2 !important;
        }
        [data-testid="stMetricValue"] {
            font-size: 17px !important;
            font-weight: 700 !important;
            color: #ffffff !important;
            line-height: 1.2 !important;
        }
        [data-testid="stMetricDelta"] {
            font-size: 10.5px !important;
            margin-top: 1px !important;
            line-height: 1 !important;
        }

        /* Kompakt Butonlar */
        .stButton button {
            padding: 2px 8px !important;
            min-height: 28px !important;
            height: 28px !important;
            font-size: 12px !important;
            font-weight: 600 !important;
            border-radius: 5px !important;
            line-height: 1.2 !important;
        }

        /* Link Butonları */
        .stLinkButton a {
            padding: 2px 8px !important;
            min-height: 28px !important;
            height: 28px !important;
            font-size: 12px !important;
            font-weight: 600 !important;
            border-radius: 5px !important;
            line-height: 1.2 !important;
            display: inline-flex !important;
            align-items: center !important;
            justify-content: center !important;
        }

        /* Kompakt Sekmeler (Tabs) */
        .stTabs [data-baseweb="tab-list"] {
            gap: 2px !important;
            border-bottom: 1px solid #23273e !important;
            margin-bottom: 8px !important;
        }
        .stTabs [data-baseweb="tab"] {
            padding: 5px 12px !important;
            font-size: 12.5px !important;
            font-weight: 600 !important;
            border-radius: 6px 6px 0 0 !important;
        }

        /* Kompakt Expander */
        [data-testid="stExpander"] {
            border-radius: 6px !important;
            border: 1px solid #22263d !important;
            background: #11131c !important;
            margin-bottom: 4px !important;
        }
        [data-testid="stExpander"] summary {
            padding: 4px 10px !important;
            font-size: 12px !important;
        }
        [data-testid="stExpander"] [data-testid="stExpanderDetails"] {
            padding: 8px 10px !important;
        }

        /* Form ve Input Elemanları */
        .stTextInput input, .stSelectbox [data-baseweb="select"], .stNumberInput input {
            min-height: 28px !important;
            height: 28px !important;
            font-size: 12px !important;
            padding: 2px 8px !important;
        }
        .stCheckbox label {
            font-size: 12px !important;
        }
        [data-testid="stForm"] {
            padding: 10px !important;
            border-radius: 8px !important;
            border: 1px solid #23273e !important;
            background: #10121a;
        }

        /* Progress Bar Kompakt */
        [data-testid="stProgress"] > div {
            height: 6px !important;
            border-radius: 3px !important;
        }

        /* Ayırıcı Çizgiler */
        hr {
            margin: 6px 0 !important;
            border-color: #23273e !important;
        }
        </style>
    """, unsafe_allow_html=True)


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
        from app.services.video_progress_tracker import video_progress_tracker
        video_progress_tracker.start_task(
            clip_id=clip.clip_id,
            title=clip.youtube_title or clip.title,
            channel_name=clip.channel_name,
            target_format=clip.target_format.value,
            platform=clip.platform.value
        )
        video_progress_tracker.update_stage(
            stage="uploading",
            stage_label="YouTube'a Gönderiliyor (%95)",
            percent=95.0,
            detail=f"YouTube API ile anında yükleniyor: {clip.title[:35]}"
        )
        # Manuel yuklemede Prime Time veya kilit beklemeden aninda YouTube'a yukle
        video_id = shorts_publisher.publish_clip(clip)
        logger.info(f"Manuel YouTube yukleme basarili: {clip.clip_id} -> https://youtu.be/{video_id}")
    except Exception as e:
        err_msg = f"Manuel yükleme hatası: {e}"
        logger.error(err_msg)
        clip_db.update_clip_status(clip.clip_id, ClipStatus.FAILED, error_message=err_msg)
        from app.services.video_progress_tracker import video_progress_tracker
        video_progress_tracker.finish_task(clip.clip_id, success=False, error=err_msg)


# ================= CANLI İLERLEME & MOTOR LOGLARI (KOMPAKT) =================

@st.fragment(run_every="2s")
def _render_live_pipeline_panel():
    """Video başına detaylı indirme/kurgu ilerlemesi ve canlı durum paneli (ultra-kompakt)."""
    # Canlı stok ve görev tamamlama reaktif tetikleyicisi (F5 atmadan güncellenmesi için)
    current_counter = video_progress_tracker.get_change_counter()
    last_counter = st.session_state.get("_last_progress_counter", current_counter)
    cur_ready_total = clip_db.count_clips(status="ready")
    last_ready_total = st.session_state.get("_last_ready_total", cur_ready_total)

    if current_counter != last_counter or cur_ready_total != last_ready_total:
        st.session_state["_last_progress_counter"] = current_counter
        st.session_state["_last_ready_total"] = cur_ready_total
        try:
            st.rerun(scope="app")
        except Exception:
            st.rerun()

    task = video_progress_tracker.get_active_task()

    if task:
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
            <div style="background: linear-gradient(135deg, #131728 0%, #1a1e36 100%); border: 1px solid #363d66; border-left: 4px solid #00E676; border-radius: 8px; padding: 6px 12px; margin-bottom: 4px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px;">
                    <div style="display: flex; align-items: center; gap: 6px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                        <span style="display: inline-block; width: 8px; height: 8px; background-color: #00E676; border-radius: 50%; box-shadow: 0 0 6px #00E676; flex-shrink: 0;"></span>
                        <span style="font-weight: 700; color: #FFFFFF; font-size: 12.5px;">CANLI İŞLENİYOR</span>
                        <span style="background: #252b48; color: #a5b4fc; padding: 1px 5px; border-radius: 3px; font-size: 11px; font-weight: 600;">{fmt}</span>
                        <span style="color: #e2e8f0; font-size: 12.5px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; max-width: 480px;">{title}</span>
                        <span style="color: #818cf8; font-size: 11.5px;">({channel})</span>
                    </div>
                    <span style="font-weight: 800; font-size: 13.5px; color: #00E676; font-family: monospace; flex-shrink: 0;">{pct_display}</span>
                </div>
                <div style="display: flex; gap: 12px; font-size: 11px; color: #94a3b8; align-items: center;">
                    <span>Aşama: <strong style="color: #38bdf8;">{stage_label}</strong></span>
                    <span>Hız: <strong style="color: #cbd5e1;">{speed}</strong></span>
                    <span>Kalan: <strong style="color: #cbd5e1;">{eta}</strong></span>
                    <span>Süre: <strong style="color: #cbd5e1;">{elapsed}</strong></span>
                    {f'<span style="color: #a78bfa; overflow: hidden; text-overflow: ellipsis;">ℹ️ {detail}</span>' if detail else ''}
                </div>
            </div>
        """, unsafe_allow_html=True)
        st.progress(progress_val)
    else:
        # Motor beklemede
        settings = clip_db.get_settings()
        ready_h = clip_db.count_clips(status="ready", target_format="horizontal")
        ready_s = clip_db.count_clips(status="ready", target_format="shorts")
        target_h = settings.target_stock_horizontal
        target_s = settings.target_stock_shorts

        is_prime, prime_slot = clip_publisher.is_prime_time_now(settings)
        prime_info = f"🔥 Prime Time Slotunda ({prime_slot})" if is_prime else f"⏰ Prime Slotları: {settings.prime_time_slots}"

        if clip_scheduler.is_running:
            status_html = f"""
                <div style="background: #111524; border: 1px solid #1e2640; border-left: 4px solid #10b981; border-radius: 6px; padding: 5px 12px; margin-bottom: 4px; display: flex; justify-content: space-between; align-items: center; font-size: 12px;">
                    <div style="display: flex; align-items: center; gap: 6px;">
                        <span style="display: inline-block; width: 7px; height: 7px; background-color: #10b981; border-radius: 50%;"></span>
                        <span style="color: #e2e8f0; font-weight: 600;">Otonom Motor Boşta & Nöbette</span>
                        <span style="color: #64748b;">·</span>
                        <span style="color: #94a3b8;">Stoklar: Yatay <strong>{ready_h}/{target_h}</strong> · Shorts <strong>{ready_s}/{target_s}</strong></span>
                        <span style="color: #64748b;">·</span>
                        <span style="color: #38bdf8;">{prime_info}</span>
                    </div>
                    <span style="color: #64748b; font-size: 11px;">Sıralı (1'er 1'er) indirme modu</span>
                </div>
            """
        else:
            status_html = """
                <div style="background: #171520; border: 1px solid #2e2133; border-left: 4px solid #f59e0b; border-radius: 6px; padding: 5px 12px; margin-bottom: 4px; display: flex; justify-content: space-between; align-items: center; font-size: 12px;">
                    <span style="color: #fcd34d; font-weight: 500;">⏸️ Motor Durduruldu. Otomasyonu başlatmak için yukarıdaki "Otonom Motoru Başlat" butonuna tıklayın.</span>
                    <span style="color: #78716c; font-size: 11px;">Beklemede</span>
                </div>
            """
        st.markdown(status_html, unsafe_allow_html=True)

    # Canlı Motor Logları (Ultra-Kompakt Expander)
    with st.expander("📜 Canlı Motor ve Keşif Logları (Son Olaylar)", expanded=False):
        logs = clip_scheduler.get_recent_logs(limit=25)
        log_box = ""
        for l in logs:
            color = "#00E676" if l["level"] == "info" else ("#FFB74D" if l["level"] == "warning" else "#FF5252")
            log_box += (
                f"<div style='font-family: monospace; font-size: 11.5px; margin-bottom: 2px; line-height: 1.35;'>"
                f"<span style='color: #7986cb;'>[{l['timestamp']}]</span> "
                f"<span style='color: {color}; font-weight: bold;'>[{l['level'].upper()}]</span> "
                f"<span style='color: #e0e0e0;'>{l['message']}</span></div>"
            )
        if not log_box:
            log_box = "<div style='color: #888; font-family: monospace; font-size: 11.5px;'>Henüz log kaydı yok.</div>"

        st.markdown(f"""
            <div style="background-color: #0c0d14; border: 1px solid #202438; border-radius: 5px; padding: 6px 10px; max-height: 140px; overflow-y: auto;">
                {log_box}
            </div>
        """, unsafe_allow_html=True)


# ================= MEDYA HAVUZU & STOK KARTLARI (KOMPAKT SATIR DÜZENİ) =================

def _render_video_card(clip: ClipItem, settings: ClipPipelineSettings, is_stock: bool = True):
    """Her video için kompakt medya şeridi, hızlı aksiyonlar ve açılır önizleme/filtreler."""
    with st.container(border=True):
        col_thumb, col_info, col_acts = st.columns([1.1, 6.4, 2.5])

        with col_thumb:
            if clip.thumbnail_url:
                st.image(clip.thumbnail_url, width="stretch")
            else:
                fmt_badge = "📱 SHORTS" if clip.target_format == ClipTargetFormat.SHORTS else "🖥️ YATAY"
                st.markdown(f"""
                    <div style="background: #1e2238; border: 1px solid #2f3659; border-radius: 5px; height: 50px; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; font-size: 10px; font-weight: 700; color: #a5b4fc;">
                        <span>{fmt_badge}</span>
                        <span style="color: #64748b; font-size: 9px;">{clip.duration:.0f}s</span>
                    </div>
                """, unsafe_allow_html=True)

        with col_info:
            # 1. Satır: Başlık ve YouTube Linki
            yt_link = f" · <a href='https://youtu.be/{clip.youtube_video_id}' target='_blank' style='color: #ff5252; text-decoration: none; font-size: 11px;'>[▶️ YouTube'da Aç]</a>" if clip.youtube_video_id else ""
            st.markdown(
                f"<div style='font-size: 13px; font-weight: 700; color: #FFFFFF; line-height: 1.25; margin-bottom: 2px;'>"
                f"{clip.title[:85]}{yt_link}</div>",
                unsafe_allow_html=True
            )

            # 2. Satır: Rozetler ve Meta Veriler (Tek Satırda Yatay)
            sched_badge = ""
            if is_stock and clip.scheduled_publish_time:
                sched_dt = clip.scheduled_publish_time
                now = datetime.now()
                if sched_dt.date() == now.date():
                    sched_text = f"Bugün {sched_dt.strftime('%H:%M')}"
                elif sched_dt.date() == (now.date() + timedelta(days=1)):
                    sched_text = f"Yarın {sched_dt.strftime('%H:%M')}"
                else:
                    sched_text = f"{sched_dt.strftime('%d.%m %H:%M')}"
                sched_badge = f"<span style='background: linear-gradient(90deg, #7b1fa2, #e91e63); color: #fff; padding: 1px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; margin-right: 4px;'>⏰ {sched_text}</span>"

            fmt_icon = "📱 Shorts" if clip.target_format == ClipTargetFormat.SHORTS else "🖥️ Yatay"
            status_txt = STATUS_LABELS.get(clip.status, clip.status.value)

            # Kaynak video bağlantısı: YouTube/Twitch/Kick orijinal URL'si tıklandığında yeni sekmede açılır
            src_url = clip.url or ""
            if src_url:
                platform_display = f"(<a href='{src_url}' target='_blank' style='color: #60a5fa; text-decoration: underline; font-weight: 600;' title='Orijinal Kaynak Videoyu Aç'>{clip.platform.value}</a>)"
            else:
                platform_display = f"({clip.platform.value})"

            draft_badge = ""
            if clip.status == ClipStatus.READY and clip.youtube_video_id:
                draft_badge = f"<a href='https://youtu.be/{clip.youtube_video_id}' target='_blank' style='background: #064e3b; color: #34d399; padding: 1px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; text-decoration: none; margin-right: 4px;' title='YouTube üzerinde taslak yüklendi, yayın saatinde saniyesinde herkese açılacak'>☁️ YouTube Taslağı Hazır</a>"

            meta_chips = (
                f"{sched_badge}{draft_badge}"
                f"<span style='background: #20243a; color: #9fa8da; padding: 1px 5px; border-radius: 3px; font-size: 11px; margin-right: 4px;'>{fmt_icon}</span>"
                f"<span style='background: #193325; color: #81c784; padding: 1px 5px; border-radius: 3px; font-size: 11px; margin-right: 4px;'>{status_txt}</span>"
                f"<span style='color: #cbd5e1; font-size: 11.5px; font-weight: 600;'>{clip.channel_name}</span> "
                f"<span style='color: #64748b; font-size: 11px;'>{platform_display} · ⏱️ {clip.duration:.0f}s · 👁️ {clip.view_count:,}</span>"
            )
            if clip.created_at_platform:
                date_str = clip.created_at_platform[:10]
                meta_chips += f" · <span style='color: #a7f3d0; font-size: 11px;'>📅 {date_str}</span>"
            if clip.viral_score:
                meta_chips += f" · <span style='color: #ffb74d; font-size: 11px; font-weight: bold;'>🔥 Viral: {clip.viral_score}/10</span>"
            if is_stock and clip.priority:
                meta_chips += f" · <span style='color: #80d8ff; font-size: 11px;'>⬆️ Öncelik: {clip.priority}</span>"

            st.markdown(f"<div style='line-height: 1.4;'>{meta_chips}</div>", unsafe_allow_html=True)

            # 3. Satır: Varsa Kanca Başlığı veya Hata Mesajı
            if clip.hook_text:
                st.markdown(f"<div style='font-size: 11px; color: #a5b4fc; font-style: italic; margin-top: 1px;'>🎣 Kanca: \"{clip.hook_text}\"</div>", unsafe_allow_html=True)
            if clip.error_message:
                st.markdown(f"<div style='font-size: 11px; color: #ef4444; margin-top: 1px;'>⚠️ {clip.error_message}</div>", unsafe_allow_html=True)

        with col_acts:
            # Hızlı Aksiyon Butonları (Kompakt Grid)
            if clip.status == ClipStatus.READY:
                c_pub, c_p1, c_p2, c_del = st.columns([1.8, 0.8, 0.8, 0.8])
                with c_pub:
                    btn_text = "⚡ Yayına Al" if clip.youtube_video_id else "🚀 Yükle"
                    if st.button(btn_text, key=f"pub_btn_{clip.clip_id}", width="stretch", type="primary"):
                        _start_background(_publish_clip_bg, clip.clip_id)
                        action_msg = "Yayına alınıyor" if clip.youtube_video_id else "Yükleme başlatıldı"
                        st.toast(f"{action_msg}: {clip.title[:25]}")
                        st.rerun()
                if is_stock:
                    with c_p1:
                        if st.button("⬆️", key=f"p_up_{clip.clip_id}", help="Önceliği artır", width="stretch"):
                            clip_db.update_clip_metadata(clip.clip_id, priority=clip.priority + 10)
                            clip_publisher.update_scheduled_times_in_db(settings)
                            st.rerun()
                    with c_p2:
                        if st.button("⬇️", key=f"p_dn_{clip.clip_id}", help="Önceliği düşür", width="stretch"):
                            clip_db.update_clip_metadata(clip.clip_id, priority=max(0, clip.priority - 10))
                            clip_publisher.update_scheduled_times_in_db(settings)
                            st.rerun()
                    with c_del:
                        if st.button("🗑️", key=f"del_btn_{clip.clip_id}", help="Sil", width="stretch"):
                            clip_db.delete_clip(clip.clip_id)
                            st.toast(f"Silindi: {clip.title[:20]}")
                            st.rerun()
                else:
                    with c_del:
                        if st.button("🗑️", key=f"del_btn_{clip.clip_id}", help="Sil", width="stretch"):
                            clip_db.delete_clip(clip.clip_id)
                            st.toast(f"Silindi: {clip.title[:20]}")
                            st.rerun()

            elif clip.status in (ClipStatus.DOWNLOADED, ClipStatus.DISCOVERED, ClipStatus.FAILED):
                c_proc, c_del = st.columns([2.2, 1.0])
                with c_proc:
                    if st.button("⚙️ Kurgula", key=f"proc_btn_{clip.clip_id}", width="stretch"):
                        _start_background(_process_clip_bg, clip.clip_id)
                        st.toast(f"Kurgulanıyor: {clip.title[:25]}")
                        st.rerun()
                with c_del:
                    if st.button("🗑️", key=f"del_btn_{clip.clip_id}", help="Sil", width="stretch"):
                        clip_db.delete_clip(clip.clip_id)
                        st.toast(f"Silindi: {clip.title[:20]}")
                        st.rerun()

            elif clip.status == ClipStatus.PUBLISHED:
                c_yt, c_del = st.columns([2.2, 1.0])
                with c_yt:
                    if clip.youtube_video_id:
                        st.link_button("▶️ YouTube", f"https://youtu.be/{clip.youtube_video_id}", width="stretch")
                with c_del:
                    if st.button("🗑️", key=f"del_btn_{clip.clip_id}", help="Sil", width="stretch"):
                        clip_db.delete_clip(clip.clip_id)
                        st.toast(f"Silindi: {clip.title[:20]}")
                        st.rerun()
            else:
                if st.button("🗑️", key=f"del_btn_{clip.clip_id}", help="Sil", width="stretch"):
                    clip_db.delete_clip(clip.clip_id)
                    st.toast(f"Silindi: {clip.title[:20]}")
                    st.rerun()

        # Açılır Mini Panel: Video Oynatıcı & YouTube Yayın Ayarlarını Düzenle
        video_file = clip.processed_video_path or clip.raw_video_path
        has_video = video_file and os.path.exists(video_file)

        with st.expander("🎬 Videoyu İzle & ✏️ YouTube Yayın Filtrelerini Düzenle", expanded=False):
            col_v_pv, col_v_edit = st.columns([1.5, 2.5])

            with col_v_pv:
                if has_video:
                    st.video(video_file)
                elif clip.thumbnail_url:
                    st.image(clip.thumbnail_url, width="stretch")
                else:
                    st.caption("Video dosyası diskte bulunamadı.")

            with col_v_edit:
                with st.form(f"edit_meta_{clip.clip_id}"):
                    new_title = st.text_input("YouTube Başlığı", value=clip.youtube_title or clip.title)
                    new_desc = st.text_area("Açıklama", value=clip.youtube_description or "", height=65)

                    c_m_t, c_m_c = st.columns([1.8, 1.2])
                    with c_m_t:
                        tags_str = ", ".join(clip.youtube_tags or [])
                        new_tags_input = st.text_input("Etiketler (Virgülle)", value=tags_str)
                    with c_m_c:
                        cat_keys = list(YOUTUBE_CATEGORIES.keys())
                        current_cat = clip.youtube_category_id or settings.youtube_category_id or "20"
                        cat_idx = cat_keys.index(current_cat) if current_cat in cat_keys else 0
                        new_cat = st.selectbox(
                            "Kategori",
                            options=cat_keys,
                            format_func=lambda k: YOUTUBE_CATEGORIES.get(k, k),
                            index=cat_idx
                        )

                    c_k1, c_k2, c_th = st.columns([1, 1, 2])
                    with c_k1:
                        new_kids = st.checkbox("Çocuklara Özel", value=clip.made_for_kids)
                    with c_k2:
                        new_age = st.checkbox("18+ Yaş Sınırı", value=clip.age_restricted)
                    with c_th:
                        thumb_input = st.text_input("Thumbnail Dosya Yolu", value=clip.custom_thumbnail_path or "")

                    c_k3, c_k4 = st.columns([1, 1])
                    with c_k3:
                        new_paid = st.checkbox("Ücretli Tanıtım / Sponsorluk", value=getattr(clip, "has_paid_promotion", False))
                    with c_k4:
                        new_synth = st.checkbox("Yapay Zeka / Sentetik Medya", value=getattr(clip, "contains_synthetic_media", False))

                    save_meta = st.form_submit_button("💾 Kaydet ve Güncelle", width="stretch")
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
                            has_paid_promotion=new_paid,
                            contains_synthetic_media=new_synth,
                            custom_thumbnail_path=thumb_input.strip() or None
                        )
                        st.success("Filtreler güncellendi!")
                        st.rerun()


# ================= ANA SEKMELER & KOMPAKT DASHBOARD =================

def render_clips_automation_tab():
    # Kompakt stil enjeksiyonu
    _inject_compact_custom_css()

    if "_last_progress_counter" not in st.session_state:
        st.session_state["_last_progress_counter"] = video_progress_tracker.get_change_counter()
    if "_last_ready_total" not in st.session_state:
        st.session_state["_last_ready_total"] = clip_db.count_clips(status="ready")

    settings = clip_db.get_settings()
    try:
        clip_publisher.update_scheduled_times_in_db(settings)
    except Exception:
        pass

    quota = clip_publisher.get_quota_summary(settings)

    # 1. Kompakt Üst Araç Çubuğu (Toolbar / Command Strip)
    col_brand, col_engine_bar = st.columns([6.5, 3.5])
    with col_brand:
        st.markdown("""
            <div style="display: flex; align-items: center; gap: 8px; padding: 2px 0;">
                <span style="font-size: 19px;">⚡</span>
                <span style="font-size: 16px; font-weight: 800; color: #FFFFFF; letter-spacing: -0.3px;">AutoShorts Studio</span>
                <span style="color: #64748b; font-size: 12px; margin-left: 4px;">Klip & Shorts Otonom Fabrikası (1'er 1'er İndirme & Prime Time)</span>
            </div>
        """, unsafe_allow_html=True)

    with col_engine_bar:
        c_btn, c_pill = st.columns([1.6, 1.4])
        with c_btn:
            if not clip_scheduler.is_running:
                if st.button("🚀 Motoru Başlat", width="stretch", type="primary"):
                    clip_scheduler.start()
                    st.rerun()
            else:
                if st.button("🛑 Motoru Durdur", width="stretch"):
                    clip_scheduler.stop()
                    st.rerun()
        with c_pill:
            st_color = "#10b981" if clip_scheduler.is_running else "#f59e0b"
            st_text = "🟢 AKTİF" if clip_scheduler.is_running else "⏸️ DURDU"
            st.markdown(f"""
                <div style="background: #141724; border: 1px solid #282f4c; border-radius: 5px; height: 28px; display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700; color: {st_color};">
                    {st_text}
                </div>
            """, unsafe_allow_html=True)

    # 2. Beşli Kompakt Metrik Şeridi (Tek Satır)
    c_m1, c_m2, c_m3, c_m4, c_m5 = st.columns(5)

    with c_m1:
        is_prime, prime_slot = clip_publisher.is_prime_time_now(settings)
        p_val = f"🔥 {prime_slot}" if is_prime else "Normal Saat"
        st.metric("Prime Time", p_val, delta=f"Slotlar: {settings.prime_time_slots}")

    with c_m2:
        streamers = clip_db.list_streamers(active_only=True)
        st.metric("Kaynaklar", f"{len(streamers)} Kanal", help="Takip edilen YouTube & Twitch kanalları")

    with c_m3:
        sq = quota[ClipTargetFormat.SHORTS.value]
        st.metric("Shorts Stoğu", f"{sq['ready_in_stock']} / {sq['target_stock']}", delta=f"Bugün: {sq['published_today']}/{sq['daily_limit']}")

    with c_m4:
        hq = quota[ClipTargetFormat.HORIZONTAL.value]
        st.metric("Yatay Stoğu", f"{hq['ready_in_stock']} / {hq['target_stock']}", delta=f"Bugün: {hq['published_today']}/{hq['daily_limit']}")

    with c_m5:
        is_yt = shorts_publisher.is_youtube_ready()
        yt_badge = "✅ Bağlı" if is_yt else "⚠️ Token Yok"
        st.metric("YouTube Durumu", yt_badge, delta="Oto-Yayın Açık" if settings.auto_upload else "Manuel Mod")

    # 3. Canlı İşlem Hattı ve İlerleme Paneli
    _render_live_pipeline_panel()

    # 4. Medya Havuzu, Stok Yönetimi ve Ayarlar Sekmeleri
    tab_ready_h, tab_ready_s, tab_downloaded, tab_published, tab_settings, tab_streamers = st.tabs([
        f"🖥️ Yatay Stok ({quota['horizontal']['ready_in_stock']}/{quota['horizontal']['target_stock']})",
        f"📱 Shorts Stok ({quota['shorts']['ready_in_stock']}/{quota['shorts']['target_stock']})",
        f"📥 Ham Dosyalar",
        f"🎉 Yayınlananlar",
        f"⚙️ Kurgu & Filtre Ayarları",
        f"📺 Yayıncılar"
    ])

    # A. YATAY STOK SEKMESİ
    with tab_ready_h:
        ready_horizontal = clip_db.list_ready_clips(target_format=ClipTargetFormat.HORIZONTAL.value, limit=30)
        if ready_horizontal:
            for c in ready_horizontal:
                _render_video_card(c, settings, is_stock=True)
        else:
            st.info("Yatay stokta hazır video bulunmuyor. Motor çalıştığında 1'er 1'er indirilip hazırlanacaktır.")

    # B. SHORTS STOK SEKMESİ
    with tab_ready_s:
        ready_shorts = clip_db.list_ready_clips(target_format=ClipTargetFormat.SHORTS.value, limit=30)
        if ready_shorts:
            for c in ready_shorts:
                _render_video_card(c, settings, is_stock=True)
        else:
            st.info("Shorts stoğunda hazır video bulunmuyor. Motor çalıştığında klipler 1'er 1'er indirilip kurgulanacaktır.")

    # C. İNDİRİLEN HAM DOSYALAR (RAW ARCHIVE)
    with tab_downloaded:
        downloaded_clips = clip_db.list_downloaded_clips(limit=25)
        if downloaded_clips:
            for c in downloaded_clips:
                _render_video_card(c, settings, is_stock=False)
        else:
            st.info("Şu an diskte kurgu bekleyen ham video bulunmuyor.")

    # D. YAYINLANANLAR
    with tab_published:
        published_clips = clip_db.list_clips_by_status(status=ClipStatus.PUBLISHED, limit=30)
        if published_clips:
            for c in published_clips:
                _render_video_card(c, settings, is_stock=False)
        else:
            st.info("Henüz YouTube'da yayınlanan video kaydı yok.")

    # E. GELİŞMİŞ AYARLAR, AI OTOPİLOT & YOUTUBE FİLTRELERİ
    with tab_settings:
        st.markdown("""
            <div style="background: linear-gradient(135deg, #131728 0%, #1c223c 100%); border: 1px solid #2e375c; border-radius: 8px; padding: 10px 14px; margin-bottom: 10px;">
                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
                    <div>
                        <div style="font-size: 14.5px; font-weight: 800; color: #FFFFFF; display: flex; align-items: center; gap: 6px;">
                            <span>🤖</span> AI Otopilot & YouTube Para Kazanma (Monetizasyon) Paneli
                        </div>
                        <div style="font-size: 11.5px; color: #94a3b8; margin-top: 2px;">
                            Tekrarlanan içerik cezasını engelleyen %100 özgünleştirme motoru, clickbait kapak tasarımı ve Prime Time otomasyonu.
                        </div>
                    </div>
                    <div style="display: flex; gap: 6px;">
                        <span style="background: #064e3b; color: #34d399; font-size: 11px; font-weight: 700; padding: 2px 7px; border-radius: 4px; border: 1px solid #059669;">
                            🛡️ Çocuklara Özel Değil (Gelir Açık)
                        </span>
                        <span style="background: #064e3b; color: #34d399; font-size: 11px; font-weight: 700; padding: 2px 7px; border-radius: 4px; border: 1px solid #059669;">
                            🔞 18+ Yaş Sınırı Devre Dışı
                        </span>
                    </div>
                </div>
            </div>
        """, unsafe_allow_html=True)

        st.caption("ℹ️ *AI modellerini ve 3 kademeli kesintisiz zeka (Main + Fallback) zincirini **⚙️ Ayarlar ➔ LLM Ayarları** menüsünden yönetebilirsiniz.*")

        # 2. Ana Strateji, Kota & Prime Time Formu
        with st.form("advanced_pipeline_settings_form"):
            st.markdown("##### 📅 Otonom Prime Time & Stok Kotası (Para Kazanma Stratejisi)")
            c_s1, c_s2, c_s3, c_s4, c_s5 = st.columns(5)
            with c_s1:
                target_stock_h = st.number_input("Yatay Stok Hedefi (Otomatik)", min_value=1, max_value=20, value=settings.target_stock_horizontal)
            with c_s2:
                daily_limit_h = st.number_input("Günlük Yatay Kotası (Otomatik)", min_value=1, max_value=10, value=settings.daily_horizontal_limit)
            with c_s3:
                target_stock_s = st.number_input("Shorts Stok Hedefi (Otomatik)", min_value=1, max_value=30, value=settings.target_stock_shorts)
            with c_s4:
                daily_limit_s = st.number_input("Günlük Shorts Kotası (Otomatik)", min_value=1, max_value=10, value=settings.daily_shorts_limit)
            with c_s5:
                stock_max_val = st.number_input("Azami Hazır Limit (Otomatik)", min_value=1, max_value=50, value=int(getattr(settings, "stock_max_ready_per_format", 15)))

            c_pt1, c_pt2, c_win1, c_win2, c_win3 = st.columns([2.5, 1.5, 1.0, 1.0, 1.0])
            with c_pt1:
                prime_slots_input = st.text_input(
                    "🔥 Prime Time Yayın Slotları (Otomatik - En Çok İzlenen Saatler)",
                    value=settings.prime_time_slots,
                    help="Günün en çok izlenme alan saatleri. Örn: 18:00, 20:00, 22:00. Sıradaki en viral videolar bu saatlerde 1-1-1 yüklenir."
                )
            with c_pt2:
                enforce_prime_toggle = st.checkbox(
                    "Yalnızca Prime Time'da Yayınla (Önerilen)",
                    value=getattr(settings, "enforce_prime_time", True),
                )
            with c_win1:
                win_start = st.number_input("Pencere Başlangıç", min_value=0, max_value=23, value=settings.publish_window_start_hour)
            with c_win2:
                win_end = st.number_input("Pencere Bitiş", min_value=1, max_value=24, value=settings.publish_window_end_hour)
            with c_win3:
                interval_min = st.number_input("Kontrol (Dk)", min_value=5, max_value=120, value=settings.publish_interval_minutes)

            st.markdown("##### 📤 YouTube Otomasyonu & Gelir Güvenliği")
            c_y1, c_y2, c_y3 = st.columns([1.5, 1.5, 1.5])
            with c_y1:
                auto_upload_toggle = st.checkbox("YouTube Otomatik Yayınlama Açık", value=settings.auto_upload)
            with c_y2:
                privacy_opt = st.selectbox(
                    "Yayın Gizliliği",
                    options=["public", "private", "unlisted"],
                    format_func=lambda x: {"public": "Herkese Açık (Canlı & Para Kazanma)", "private": "Gizli (Taslak İnceleme)", "unlisted": "Liste Dışı"}.get(x, x),
                    index=["public", "private", "unlisted"].index(settings.privacy_status) if settings.privacy_status in ["public", "private", "unlisted"] else 0
                )
            with c_y3:
                cat_keys = list(YOUTUBE_CATEGORIES.keys())
                cur_cat = settings.youtube_category_id or "20"
                c_idx = cat_keys.index(cur_cat) if cur_cat in cat_keys else 0
                cat_select = st.selectbox(
                    "Varsayılan YouTube Kategorisi",
                    options=cat_keys,
                    format_func=lambda k: YOUTUBE_CATEGORIES.get(k, k),
                    index=c_idx
                )

            pre_upload_toggle = st.checkbox(
                "☁️ Stoktaki Videoları Önceden YouTube'a Taslak/Gizli Yükle (Önerilen)",
                value=getattr(settings, "pre_upload_as_draft", True),
                help="Video hazırlandığında tüm ayarlarıyla (başlık, açıklama, etiket, kapak görseli) YouTube'a 'Gizli Taslak' olarak yüklenir. YouTube arka planda HD/4K işlemesini tamamlar. Yayın saati (Prime Time) geldiğinde anında 'Herkese Açık' yapılır; yükleme beklemez ve stoktan -1 düşer."
            )

            c_decl1, c_decl2 = st.columns(2)
            with c_decl1:
                paid_promo_toggle = st.checkbox(
                    "💼 Ücretli Tanıtım / Sponsorluk (Varsayılan: Hayır)",
                    value=getattr(settings, "has_paid_promotion", False),
                    help="YouTube yükleme beyanında 'Bu video ücretli ürün yerleşimi, sponsorluk veya onay içeriyor' sorusuna otomatik HAYIR cevabı verilir."
                )
            with c_decl2:
                synthetic_media_toggle = st.checkbox(
                    "🤖 Yapay Zeka / Değiştirilmiş İçerik (Varsayılan: Hayır)",
                    value=getattr(settings, "contains_synthetic_media", False),
                    help="YouTube yükleme beyanında 'Gerçekçi görünen değiştirilmiş veya yapay zeka tarafından oluşturulmuş içerik içeriyor mu?' sorusuna otomatik HAYIR cevabı verilir."
                )

            st.markdown("##### 🎯 Video Keşfi, Güncellik & Viral Video Avı")
            c_vh1, c_vh2, c_vh3 = st.columns([1.6, 1.2, 1.2])
            with c_vh1:
                viral_hunter_toggle = st.checkbox(
                    "🚀 Eski Viral Video Avı (300k+ Eski Videoları Dahil Et)",
                    value=settings.enable_viral_hunter,
                    help="Kapalıyken sistem SADECE belirlediğiniz güncellik süresi (son X gün) içinde yüklenmiş taze videoları seçer. Açıkken belirlenen izlenme barajını aşan eski viral videolar da dahil edilir."
                )
            with c_vh2:
                max_age_val = st.number_input(
                    "📅 Video Yaş Limiti (Son X Gün)",
                    min_value=1,
                    max_value=365,
                    value=int(settings.max_video_age_days),
                    help="Örn: 7 seçilirse sadece son 7 gün içinde paylaşılan güncel videolar seçilir."
                )
            with c_vh3:
                viral_baraj_val = st.number_input(
                    "🔥 Viral Barajı (İzlenme)",
                    min_value=1000,
                    max_value=5000000,
                    value=int(settings.viral_threshold_for_older),
                    step=5000,
                    help="Viral video avı açıkken, yaş limitinden eski videoların kabul edilmesi için gereken asgari izlenme sayısı."
                )

            if viral_hunter_toggle:
                st.warning(f"🚀 **Viral Video Avı AÇIK:** Son {max_age_val} günden eski olsa bile **{viral_baraj_val:,}+** üzeri izlenen eski viral videolar taranıp indirilecek.")
            else:
                st.info(f"🕒 **Sadece Güncel Videolar Modu AKTİF:** Sistem yalnızca son **{max_age_val} gün** içinde yüklenmiş yeni videoları seçecek. 300k-500k izlenmiş eski videolar elenecektir.")

            st.markdown("##### 🖥️ Yatay Video & Telif Koruması (İçerik Hırsızlığını Önle)")
            c_h1, c_h2 = st.columns([2.8, 1.2])
            with c_h1:
                reaction_only_toggle = st.checkbox(
                    "🛡️ Düz Funny Moments / Montajları Ele (Yalnızca Yayıncının İzlediği Videoları Kabul Et)",
                    value=getattr(settings, "filter_horizontal_reactions_only", True),
                    help="Düz funny moments/derleme videoları içerik hırsızlığı riski taşıdığı için elenir. Yalnızca yayıncının izlediği, tepki ve reaksiyon verdiği videolar kabul edilir."
                )

            st.markdown("##### 🌐 İnternet & İndirme Hız Limiti (Bant Genişliği Koruması)")
            c_net1, c_net2 = st.columns([2.0, 2.0])
            with c_net1:
                dl_limit_val = st.number_input(
                    "İndirme Hız Limiti (MB/s)",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(getattr(settings, "download_rate_limit_mb", 0.0) or 0.0),
                    step=1.0,
                    help="0 girilirse limitsizdir. Ev veya iş internetinin çökmemesi veya yavaşlamaması için örn: 3 veya 5 MB/s sınır koyabilirsiniz."
                )
            with c_net2:
                if dl_limit_val > 0:
                    st.info(f"⚡ En fazla **{dl_limit_val:g} MB/s** hızla sınırlandırılacak (İnternetiniz yavaşlamaz).")
                else:
                    st.caption("🚀 Limitsiz: İnternetin izin verdiği maksimum hız kullanılır.")

            st.markdown("##### 📱 Shorts Video Kurgusu & Yayıncı Düzeni")
            c_sh1, c_sh2, c_sh3, c_sh4 = st.columns(4)
            with c_sh1:
                source_mode_opts = ["smart_hybrid", "only_native_shorts", "convert_horizontal"]
                curr_sm = getattr(settings, "shorts_source_mode", "smart_hybrid")
                shorts_source_select = st.selectbox(
                    "Shorts Kaynak Tercihi",
                    options=source_mode_opts,
                    index=source_mode_opts.index(curr_sm) if curr_sm in source_mode_opts else 0,
                    format_func=lambda x: {
                        "smart_hybrid": "Otomatik Hibrit (Dikey varsa koru, yataysa kurgula)",
                        "only_native_shorts": "Sadece Hazır Dikey Shorts İndir",
                        "convert_horizontal": "Yatay Videoları Dikey Kurgula"
                    }.get(x, x),
                    help="Shorts için videoların nasıl tedarik edileceği."
                )
            with c_sh2:
                layout_opts = ["streamer_facecam", "center_crop", "split"]
                curr_lo = getattr(settings, "video_layout", "streamer_facecam")
                layout_select = st.selectbox(
                    "Yatay Video Dikey Kurgusu",
                    options=layout_opts,
                    index=layout_opts.index(curr_lo) if curr_lo in layout_opts else 0,
                    format_func=lambda x: {
                        "streamer_facecam": "Webcam Üstte + Oyun Altta (Önerilen)",
                        "center_crop": "Merkeze Odaklanıp Yakınlaştır (Center Crop)",
                        "split": "Eşit İkiye Böl (Split)"
                    }.get(x, x),
                    help="Yatay videodan 9:16 Shorts oluşturulurken uygulanacak viral montaj formatı."
                )
            with c_sh3:
                cam_pos_opts = ["auto", "bottom_left", "bottom_right", "top_left", "top_right"]
                curr_cp = getattr(settings, "facecam_position", "auto") or "auto"
                facecam_select = st.selectbox(
                    "Yayıncı Webcam Konumu",
                    options=cam_pos_opts,
                    index=cam_pos_opts.index(curr_cp) if curr_cp in cam_pos_opts else 0,
                    format_func=lambda x: {
                        "auto": "🤖 Otomatik Bul (AI - Önerilen)",
                        "bottom_left": "Sol Alt (Manuel)",
                        "bottom_right": "Sağ Alt (Manuel)",
                        "top_left": "Sol Üst (Manuel)",
                        "top_right": "Sağ Üst (Manuel)"
                    }.get(x, x),
                    help="🤖 Otomatik Bul: OpenCV ve bilgisayarlı görü ile videodaki yüzü ve yayıncı kamerasını otomatik tespit eder."
                )
            with c_sh4:
                skip_sub_toggle = st.checkbox(
                    "Mevcut Altyazılı Videoları Koru",
                    value=getattr(settings, "skip_subtitles_if_detected", True),
                    help="İndirilen videoda zaten altyazı varsa, çift altyazı çakışması olmaması için Whisper altyazısını atlar."
                )

            st.markdown("##### 🎨 AI Özgünleştirme, Clickbait Thumbnail & Kurgu")
            c_k1, c_k2, c_k3, c_k4 = st.columns(4)
            with c_k1:
                ai_title_toggle = st.checkbox("AI Özgün Başlık & Etiket (Önerilen)", value=settings.ai_title_generation)
                auto_thumb_toggle = st.checkbox("Clickbait Sarı Yazılı Thumbnail (Önerilen)", value=settings.auto_generate_thumbnail)
                sub_toggle = st.checkbox("Shorts Altyazı Ekle (faster-whisper)", value=settings.subtitle_enabled)
            with c_k2:
                llm_moment_toggle = st.checkbox("LLM ile Viral An Tespiti (Önerilen)", value=settings.llm_moment_detection)
                hook_toggle = st.checkbox("Shorts Kanca Başlık Çıkartması", value=settings.hook_overlay_enabled)
                yt_search_toggle = st.checkbox("YouTube Kesit Araması Yap", value=settings.youtube_search_enabled)
            with c_k3:
                intro_trim_val = st.number_input("Intro Kesme (sn - Otomatik)", min_value=0.0, max_value=60.0, value=float(settings.intro_trim_seconds), step=1.0)
                sub_font_val = st.text_input("Altyazı Fontu", value=settings.subtitle_font or "Arial")
            with c_k4:
                outro_trim_val = st.number_input("Outro Kesme (sn - Otomatik)", min_value=0.0, max_value=60.0, value=float(settings.outro_trim_seconds), step=1.0)
                sub_size_val = st.number_input("Altyazı Font Boyutu", min_value=24, max_value=120, value=int(settings.subtitle_font_size or 58))

            blacklist_str = st.text_input("Başlık Kara Listesi (Negatif Kelimeler, virgülle ayırın)", value=settings.title_blacklist)

            st.markdown("##### 🧠 AI Başlık, Açıklama & SEO Sistem Promptu (Yatay & Dikey)")
            st.caption(
                "Yapay zekanın videolarınız için hazırladığı YouTube başlık, açıklama, etiket ve kapak kanca metinlerini belirleyen sistem talimatıdır. "
                "İçerisinde `{streamer}`, `{platform}`, `{title}`, `{transcript}`, `{format}` ve `{format_rule}` değişkenlerini kullanabilirsiniz."
            )
            from app.services.clip_ai_seo import DEFAULT_AI_SEO_PROMPT_TEMPLATE
            curr_prompt = getattr(settings, "ai_seo_system_prompt", "") or ""
            prompt_input = st.text_area(
                "AI Sistem Prompt Şablonu",
                value=curr_prompt if curr_prompt.strip() else DEFAULT_AI_SEO_PROMPT_TEMPLATE,
                height=220,
                help="Boş bırakırsanız veya varsayılanı kullanırsanız sistemin önerilen viral YouTube promptu geçerli olur."
            )

            c_btn_save, c_btn_clean = st.columns([2.2, 1.3])
            with c_btn_save:
                save_btn = st.form_submit_button("💾 Tüm Otopilot Ayarlarını Kaydet", width="stretch", type="primary")
            with c_btn_clean:
                clean_btn = st.form_submit_button("🧹 Eski Keşif Kuyruğunu Temizle", width="stretch")

            if clean_btn:
                cleaned_count = clip_db.clean_stale_discovered_clips(
                    max_age_days=int(max_age_val),
                    enable_viral_hunter=bool(viral_hunter_toggle),
                    viral_threshold=int(viral_baraj_val)
                )
                st.success(f"🧹 {cleaned_count} adet eski keşfedilmiş klip kuyruktan temizlendi!")
                st.rerun()

            if save_btn:
                # Pipeline ayarlarını kaydet
                settings.shorts_source_mode = shorts_source_select
                settings.video_layout = layout_select
                settings.facecam_position = facecam_select
                settings.skip_subtitles_if_detected = skip_sub_toggle
                settings.target_stock_horizontal = int(target_stock_h)
                settings.target_stock_shorts = int(target_stock_s)
                settings.daily_horizontal_limit = int(daily_limit_h)
                settings.daily_shorts_limit = int(daily_limit_s)
                settings.stock_max_ready_per_format = int(stock_max_val)
                settings.prime_time_slots = prime_slots_input.strip()
                settings.enforce_prime_time = bool(enforce_prime_toggle)
                settings.publish_window_start_hour = int(win_start)
                settings.publish_window_end_hour = int(win_end)
                settings.publish_interval_minutes = int(interval_min)
                settings.auto_upload = auto_upload_toggle
                settings.privacy_status = privacy_opt
                settings.auto_generate_thumbnail = auto_thumb_toggle
                settings.youtube_category_id = cat_select
                settings.made_for_kids = False  # Para kazanma ve yorumlar için daima False
                settings.age_restricted = False # Maksimum keşfet erişimi için daima False
                settings.intro_trim_seconds = float(intro_trim_val)
                settings.outro_trim_seconds = float(outro_trim_val)
                settings.subtitle_enabled = sub_toggle
                settings.subtitle_font = sub_font_val.strip() or "Arial"
                settings.subtitle_font_size = int(sub_size_val)
                settings.hook_overlay_enabled = hook_toggle
                settings.llm_moment_detection = llm_moment_toggle
                settings.ai_title_generation = ai_title_toggle
                settings.enable_viral_hunter = bool(viral_hunter_toggle)
                settings.max_video_age_days = int(max_age_val)
                settings.viral_threshold_for_older = int(viral_baraj_val)
                settings.title_blacklist = blacklist_str.strip()
                settings.youtube_search_enabled = yt_search_toggle
                settings.filter_horizontal_reactions_only = bool(reaction_only_toggle)
                settings.download_rate_limit_mb = float(dl_limit_val)
                settings.pre_upload_as_draft = bool(pre_upload_toggle)
                settings.has_paid_promotion = bool(paid_promo_toggle)
                settings.contains_synthetic_media = bool(synthetic_media_toggle)
                saved_prompt = prompt_input.strip()
                if saved_prompt == DEFAULT_AI_SEO_PROMPT_TEMPLATE.strip():
                    settings.ai_seo_system_prompt = ""
                else:
                    settings.ai_seo_system_prompt = saved_prompt

                clip_db.save_settings(settings)
                clip_publisher.update_scheduled_times_in_db(settings)
                st.success("Tüm otopilot ve AI ayarları başarıyla kaydedildi!")
                st.rerun()

    # F. YAYINCI LİSTESİ
    with tab_streamers:
        with st.expander("➕ Yeni Yayıncı / Kesit Kanalı Ekle", expanded=False):
            with st.form("add_streamer_form", clear_on_submit=True):
                c_u1, c_u2 = st.columns([2.5, 1.5])
                with c_u1:
                    s_url = st.text_input(
                        "Kanal / Yayıncı Adresi (URL)",
                        placeholder="https://youtube.com/@rraenekesitleri · https://twitch.tv/kanal"
                    )
                with c_u2:
                    s_queries = st.text_input(
                        "Arama Terimleri (Virgülle)",
                        placeholder="rraenee kesitleri, komik anlar"
                    )

                c_sf1, c_sf2, c_sbtn = st.columns([1, 1, 1.2])
                with c_sf1:
                    w_shorts = st.checkbox("📱 Shorts Üret", value=True)
                with c_sf2:
                    w_horiz = st.checkbox("🖥️ Yatay Üret", value=True)
                with c_sbtn:
                    submit_s = st.form_submit_button("➕ Kaydet", width="stretch")

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
                    c_info, c_act = st.columns([4, 1])
                    with c_info:
                        f_badges = " ".join(FORMAT_LABELS.get(f, f.value) for f in s.formats)
                        st.markdown(f"**{PLATFORM_LABELS.get(s.platform, s.platform.value)}** `{s.channel_name}` · {f_badges}")
                        if s.fan_channel_queries:
                            st.caption(f"Kesit aramaları: `{', '.join(s.fan_channel_queries)}`")
                    with c_act:
                        if st.button("🗑️ Sil", key=f"del_streamer_{s.id}", width="stretch"):
                            clip_db.delete_streamer(s.id)
                            st.rerun()
        else:
            st.info("Kayıtlı yayıncı bulunmuyor.")
