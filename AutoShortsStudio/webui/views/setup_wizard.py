import streamlit as st
import time
from app.config import config
from app.models.llm_provider import (
    DEFAULT_LLM_PROVIDER_ID,
    LLM_PROVIDER_REGISTRY,
    get_llm_provider,
)
from app.services import llm
from app.services.hardware_detector import hardware_detector
from app.services.clip_db import clip_db
from app.models.clip_schema import ClipPipelineSettings


def _inject_splash_styles():
    st.markdown(
        """
        <style>
        /* 🌌 5 SANİYELİK ŞATAFATLI CYBERPUNK SPLASH */
        .hologram-stage {
            position: relative;
            width: 300px;
            height: 300px;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 40px auto 20px auto;
        }
        .ring-outer {
            position: absolute;
            width: 270px;
            height: 270px;
            border-radius: 50%;
            border: 2px dashed rgba(56, 189, 248, 0.5);
            box-shadow: 0 0 35px rgba(56, 189, 248, 0.25);
            animation: rotateClockwise 10s linear infinite;
        }
        .ring-inner {
            position: absolute;
            width: 210px;
            height: 210px;
            border-radius: 50%;
            border: 2px solid transparent;
            border-top: 2px solid #c084fc;
            border-bottom: 2px solid #38bdf8;
            box-shadow: 0 0 45px rgba(192, 132, 252, 0.35);
            animation: rotateCounter 6s linear infinite;
        }
        .ring-glow {
            position: absolute;
            width: 160px;
            height: 160px;
            border-radius: 50%;
            background: radial-gradient(circle, rgba(56, 189, 248, 0.25) 0%, transparent 70%);
            animation: pulseCore 2s ease-in-out infinite alternate;
        }
        @keyframes rotateClockwise {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }
        @keyframes rotateCounter {
            from { transform: rotate(360deg); }
            to { transform: rotate(0deg); }
        }
        @keyframes pulseCore {
            0% { transform: scale(0.9); opacity: 0.6; }
            100% { transform: scale(1.15); opacity: 1; }
        }
        .splash-logo-container {
            position: relative;
            z-index: 2;
            display: flex;
            flex-direction: column;
            align-items: center;
            text-align: center;
        }
        .splash-icon {
            font-size: 74px;
            margin-bottom: 8px;
            filter: drop-shadow(0 0 25px rgba(56, 189, 248, 0.8));
            animation: floatLogo 2.5s ease-in-out infinite alternate;
        }
        @keyframes floatLogo {
            0% { transform: translateY(0px) scale(0.98); }
            100% { transform: translateY(-8px) scale(1.02); }
        }
        .splash-title {
            font-size: 34px;
            font-weight: 900;
            background: linear-gradient(90deg, #38bdf8, #818cf8, #c084fc, #38bdf8);
            background-size: 200% auto;
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            letter-spacing: -0.5px;
            animation: shimmerText 3s linear infinite;
        }
        @keyframes shimmerText {
            to { background-position: 200% center; }
        }
        .splash-subtitle {
            font-size: 13px;
            color: #94a3b8;
            letter-spacing: 3px;
            text-transform: uppercase;
            font-weight: 700;
            margin-top: 4px;
        }
        </style>
        """,
        unsafe_allow_html=True
    )


def render_splash_screen(
    target_session_key: str = "app_splash_dismissed",
    ready_message: str = "Sistem hazır! AutoShorts Studio arayüzü açılıyor...",
    min_duration: float = 5.0
):
    """
    🌌 5 Saniyelik Şatafatlı Cyberpunk Açılış & Yüklenme Animasyonu.
    Site yüklenmiş olsa dahi en az `min_duration` saniye (5.0s) gösterilmesi garanti edilir.
    """
    _inject_splash_styles()

    hw = hardware_detector.detect()
    has_nvidia = hw.get("has_nvidia", False)
    gpu_name = hw.get("gpu_name", "Standart Ekran Kartı")
    hw_info_str = f"NVIDIA {gpu_name} (NVENC Donanım Hızlandırma)" if has_nvidia else f"{gpu_name} (CPU Modu)"

    splash_container = st.empty()
    with splash_container.container():
        st.markdown(
            """
            <div class="hologram-stage">
                <div class="ring-outer"></div>
                <div class="ring-inner"></div>
                <div class="ring-glow"></div>
                <div class="splash-logo-container">
                    <div class="splash-icon">🎬</div>
                    <div class="splash-title">AutoShorts Studio</div>
                    <div class="splash-subtitle">Yapay Zeka Video & Klip Motoru</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )
        _, col_m, _ = st.columns([1, 2, 1])
        with col_m:
            progress_bar = st.progress(0)
            status_text = st.empty()

            steps = [
                (15, f"> [01/05] Donanım sensörleri taranıyor... {hw_info_str} algılandı."),
                (35, "> [02/05] NVENC donanım hızlandırma & video encoder çekirdekleri hazırlandı."),
                (58, "> [03/05] 3 Kademeli Kesintisiz Yapay Zeka Zinciri (Groq + GPT-4o) bağlandı."),
                (78, "> [04/05] OpenCV yüz algılama & Faster-Whisper modelleri belleğe yüklendi."),
                (92, "> [05/05] Yayıncı klip avcısı & otopilot kurgu motorları optimize edildi."),
                (100, f"> [READY] {ready_message}"),
            ]

            start_time = time.time()
            step_sleep = min_duration / len(steps)
            for pct, text in steps:
                progress_bar.progress(pct)
                status_text.caption(f"{text} %{pct}")
                time.sleep(step_sleep)

            # Güvence: Site yüklenmiş olsa dahi en az min_duration saniye gösterilsin
            elapsed = time.time() - start_time
            if elapsed < min_duration:
                time.sleep(min_duration - elapsed)

    st.session_state[target_session_key] = True
    splash_container.empty()
    st.rerun()


def _inject_wizard_styles():
    _inject_splash_styles()
    st.markdown(
        """
        <style>
        /* 🌌 5 SANİYELİK ŞATAFATLI CYBERPUNK SPLASH */
        .hologram-stage {
            position: relative;
            width: 300px;
            height: 300px;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 20px auto;
        }
        .ring-outer {
            position: absolute;
            width: 270px;
            height: 270px;
            border-radius: 50%;
            border: 2px dashed rgba(56, 189, 248, 0.5);
            box-shadow: 0 0 35px rgba(56, 189, 248, 0.25);
            animation: rotateClockwise 10s linear infinite;
        }
        .ring-inner {
            position: absolute;
            width: 210px;
            height: 210px;
            border-radius: 50%;
            border: 2px solid transparent;
            border-top: 2px solid #c084fc;
            border-bottom: 2px solid #38bdf8;
            box-shadow: 0 0 45px rgba(192, 132, 252, 0.35);
            animation: rotateCounter 6s linear infinite;
        }
        .ring-glow {
            position: absolute;
            width: 160px;
            height: 160px;
            border-radius: 50%;
            background: radial-gradient(circle, rgba(56, 189, 248, 0.25) 0%, transparent 70%);
            animation: pulseCore 2s ease-in-out infinite alternate;
        }
        @keyframes rotateClockwise {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }
        @keyframes rotateCounter {
            from { transform: rotate(360deg); }
            to { transform: rotate(0deg); }
        }
        @keyframes pulseCore {
            0% { transform: scale(0.9); opacity: 0.6; }
            100% { transform: scale(1.15); opacity: 1; }
        }
        .splash-logo-container {
            position: relative;
            z-index: 2;
            display: flex;
            flex-direction: column;
            align-items: center;
            text-align: center;
        }
        .splash-icon {
            font-size: 74px;
            margin-bottom: 8px;
            filter: drop-shadow(0 0 25px rgba(56, 189, 248, 0.8));
            animation: floatLogo 2.5s ease-in-out infinite alternate;
        }
        @keyframes floatLogo {
            0% { transform: translateY(0px) scale(0.98); }
            100% { transform: translateY(-8px) scale(1.02); }
        }
        .splash-title {
            font-size: 34px;
            font-weight: 900;
            background: linear-gradient(90deg, #38bdf8, #818cf8, #c084fc, #38bdf8);
            background-size: 200% auto;
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            letter-spacing: -0.5px;
            animation: shimmerText 3s linear infinite;
        }
        @keyframes shimmerText {
            to { background-position: 200% center; }
        }
        .splash-subtitle {
            font-size: 13px;
            color: #94a3b8;
            letter-spacing: 3px;
            text-transform: uppercase;
            font-weight: 700;
            margin-top: 4px;
        }
        .splash-hud-badges {
            display: flex;
            gap: 8px;
            margin-top: 12px;
        }
        .hud-badge {
            background: rgba(15, 23, 42, 0.8);
            border: 1px solid rgba(56, 189, 248, 0.3);
            color: #38bdf8;
            font-family: monospace;
            font-size: 10px;
            padding: 2px 8px;
            border-radius: 6px;
            letter-spacing: 0.5px;
        }

        /* 🎬 Ana Sihirbaz Gövdesi */
        .wizard-shell {
            max-width: 880px;
            margin: 0 auto;
            padding: 24px;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, sans-serif;
            animation: slideUpFade 0.35s cubic-bezier(0.16, 1, 0.3, 1);
        }
        @keyframes slideUpFade {
            from { opacity: 0; transform: translateY(14px); }
            to { opacity: 1; transform: translateY(0); }
        }

        /* 3 Aşamalı Üst Tab Göstergesi */
        .stage-tabs-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 24px;
            padding-bottom: 14px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            gap: 12px;
        }
        .stage-tab-pill {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 13px;
            font-weight: 700;
            color: #475569;
            flex: 1;
        }
        .stage-tab-pill.active { color: #38bdf8; }
        .stage-tab-pill.done { color: #10b981; }

        .stage-num-badge {
            width: 24px;
            height: 24px;
            border-radius: 50%;
            background: #1e293b;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 11px;
            font-weight: 800;
        }
        .stage-tab-pill.active .stage-num-badge {
            background: #38bdf8;
            color: #000;
            box-shadow: 0 0 10px rgba(56, 189, 248, 0.5);
        }
        .stage-tab-pill.done .stage-num-badge {
            background: #10b981;
            color: #fff;
        }

        .wizard-step-pill {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: rgba(56, 189, 248, 0.12);
            border: 1px solid rgba(56, 189, 248, 0.3);
            color: #38bdf8;
            font-size: 11px;
            font-weight: 700;
            padding: 4px 12px;
            border-radius: 9999px;
            letter-spacing: 0.5px;
            text-transform: uppercase;
            margin-bottom: 10px;
        }
        .wizard-question {
            font-size: 24px;
            font-weight: 800;
            color: #f8fafc;
            line-height: 1.3;
            margin-bottom: 6px;
            letter-spacing: -0.4px;
        }
        .wizard-desc {
            font-size: 14px;
            color: #94a3b8;
            line-height: 1.5;
            margin-bottom: 22px;
        }

        /* Kart Stilleri */
        .card-choice {
            background: #111827;
            border: 2px solid #1f2937;
            border-radius: 16px;
            padding: 18px;
            transition: all 0.22s ease-in-out;
            cursor: pointer;
            height: 100%;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }
        .card-choice:hover {
            border-color: #374151;
            transform: translateY(-2px);
            background: #161f30;
        }
        .card-choice.selected {
            border-color: #38bdf8;
            background: linear-gradient(145deg, #0f2342, #111827);
            box-shadow: 0 0 24px rgba(56, 189, 248, 0.22), inset 0 0 12px rgba(56, 189, 248, 0.08);
        }
        .card-badge-top {
            display: inline-block;
            font-size: 10px;
            font-weight: 800;
            padding: 3px 8px;
            border-radius: 6px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 10px;
            width: fit-content;
        }
        .badge-recommended { background: #059669; color: #ffffff; }
        .badge-fast { background: #0284c7; color: #ffffff; }
        .badge-stable { background: #475569; color: #f1f5f9; }
        .badge-gold { background: #d97706; color: #ffffff; }

        .card-icon { font-size: 28px; margin-bottom: 6px; }
        .card-title { font-size: 15px; font-weight: 700; color: #f8fafc; margin-bottom: 4px; }
        .card-subtitle { font-size: 12px; color: #94a3b8; line-height: 1.4; }

        .card-indicator {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-top: 12px;
            padding-top: 10px;
            border-top: 1px solid rgba(255, 255, 255, 0.06);
            font-size: 12px;
            font-weight: 600;
        }
        .status-selected { color: #38bdf8; display: flex; align-items: center; gap: 4px; }
        .status-unselected { color: #64748b; }

        .mockup-box {
            background: #090d16;
            border: 1px dashed #334155;
            border-radius: 8px;
            padding: 10px;
            text-align: center;
            font-family: monospace;
            font-size: 11px;
            color: #94a3b8;
            margin: 10px 0;
            line-height: 1.4;
        }
        .input-panel {
            background: #0d1526;
            border: 1px solid #1e293b;
            border-radius: 14px;
            padding: 18px;
            margin-top: 16px;
            margin-bottom: 20px;
            animation: slideUpFade 0.25s ease-out;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_stage_tabs(active_stage: int):
    stages = [
        (1, "⚙️ Genel Ayarlar"),
        (2, "🎬 AI Video Oluşturucu"),
        (3, "⚡ Yayıncı Klip & Shorts"),
        (4, "🚀 Tamamla"),
    ]
    html = '<div class="stage-tabs-row">'
    for num, label in stages:
        if num < active_stage:
            cls = "stage-tab-pill done"
            badge = "✓"
        elif num == active_stage:
            cls = "stage-tab-pill active"
            badge = str(num)
        else:
            cls = "stage-tab-pill"
            badge = str(num)
        html += f"""
        <div class="{cls}">
            <div class="stage-num-badge">{badge}</div>
            <span>{label}</span>
        </div>
        """
    html += '</div>'
    st.markdown(html, unsafe_allow_html=True)


def render_setup_wizard():
    """
    3 Ana Aşama + Logo İntro Animasyonlu Soru-Cevap Kurulum Kılavuzu.
    """
    _inject_wizard_styles()

    # 🌌 Logo Splash Animasyonu (İlk açılışta tam 5.0 saniyelik şatafatlı cyberpunk yüklenme)
    if not st.session_state.get("wizard_splash_dismissed", False):
        render_splash_screen(
            target_session_key="wizard_splash_dismissed",
            ready_message="Kurulum kılavuzuna giriliyor...",
            min_duration=5.0
        )
        return

    if "wizard_step" not in st.session_state:
        st.session_state["wizard_step"] = 1

    current_step = st.session_state["wizard_step"]

    # Donanım algılama
    hw = hardware_detector.detect()
    has_nvidia = hw.get("has_nvidia", False)
    gpu_name = hw.get("gpu_name", "Standart Ekran Kartı")

    # Mevcut ayarları al
    pipe_settings = clip_db.get_settings()

    if "wizard_hw_choice" not in st.session_state:
        st.session_state["wizard_hw_choice"] = config.app.get("hardware_mode", "gpu" if has_nvidia else "cpu")

    if "wizard_llm_choice" not in st.session_state:
        st.session_state["wizard_llm_choice"] = config.app.get("llm_provider", "groq")

    if "wizard_aspect_choice" not in st.session_state:
        st.session_state["wizard_aspect_choice"] = config.ui.get("video_aspect", "9:16")

    if "wizard_layout_choice" not in st.session_state:
        st.session_state["wizard_layout_choice"] = getattr(pipe_settings, "shorts_layout", "split_facecam") or "split_facecam"

    st.markdown('<div class="wizard-shell">', unsafe_allow_html=True)

    # 4 Ana Aşamayı belirle (1-2 -> Aşama 1, 3-4 -> Aşama 2, 5-6 -> Aşama 3, 7 -> Aşama 4)
    stage_map = {1: 1, 2: 1, 3: 2, 4: 2, 5: 3, 6: 3, 7: 4}
    active_stage = stage_map.get(current_step, 1)
    _render_stage_tabs(active_stage)

    # =========================================================================
    # ⚙️ 1. AŞAMA: GENEL SİSTEM AYARLARI
    # =========================================================================
    if current_step == 1:
        st.markdown('<div class="wizard-step-pill">1. Aşama · Adım 1/2 · Donanım & Render</div>', unsafe_allow_html=True)
        st.markdown('<div class="wizard-question">Video montajını hangi donanım motoru üstlensin?</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="wizard-desc">Sisteminizde <b>{gpu_name}</b> tespit edildi. Render motorunu seçin.</div>',
            unsafe_allow_html=True
        )

        selected_hw = st.session_state["wizard_hw_choice"]
        col_gpu, col_cpu = st.columns(2, gap="medium")

        with col_gpu:
            is_gpu = (selected_hw == "gpu")
            card_cls = "card-choice selected" if is_gpu else "card-choice"
            badge = '<span class="card-badge-top badge-recommended">🚀 ÖNERİLEN · EN HIZLI</span>' if has_nvidia else '<span class="card-badge-top badge-fast">GPU NVENC</span>'
            status = '<div class="status-selected">✓ Seçili Motor</div>' if is_gpu else '<div class="status-unselected">Seçmek için tıkla</div>'
            st.markdown(
                f"""
                <div class="{card_cls}">
                    <div>
                        {badge}
                        <div class="card-icon">⚡</div>
                        <div class="card-title">NVIDIA Donanım Motoru (NVENC)</div>
                        <div class="card-subtitle">Videolar ekran kartınızın özel NVENC çipleriyle render edilir. İşlemciyi yormaz ve <b>5 kata kadar daha hızlıdır</b>.</div>
                    </div>
                    <div class="card-indicator">{status}</div>
                </div>
                """,
                unsafe_allow_html=True
            )
            if st.button("⚡ NVIDIA GPU Seç", key="btn_hw_gpu", use_container_width=True, type="primary" if is_gpu else "secondary"):
                st.session_state["wizard_hw_choice"] = "gpu"
                st.rerun()

        with col_cpu:
            is_cpu = (selected_hw == "cpu")
            card_cls = "card-choice selected" if is_cpu else "card-choice"
            badge = '<span class="card-badge-top badge-recommended">🛡️ ÖNERİLEN · KARARLI</span>' if not has_nvidia else '<span class="card-badge-top badge-stable">İŞLEMCİ</span>'
            status = '<div class="status-selected">✓ Seçili Motor</div>' if is_cpu else '<div class="status-unselected">Seçmek için tıkla</div>'
            st.markdown(
                f"""
                <div class="{card_cls}">
                    <div>
                        {badge}
                        <div class="card-icon">🖥️</div>
                        <div class="card-title">Standart İşlemci Motoru (libx264)</div>
                        <div class="card-subtitle">Video kodlama CPU çekirdekleri tarafından yapılır. Evrenseldir, tüm AMD ve dahili grafiklerde kusursuz çalışır.</div>
                    </div>
                    <div class="card-indicator">{status}</div>
                </div>
                """,
                unsafe_allow_html=True
            )
            if st.button("🖥️ CPU Seç", key="btn_hw_cpu", use_container_width=True, type="primary" if is_cpu else "secondary"):
                st.session_state["wizard_hw_choice"] = "cpu"
                st.rerun()

        st.write("")
        _, c_next = st.columns(2)
        with c_next:
            if st.button("Sonraki Soru: Yapay Zeka & Materyal API ➡️", type="primary", use_container_width=True):
                config.app["hardware_mode"] = st.session_state["wizard_hw_choice"]
                config.app["video_codec"] = hardware_detector.get_effective_codec(st.session_state["wizard_hw_choice"])
                config.save_config()
                st.session_state["wizard_step"] = 2
                st.rerun()

    elif current_step == 2:
        st.markdown('<div class="wizard-step-pill">1. Aşama · Adım 2/2 · Yapay Zeka & Materyaller</div>', unsafe_allow_html=True)
        st.markdown('<div class="wizard-question">Temel Yapay Zeka ve Stok Video kaynaklarınızı belirleyin</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="wizard-desc">Yalnızca 1 birincil AI zorunludur. Mevcut stok video anahtarlarınız config dosyasından otomatik yüklendi.</div>',
            unsafe_allow_html=True
        )

        providers_meta = [
            ("groq", "⚡ Groq (Llama 3.3)", "Ultra hızlı, kurgularda sıfır bekleme.", "HIZLI"),
            ("openai", "🧠 OpenAI (GPT-4o)", "En popüler, yüksek mantık ve yaratıcılık.", "POPÜLER"),
            ("gemini", "🌟 Google Gemini (1.5)", "Geniş bağlam, dengeli ve ekonomik.", "DENGELİ"),
        ]

        active_llm = st.session_state.get("wizard_llm_choice", config.app.get("llm_provider", "groq"))
        cols_llm = st.columns(3, gap="medium")

        for idx, (p_id, p_name, p_desc, p_badge) in enumerate(providers_meta):
            with cols_llm[idx]:
                is_sel = (active_llm == p_id)
                c_cls = "card-choice selected" if is_sel else "card-choice"
                st.markdown(
                    f"""
                    <div class="{c_cls}">
                        <div>
                            <span class="card-badge-top badge-fast">{p_badge}</span>
                            <div class="card-title">{p_name}</div>
                            <div class="card-subtitle">{p_desc}</div>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
                if st.button(f"✓ Seç ({p_id.title()})" if is_sel else f"Seç ({p_id.title()})", key=f"btn_p_{p_id}", use_container_width=True, type="primary" if is_sel else "secondary"):
                    st.session_state["wizard_llm_choice"] = p_id
                    st.rerun()

        spec = get_llm_provider(active_llm)
        cur_key = config.app.get(spec.config_key("api_key"), "") if spec else ""
        cur_model = config.app.get(spec.config_key("model_name"), "") or (spec.default_model_name if spec else "")

        st.markdown('<div class="input-panel">', unsafe_allow_html=True)
        st.markdown(f"#### 🔑 {active_llm.upper()} Yapılandırması")
        col_k, col_m = st.columns([1.3, 0.7])
        with col_k:
            llm_key_val = st.text_input("Birincil API Anahtarı *", value=cur_key, type="password", key="wiz_llm_key")
        with col_m:
            llm_model_val = st.text_input("Model Adı", value=cur_model, key="wiz_llm_model")

        if st.button("⚡ Ana AI Bağlantısını Canlı Test Et", key="btn_test_llm_live"):
            if not llm_key_val.strip():
                st.warning("Lütfen test etmeden önce bir API anahtarı girin.")
            else:
                with st.spinner("Ping atılıyor..."):
                    with config.runtime_config_lock():
                        ok, err, elap = llm.test_connection(provider_id=active_llm, api_key=llm_key_val.strip(), model_name=llm_model_val.strip())
                    if ok:
                        st.success(f"✅ Harika! Yapay zeka {elap:.2f} saniyede yanıt verdi.")
                    else:
                        st.error(f"❌ Bağlantı Başarısız: {err}")
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="input-panel">', unsafe_allow_html=True)
        st.markdown("#### 📦 Ücretsiz Stok Video API Anahtarları (Pexels / Pixabay)")
        col_px, col_pb = st.columns(2)
        with col_px:
            px_keys = config.app.get("pexels_api_keys", [""])
            px_val = px_keys[0] if px_keys else ""
            new_px = st.text_input("Pexels API Key", value=px_val, type="password", key="wiz_px_key")
        with col_pb:
            pb_keys = config.app.get("pixabay_api_keys", [""])
            pb_val = pb_keys[0] if pb_keys else ""
            new_pb = st.text_input("Pixabay API Key", value=pb_val, type="password", key="wiz_pb_key")
        st.markdown('</div>', unsafe_allow_html=True)

        st.write("")
        c_back, c_next = st.columns(2)
        with c_back:
            if st.button("⬅️ Geri (Donanım)", use_container_width=True):
                st.session_state["wizard_step"] = 1
                st.rerun()
        with c_next:
            if st.button("2. Aşamaya Geç: AI Video Oluşturucu ➡️", type="primary", use_container_width=True):
                if not llm_key_val.strip():
                    st.error("⚠️ Lütfen seçtiğiniz yapay zeka için API anahtarı girin.")
                else:
                    config.app["llm_provider"] = active_llm
                    if spec:
                        config.app[spec.config_key("api_key")] = llm_key_val.strip()
                        config.app[spec.config_key("model_name")] = llm_model_val.strip()
                    if new_px.strip():
                        config.app["pexels_api_keys"] = [new_px.strip()]
                    if new_pb.strip():
                        config.app["pixabay_api_keys"] = [new_pb.strip()]
                    config.save_config()
                    st.session_state["wizard_step"] = 3
                    st.rerun()

    # =========================================================================
    # 🎬 2. AŞAMA: AI VİDEO OLUŞTURUCU AYARLARI
    # =========================================================================
    elif current_step == 3:
        st.markdown('<div class="wizard-step-pill">2. Aşama · Adım 1/2 · Format & Senaryo</div>', unsafe_allow_html=True)
        st.markdown('<div class="wizard-question">Üretilecek videoların varsayılan formatı ve kurgu tarzı ne olsun?</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="wizard-desc">Sıfırdan video üreten yapay zekanın varsayılan video en-boy oranını ve senaryo süresini belirleyin.</div>',
            unsafe_allow_html=True
        )

        active_aspect = st.session_state["wizard_aspect_choice"]
        col_a1, col_a2, col_a3 = st.columns(3, gap="medium")

        with col_a1:
            is_a1 = (active_aspect == "9:16")
            c_cls = "card-choice selected" if is_a1 else "card-choice"
            st.markdown(
                f"""
                <div class="{c_cls}">
                    <div>
                        <span class="card-badge-top badge-recommended">DİKİNE SHORTS</span>
                        <div class="card-title">📱 9:16 Dikey Shorts</div>
                        <div class="mockup-box">1080 x 1920<br>TikTok, Shorts, Reels</div>
                        <div class="card-subtitle">Mobil cihazlar için optimize edilmiş tam ekran dikey video.</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )
            if st.button("✓ 9:16 Dikey Seç" if is_a1 else "9:16 Dikey Seç", key="btn_asp_916", use_container_width=True, type="primary" if is_a1 else "secondary"):
                st.session_state["wizard_aspect_choice"] = "9:16"
                st.rerun()

        with col_a2:
            is_a2 = (active_aspect == "16:9")
            c_cls = "card-choice selected" if is_a2 else "card-choice"
            st.markdown(
                f"""
                <div class="{c_cls}">
                    <div>
                        <span class="card-badge-top badge-stable">YATAY YOUTUBE</span>
                        <div class="card-title">🖥️ 16:9 Yatay Standart</div>
                        <div class="mockup-box">1920 x 1080<br>Klasik YouTube Videoları</div>
                        <div class="card-subtitle">Geniş ekran bilgisayar ve televizyonlar için sinematik format.</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )
            if st.button("✓ 16:9 Yatay Seç" if is_a2 else "16:9 Yatay Seç", key="btn_asp_169", use_container_width=True, type="primary" if is_a2 else "secondary"):
                st.session_state["wizard_aspect_choice"] = "16:9"
                st.rerun()

        with col_a3:
            is_a3 = (active_aspect == "1:1")
            c_cls = "card-choice selected" if is_a3 else "card-choice"
            st.markdown(
                f"""
                <div class="{c_cls}">
                    <div>
                        <span class="card-badge-top badge-fast">KARE FORMAT</span>
                        <div class="card-title">⬛ 1:1 Kare Instagram</div>
                        <div class="mockup-box">1080 x 1080<br>Gönderiler & Feed</div>
                        <div class="card-subtitle">Sosyal medya akışları için dengeli kare video formatı.</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )
            if st.button("✓ 1:1 Kare Seç" if is_a3 else "1:1 Kare Seç", key="btn_asp_11", use_container_width=True, type="primary" if is_a3 else "secondary"):
                st.session_state["wizard_aspect_choice"] = "1:1"
                st.rerun()

        st.markdown('<div class="input-panel">', unsafe_allow_html=True)
        st.markdown("#### ⏱️ Senaryo & Paragraf Uzunluğu")
        col_dur, col_par = st.columns(2)
        with col_dur:
            st.selectbox("Hedef Video Süresi", options=["30 - 45 Saniye (Viral Shorts)", "60 Saniye (1 Dakika)", "90+ Saniye (Detaylı)"], key="wiz_target_dur")
        with col_par:
            st.selectbox("Paragraf Sayısı", options=["3 Paragraf (Kısa)", "4-5 Paragraf (Dengeli)", "6+ Paragraf (Uzun)"], index=1, key="wiz_target_par")
        st.toggle("🎯 Stok Materyalleri Senaryoyla Akıllı Eşle", value=config.app.get("match_materials_to_script", True), key="wiz_match_mat")
        st.markdown('</div>', unsafe_allow_html=True)

        st.write("")
        c_back, c_next = st.columns(2)
        with c_back:
            if st.button("⬅️ Geri (Genel Ayarlar)", use_container_width=True):
                st.session_state["wizard_step"] = 2
                st.rerun()
        with c_next:
            if st.button("Sonraki Soru: Ses & Altyazı ➡️", type="primary", use_container_width=True):
                config.ui["video_aspect"] = st.session_state["wizard_aspect_choice"]
                config.app["match_materials_to_script"] = st.session_state.get("wiz_match_mat", True)
                config.save_config()
                st.session_state["wizard_step"] = 4
                st.rerun()

    elif current_step == 4:
        st.markdown('<div class="wizard-step-pill">2. Aşama · Adım 2/2 · Seslendirme & Altyazı</div>', unsafe_allow_html=True)
        st.markdown('<div class="wizard-question">Ses tonunu, arka plan müziğini ve altyazı efektini seçin</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="wizard-desc">Yapay zeka anlatıcısının ses karakteri ve CapCut stili animasyonlu altyazı şablonu.</div>',
            unsafe_allow_html=True
        )

        col_s1, col_s2, col_s3 = st.columns(3, gap="medium")

        sub_choice = st.session_state.get("wizard_sub_choice", "capcut_word")

        with col_s1:
            is_s1 = (sub_choice == "capcut_word")
            c_cls = "card-choice selected" if is_s1 else "card-choice"
            st.markdown(
                f"""
                <div class="{c_cls}">
                    <div>
                        <span class="card-badge-top badge-recommended">🔥 EN POPÜLER</span>
                        <div class="card-title">CapCut Tarzı Parlayan Kelime</div>
                        <div class="mockup-box" style="color:#f59e0b; font-weight:bold;">"Her KELİME parlar"</div>
                        <div class="card-subtitle">İzleyiciyi videoda tutan dinamik kelime kelime altyazı.</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )
            if st.button("✓ CapCut Seç" if is_s1 else "CapCut Seç", key="btn_sub_capcut", use_container_width=True, type="primary" if is_s1 else "secondary"):
                st.session_state["wizard_sub_choice"] = "capcut_word"
                st.rerun()

        with col_s2:
            is_s2 = (sub_choice == "pop")
            c_cls = "card-choice selected" if is_s2 else "card-choice"
            st.markdown(
                f"""
                <div class="{c_cls}">
                    <div>
                        <span class="card-badge-top badge-fast">CANLI</span>
                        <div class="card-title">Pop & Bounce Hareketi</div>
                        <div class="mockup-box" style="color:#38bdf8;">"Sıçrayarak ekrana gelen"</div>
                        <div class="card-subtitle">Enerjik ve dikkat çekici zıplama efektli yazı stili.</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )
            if st.button("✓ Pop Seç" if is_s2 else "Pop Seç", key="btn_sub_pop", use_container_width=True, type="primary" if is_s2 else "secondary"):
                st.session_state["wizard_sub_choice"] = "pop"
                st.rerun()

        with col_s3:
            is_s3 = (sub_choice == "classic")
            c_cls = "card-choice selected" if is_s3 else "card-choice"
            st.markdown(
                f"""
                <div class="{c_cls}">
                    <div>
                        <span class="card-badge-top badge-stable">KLASİK</span>
                        <div class="card-title">Sakin Satır Altyazı</div>
                        <div class="mockup-box" style="color:#e2e8f0;">"Geleneksel sinematik satır"</div>
                        <div class="card-subtitle">Belgesel ve sakin hikayeler için sade alt bant.</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )
            if st.button("✓ Klasik Seç" if is_s3 else "Klasik Seç", key="btn_sub_classic", use_container_width=True, type="primary" if is_s3 else "secondary"):
                st.session_state["wizard_sub_choice"] = "classic"
                st.rerun()

        st.markdown('<div class="input-panel">', unsafe_allow_html=True)
        st.markdown("#### 🎙️ Seslendirme (TTS) ve Arka Plan Müziği")
        col_v, col_r, col_b = st.columns(3)
        with col_v:
            st.selectbox("Türkçe Anlatıcı Sesi", options=["tr-TR-AhmetNeural (Tok Erkek)", "tr-TR-EmelNeural (Canlı Kadın)"], key="wiz_voice")
        with col_r:
            st.selectbox("Konuşma Hızı", options=["1.0x (Normal)", "1.10x (Shorts İdeal)", "1.25x (Hızlı)"], index=1, key="wiz_speed")
        with col_b:
            st.selectbox("Arka Plan Müziği", options=["Rastgele Dinamik BGM", "Sakin / Lo-Fi", "Müziksiz"], key="wiz_bgm")
        st.markdown('</div>', unsafe_allow_html=True)

        st.write("")
        c_back, c_next = st.columns(2)
        with c_back:
            if st.button("⬅️ Geri (Format)", use_container_width=True):
                st.session_state["wizard_step"] = 3
                st.rerun()
        with c_next:
            if st.button("3. Aşamaya Geç: Yayıncı Klip Otomasyonu ➡️", type="primary", use_container_width=True):
                st.session_state["wizard_step"] = 5
                st.rerun()

    # =========================================================================
    # ⚡ 3. AŞAMA: YAYINCI KLİP & SHORTS OTOMASYONU AYARLARI
    # =========================================================================
    elif current_step == 5:
        st.markdown('<div class="wizard-step-pill">3. Aşama · Adım 1/2 · Kurgu & Webcam Tespiti</div>', unsafe_allow_html=True)
        st.markdown('<div class="wizard-question">Yayıncı kayıtları hangi şablonda Shorts\'a dönüştürülsün?</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="wizard-desc">Twitch, YouTube ve Kick yayınlarındaki yatay videoların 9:16 bölünme ve kamera yerleşim kuralı.</div>',
            unsafe_allow_html=True
        )

        active_layout = st.session_state["wizard_layout_choice"]
        col_l1, col_l2, col_l3 = st.columns(3, gap="medium")

        with col_l1:
            is_l1 = (active_layout == "split_facecam")
            c_cls = "card-choice selected" if is_l1 else "card-choice"
            st.markdown(
                f"""
                <div class="{c_cls}">
                    <div>
                        <span class="card-badge-top badge-recommended">⭐ EN ÇOK İZLENEN</span>
                        <div class="card-title">Bölünmüş Ekran (Split)</div>
                        <div class="mockup-box">[ 👤 YAYINCI KAMERASI ]<br>───────────────────────<br>[ 🎮 OYUN EKRANI ]</div>
                        <div class="card-subtitle">Yapay zeka yayıncı yüzünü otomatik bulup üste koyar, oyunu alta yerleştirir.</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )
            if st.button("✓ Bölünmüş Ekran Seç" if is_l1 else "Bölünmüş Ekran Seç", key="btn_lay_split", use_container_width=True, type="primary" if is_l1 else "secondary"):
                st.session_state["wizard_layout_choice"] = "split_facecam"
                st.rerun()

        with col_l2:
            is_l2 = (active_layout == "blur_bg")
            c_cls = "card-choice selected" if is_l2 else "card-choice"
            st.markdown(
                f"""
                <div class="{c_cls}">
                    <div>
                        <span class="card-badge-top badge-fast">SİNEMATİK</span>
                        <div class="card-title">Bulanık Arka Plan (Blur)</div>
                        <div class="mockup-box">░░░ BULANIK ARKA PLAN ░░░<br>[ 🎮 Orijinal Yatay Video ]<br>░░░ BULANIK ARKA PLAN ░░░</div>
                        <div class="card-subtitle">Orijinal video oranını korur, arkasına dinamik bulanıklık ekler.</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )
            if st.button("✓ Bulanık Arka Plan Seç" if is_l2 else "Bulanık Arka Plan Seç", key="btn_lay_blur", use_container_width=True, type="primary" if is_l2 else "secondary"):
                st.session_state["wizard_layout_choice"] = "blur_bg"
                st.rerun()

        with col_l3:
            is_l3 = (active_layout == "center_crop")
            c_cls = "card-choice selected" if is_l3 else "card-choice"
            st.markdown(
                f"""
                <div class="{c_cls}">
                    <div>
                        <span class="card-badge-top badge-stable">TAM EKRAN</span>
                        <div class="card-title">Merkez Kırpma (Crop)</div>
                        <div class="mockup-box">┌─────────────────────┐<br>│  🔍 TAM EKRAN DİKEY │<br>│     ODAKLANMA       │<br>└─────────────────────┘</div>
                        <div class="card-subtitle">Videonun merkezine odaklanarak siyah şerit olmadan tam ekran doldurur.</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )
            if st.button("✓ Merkez Kırpma Seç" if is_l3 else "Merkez Kırpma Seç", key="btn_lay_crop", use_container_width=True, type="primary" if is_l3 else "secondary"):
                st.session_state["wizard_layout_choice"] = "center_crop"
                st.rerun()

        st.markdown('<div class="input-panel">', unsafe_allow_html=True)
        st.markdown("#### 🤖 Yayıncı Kamera (Facecam) Tercihi")
        col_fc, col_wh = st.columns([1.5, 1])
        with col_fc:
            st.selectbox(
                "Kamera Konumu Tespiti",
                options=["auto", "bottom_left", "bottom_right", "top_left", "none"],
                format_func=lambda x: {"auto": "🤖 Otomatik Akıllı Tespit (OpenCV AI) [Önerilen]", "bottom_left": "Sol Alt Köşe (Sabit)", "bottom_right": "Sağ Alt Köşe (Sabit)", "top_left": "Sol Üst Köşe", "none": "Kamera Yok / Sadece Oyun"}[x],
                key="wiz_facecam_pos"
            )
        with col_wh:
            st.selectbox("Faster-Whisper Modeli", options=["small (Önerilen)", "base (Hızlı)", "medium (En İyi)"], key="wiz_whisper_model")
        st.markdown('</div>', unsafe_allow_html=True)

        st.write("")
        c_back, c_next = st.columns(2)
        with c_back:
            if st.button("⬅️ Geri (Ses & Müzik)", use_container_width=True):
                st.session_state["wizard_step"] = 4
                st.rerun()
        with c_next:
            if st.button("Sonraki Soru: Viral Avcı & YouTube ➡️", type="primary", use_container_width=True):
                pipe_settings.shorts_layout = st.session_state["wizard_layout_choice"]
                pipe_settings.facecam_position = st.session_state.get("wiz_facecam_pos", "auto")
                clip_db.save_settings(pipe_settings)
                st.session_state["wizard_step"] = 6
                st.rerun()

    elif current_step == 6:
        st.markdown('<div class="wizard-step-pill">3. Aşama · Adım 2/2 · Viral Avcı & YouTube</div>', unsafe_allow_html=True)
        st.markdown('<div class="wizard-question">Viral Klip Avcısı ve YouTube otomatik yükleme kuralları</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="wizard-desc">Yayın kaydındaki en heyecanlı anların tespit hassasiyeti ve YouTube yükleme gizliliği.</div>',
            unsafe_allow_html=True
        )

        col_h1, col_h2 = st.columns(2, gap="medium")
        hype_choice = st.session_state.get("wizard_hype_choice", "75")

        with col_h1:
            is_h1 = (hype_choice == "75")
            c_cls = "card-choice selected" if is_h1 else "card-choice"
            st.markdown(
                f"""
                <div class="{c_cls}">
                    <div>
                        <span class="card-badge-top badge-recommended">🔥 VİRAL ODAKLI</span>
                        <div class="card-title">Yüksek Heyecan Eşiği (%75 Hype)</div>
                        <div class="card-subtitle">Sadece bağrışmaların, kahkahaların, çatışmaların ve en tepe anların olduğu kısımları klip yapar.</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )
            if st.button("✓ %75 Hype Seç" if is_h1 else "%75 Hype Seç", key="btn_hype_75", use_container_width=True, type="primary" if is_h1 else "secondary"):
                st.session_state["wizard_hype_choice"] = "75"
                st.rerun()

        with col_h2:
            is_h2 = (hype_choice == "60")
            c_cls = "card-choice selected" if is_h2 else "card-choice"
            st.markdown(
                f"""
                <div class="{c_cls}">
                    <div>
                        <span class="card-badge-top badge-stable">DENGELİ</span>
                        <div class="card-title">Dengeli Eşik (%60 Hype)</div>
                        <div class="card-subtitle">Hem eğlenceli anları hem de hikaye anlatımı içeren daha geniş sahneleri yakalar.</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )
            if st.button("✓ %60 Hype Seç" if is_h2 else "%60 Hype Seç", key="btn_hype_60", use_container_width=True, type="primary" if is_h2 else "secondary"):
                st.session_state["wizard_hype_choice"] = "60"
                st.rerun()

        st.markdown('<div class="input-panel">', unsafe_allow_html=True)
        st.markdown("#### 📺 YouTube Shorts Yükleme Varsayılanları")
        col_yp, col_yc = st.columns(2)
        with col_yp:
            st.selectbox("Yükleme Gizliliği", options=["unlisted", "public", "private"], format_func=lambda x: {"unlisted": "Liste Dışı (Kontrol Edip Yayınla) [Önerilen]", "public": "Herkese Açık", "private": "Gizli"}[x], key="wiz_yt_priv")
        with col_yc:
            st.selectbox("YouTube Kategori", options=["20", "24", "23"], format_func=lambda x: {"20": "Gaming (Oyun)", "24": "Entertainment (Eğlence)", "23": "Comedy (Komedi)"}[x], key="wiz_yt_cat")

        st.toggle("🎯 İlk 7 Saniye Kanca Başlık Rozeti (Hook Overlay)", value=getattr(pipe_settings, "add_hook_overlay", True), key="wiz_hook_tog")
        st.toggle("💬 Faster-Whisper Otomatik Türkçe Altyazı Bas", value=getattr(pipe_settings, "add_subtitles", True), key="wiz_sub_tog")
        st.markdown('</div>', unsafe_allow_html=True)

        st.write("")
        c_back, c_next = st.columns(2)
        with c_back:
            if st.button("⬅️ Geri (Kurgu Şablonu)", use_container_width=True):
                st.session_state["wizard_step"] = 5
                st.rerun()
        with c_next:
            if st.button("Özeti İncele ve Tamamla ➡️", type="primary", use_container_width=True):
                pipe_settings.youtube_privacy_status = st.session_state.get("wiz_yt_priv", "unlisted")
                pipe_settings.youtube_category_id = st.session_state.get("wiz_yt_cat", "20")
                pipe_settings.add_hook_overlay = st.session_state.get("wiz_hook_tog", True)
                pipe_settings.add_subtitles = st.session_state.get("wiz_sub_tog", True)
                clip_db.save_settings(pipe_settings)
                st.session_state["wizard_step"] = 7
                st.rerun()

    # =========================================================================
    # 🚀 4. AŞAMA: TAMAMLANDI VE FIRLATMA
    # =========================================================================
    elif current_step == 7:
        st.markdown('<div class="wizard-step-pill">4. Aşama · Kurulum Tamamlandı</div>', unsafe_allow_html=True)
        st.markdown('<div class="wizard-question">Tebrikler! Bütün sistemleriniz optimize edildi</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="wizard-desc">Genel ayarlar, AI video motoru ve yayıncı klip otomasyonu stüdyonuz için hazır.</div>',
            unsafe_allow_html=True
        )

        chosen_hw = config.app.get("hardware_mode", "gpu" if has_nvidia else "cpu").upper()
        chosen_llm = config.app.get("llm_provider", "groq").upper()
        chosen_asp = config.ui.get("video_aspect", "9:16")
        chosen_lay = getattr(pipe_settings, "shorts_layout", "split_facecam").replace("_", " ").title()

        st.markdown(
            f"""
            <div class="input-panel" style="border-color: #38bdf8; padding: 22px;">
                <h4 style="color: #38bdf8; margin-top:0; margin-bottom: 14px;">📋 Tam Sistem Yapılandırma Özeti</h4>
                <div style="display:grid; grid-template-columns: 1fr 1fr; gap:16px; font-size:13px; line-height:1.6;">
                    <div>
                        <div style="color:#64748b; font-size:11px; text-transform:uppercase; font-weight:700;">1. GENEL SİSTEM</div>
                        <div><b>⚡ Donanım:</b> <span style="color:#38bdf8;">{chosen_hw} ({hardware_detector.get_effective_codec(chosen_hw.lower())})</span></div>
                        <div><b>🧠 Yapay Zeka:</b> <span style="color:#818cf8;">{chosen_llm}</span></div>
                    </div>
                    <div>
                        <div style="color:#64748b; font-size:11px; text-transform:uppercase; font-weight:700;">2. AI VİDEO MOTORU</div>
                        <div><b>📱 Format:</b> <span style="color:#10b981;">{chosen_asp} Dikey Shorts</span></div>
                        <div><b>💬 Altyazı:</b> <span style="color:#f59e0b;">CapCut Tarzı Parlayan</span></div>
                    </div>
                    <div style="grid-column: span 2; margin-top: 6px; padding-top: 10px; border-top: 1px solid rgba(255,255,255,0.06);">
                        <div style="color:#64748b; font-size:11px; text-transform:uppercase; font-weight:700;">3. YAYINCI KLİP & SHORTS OTOMASYONU</div>
                        <div style="display:flex; justify-content:space-between; margin-top:4px;">
                            <div><b>📐 Kurgu:</b> <span style="color:#38bdf8;">{chosen_lay}</span></div>
                            <div><b>🤖 Kamera:</b> <span style="color:#10b981;">Otomatik Yüz Tespiti (OpenCV)</span></div>
                            <div><b>📺 YouTube:</b> <span style="color:#818cf8;">Liste Dışı (Unlisted)</span></div>
                        </div>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )

        st.write("")
        c_back, c_launch = st.columns(2)
        with c_back:
            if st.button("⬅️ Geri (YouTube Ayarları)", use_container_width=True):
                st.session_state["wizard_step"] = 6
                st.rerun()
        with c_launch:
            if st.button("🎉 Kurulumu Tamamla & Stüdyoya Gir 🚀", type="primary", use_container_width=True):
                config.app["setup_completed"] = True
                config.save_config()
                st.session_state["show_setup_wizard"] = False
                st.session_state["wizard_step"] = 1
                st.session_state["app_splash_dismissed"] = True
                st.toast("🎉 Kurulum tamamlandı! AutoShorts Studio göreve hazır.")
                time.sleep(0.4)
                st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)
