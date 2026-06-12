"""
Prosódia — Timeline do Áudio.

Exibe:
1. VAD Timeline (segmentos de fala — Gantt horizontal)
2. Features acústicas por segmento (F0, loudness, emoções)
3. Marcadores de transcrição (turnos por locutor ao longo do tempo)
"""

import io
import streamlit as st
import pandas as pd

from utils.prosodia_db import init_db, get_audio, get_project
from utils.prosodia_loader import load_prosodia_from_uploads, _session_id_from_name
from utils.prosodia_charts import (
    create_vad_timeline,
    create_speaker_stats,
    create_message_timeline,
    create_acoustic_timeline,
    create_transcription_markers,
)

init_db()

# ------------------------------------------------------------------
# Carregar áudio do banco
# ------------------------------------------------------------------
audio_id = st.session_state.get("pros_audio_id")
project_id = st.session_state.get("pros_project_id")

if not audio_id:
    st.warning("Nenhuma entrevista selecionada.")
    if st.button("← Entrevistas"):
        st.switch_page("modules/prosodia/entrevistas.py")
    st.stop()

audio = get_audio(audio_id)
if not audio:
    st.error("Entrevista não encontrada no banco.")
    if st.button("← Entrevistas"):
        st.switch_page("modules/prosodia/entrevistas.py")
    st.stop()

project = get_project(project_id) if project_id else {}
sid = audio["session_id"]

focus = st.session_state.get("pros_timeline_focus")
focus_active = bool(
    focus
    and focus.get("audio_id") == audio_id
    and (focus.get("seconds") is not None or focus.get("timestamp"))
)

# Header
h1, h2 = st.columns([6, 1])
with h1:
    st.title(f"📊 Timeline — {sid}")
    if project:
        st.caption(f"Projeto: {project.get('name', '')}")
with h2:
    st.write("")
    if st.button("← Entrevistas", width='stretch'):
        st.switch_page("modules/prosodia/entrevistas.py")

if focus_active:
    focus_question = str(focus.get("question", ""))
    focus_source = str(focus.get("source", ""))
    focus_seconds = focus.get("seconds")
    focus_ts = str(focus.get("timestamp", ""))
    focus_speaker = str(focus.get("speaker", ""))
    where_txt = f"{focus_seconds:.1f}s" if isinstance(focus_seconds, (int, float)) else (focus_ts or "tempo não informado")

    st.info(
        "🎯 Momento localizado na transcrição"
        f"\n\nPergunta: {focus_question}"
        f"\nFonte usada: {focus_source}"
        f"\nMomento: {where_txt}"
        + (f"\nLocutor: {focus_speaker}" if focus_speaker else "")
    )

# ------------------------------------------------------------------
# Reconstruir DataFrames a partir dos bytes salvos no banco
# ------------------------------------------------------------------
@st.cache_data(show_spinner="Carregando dados do áudio…")
def _load_audio_data(a_id: int, _audio_row: dict) -> dict:
    """Parse os bytes salvos no banco e retorna DataFrames."""
    json_bytes = _audio_row.get("prosodia_json")
    csv_bytes = _audio_row.get("transcricao_csv")
    sinc_bytes = _audio_row.get("sincronizado_csv")

    import io as _io

    class _BytesFile:
        """Wrapper que imita um UploadedFile para o loader existente."""
        def __init__(self, data: bytes, name: str):
            self._buf = _io.BytesIO(data)
            self.name = name

        def read(self):
            return self._buf.read()

        def seek(self, pos):
            return self._buf.seek(pos)

    session_id = _audio_row["session_id"]

    json_files = [_BytesFile(json_bytes, f"Prosodia-{session_id}.json")] if json_bytes else []
    csv_files = [_BytesFile(csv_bytes, f"Transcricao-{session_id}.csv")] if csv_bytes else []
    sinc_files = [_BytesFile(sinc_bytes, f"Sincronizado-{session_id}.csv")] if sinc_bytes else []

    return load_prosodia_from_uploads(
        json_files=json_files,
        csv_files=csv_files,
        sincronizado_files=sinc_files,
    )


data = _load_audio_data(audio_id, audio)

vad_df: pd.DataFrame = data.get("vad", pd.DataFrame())
tr_df: pd.DataFrame = data.get("transcricao", pd.DataFrame())

# Extrair dados sincronizados (features acústicas) se disponíveis
sinc_df = pd.DataFrame()
if audio.get("sincronizado_csv"):
    try:
        sinc_df = pd.read_csv(io.BytesIO(audio["sincronizado_csv"]))
    except Exception:
        pass

# Mostrar erros de parse
for err in data.get("_errors", []):
    st.warning(err)

# ------------------------------------------------------------------
# Sidebar
# ------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Controles")

    # Filtro de locutores
    all_speakers = []
    if not tr_df.empty and "SpeakerName" in tr_df.columns:
        all_speakers = sorted(tr_df["SpeakerName"].dropna().unique().tolist())

    selected_speakers = all_speakers
    if all_speakers:
        selected_speakers = st.multiselect(
            "Locutores",
            options=all_speakers,
            default=all_speakers,
            key="tl_speakers",
        )

    st.divider()

    # Seleção de features acústicas
    ACOUSTIC_COLS = [
        "f0_media", "f0_variacao", "loudness_media", "loudness_variacao",
        "speaking_rate", "intonation_score",
        "emocao_angry", "emocao_happy", "emocao_neutral", "emocao_sad",
        "dim_arousal", "dim_dominance", "dim_valence",
    ]

    acoustic_available = [c for c in ACOUSTIC_COLS if not sinc_df.empty and c in sinc_df.columns]
    selected_acoustic = []

    if acoustic_available:
        st.subheader("Features Acústicas")
        default_acoustic = [c for c in ["f0_media", "loudness_media", "emocao_happy", "emocao_neutral"]
                            if c in acoustic_available]
        selected_acoustic = st.multiselect(
            "Indicadores",
            options=acoustic_available,
            default=default_acoustic,
            key="tl_acoustic",
        )

# ------------------------------------------------------------------
# Filtrar por locutores selecionados
# ------------------------------------------------------------------
tr_filtered = tr_df.copy()
if selected_speakers and not tr_filtered.empty and "SpeakerName" in tr_filtered.columns:
    tr_filtered = tr_filtered[tr_filtered["SpeakerName"].isin(selected_speakers)]

# ------------------------------------------------------------------
# Chart 1: VAD Timeline
# ------------------------------------------------------------------
st.subheader("🎙️ Timeline VAD — Segmentos de Fala")

if not vad_df.empty:
    fig_vad = create_vad_timeline(vad_df, session_id=sid, title=f"VAD — {sid}")
    st.plotly_chart(fig_vad, width='stretch')

    m1, m2, m3 = st.columns(3)
    m1.metric("Segmentos VAD", len(vad_df))
    m2.metric("Fala total (s)", f"{vad_df['duration'].sum():.1f}")
    if "end" in vad_df.columns and "start" in vad_df.columns:
        total_dur = vad_df["end"].max() - vad_df["start"].min()
        speech = vad_df["duration"].sum()
        m3.metric("% de fala", f"{100*speech/max(total_dur,1):.0f}%")
else:
    st.info("Dados VAD não disponíveis para este áudio.")

# ------------------------------------------------------------------
# Chart 2: Features acústicas por segmento
# ------------------------------------------------------------------
if selected_acoustic and not sinc_df.empty:
    st.divider()
    st.subheader("📈 Features Acústicas por Segmento")

    fig_acoustic = create_acoustic_timeline(
        sinc_df, session_id=sid, indicators=selected_acoustic
    )
    st.plotly_chart(fig_acoustic, width='stretch')

elif acoustic_available and not selected_acoustic:
    st.info("Selecione features acústicas na barra lateral para visualizar.")

# ------------------------------------------------------------------
# Chart 3: Marcadores de transcrição / turnos
# ------------------------------------------------------------------
st.divider()
st.subheader("💬 Marcadores de Transcrição — Turnos por Locutor")

if not tr_filtered.empty and "seconds" in tr_filtered.columns:
    fig_tr = create_transcription_markers(
        tr_filtered, session_id=sid, speakers=selected_speakers or None
    )

    if focus_active and isinstance(focus.get("seconds"), (int, float)):
        focus_seconds = float(focus["seconds"])
        fig_tr.add_vline(
            x=focus_seconds,
            line_width=2,
            line_dash="dash",
            line_color="#FFD166",
            annotation_text="Pergunta",
            annotation_position="top",
        )

    st.plotly_chart(fig_tr, width='stretch')

    # Participação por locutor
    with st.expander("📊 Participação por Locutor"):
        fig_stats = create_speaker_stats(tr_filtered, session_id=sid)
        st.plotly_chart(fig_stats, width='stretch')

    # Preview transcrição
    with st.expander("📋 Transcrição completa"):
        preview_df = tr_filtered.copy().reset_index(drop=True)
        if focus_active:
            preview_df["🎯"] = ""
            nearest_idx = None

            if "seconds" in preview_df.columns and isinstance(focus.get("seconds"), (int, float)):
                sec_series = pd.to_numeric(preview_df["seconds"], errors="coerce")
                if sec_series.notna().any():
                    nearest_idx = (sec_series - float(focus["seconds"])).abs().idxmin()

            if nearest_idx is None and "Timestamp" in preview_df.columns and focus.get("timestamp"):
                match_idx = preview_df.index[preview_df["Timestamp"].astype(str) == str(focus.get("timestamp"))]
                if len(match_idx) > 0:
                    nearest_idx = int(match_idx[0])

            if nearest_idx is not None:
                preview_df.loc[nearest_idx, "🎯"] = "🎯"

        cols_show = [c for c in ["🎯", "SpeakerName", "Timestamp", "seconds", "word_count", "Text"]
                     if c in preview_df.columns]
        st.dataframe(
            preview_df[cols_show],
            width='stretch',
            height=400,
        )
else:
    st.info("Dados de transcrição não disponíveis para este áudio.")
