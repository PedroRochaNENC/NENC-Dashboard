"""
Prosódia — Timeline do Áudio.

Exibe:
1. VAD Timeline (segmentos de fala — Gantt horizontal)
2. Features acústicas por segmento (F0, loudness, emoções)
3. Marcadores de transcrição (turnos por locutor ao longo do tempo)
"""

import io
import streamlit as st
from utils import auth

auth.require_module("prosodia")

import pandas as pd
import base64

from utils.prosodia_db import init_db, get_audio, get_project
from utils.prosodia_loader import load_prosodia_from_uploads, _session_id_from_name
from utils.organization_data import list_external_resources
from utils.prosodia_charts import (
    create_vad_timeline,
    create_speaker_stats,
    create_message_timeline,
    create_acoustic_timeline,
    create_transcription_markers,
)

init_db()

@st.cache_data(show_spinner="Carregando áudio da API...")
def _load_audio_bytes(api_audio_id: int, wa_msg_id: str = "") -> bytes:
    from utils.whatsapp_api_client import get_audio_file, is_configured
    if not is_configured():
        raise RuntimeError("WhatsApp API não está configurada.")
    return get_audio_file(api_audio_id, kind="wav")


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
    st.session_state.pop("pros_audio_id", None)
    st.session_state.pop("pros_timeline_focus", None)
    st.error("Entrevista não encontrada no banco.")
    if st.button("← Entrevistas"):
        st.switch_page("modules/prosodia/entrevistas.py")
    st.stop()

project = get_project(project_id) if project_id else None
if not project or audio.get("project_id") != project.get("id"):
    st.session_state.pop("pros_project_id", None)
    st.session_state.pop("pros_audio_id", None)
    st.session_state.pop("pros_timeline_focus", None)
    st.error("A entrevista selecionada não pertence ao projeto ativo.")
    if st.button("← Entrevistas"):
        st.switch_page("modules/prosodia/entrevistas.py")
    st.stop()
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

# ------------------------------------------------------------------
# Filtrar por locutores selecionados
# ------------------------------------------------------------------
tr_filtered = tr_df.copy()
if selected_speakers and not tr_filtered.empty and "SpeakerName" in tr_filtered.columns:
    tr_filtered = tr_filtered[tr_filtered["SpeakerName"].isin(selected_speakers)]

# ------------------------------------------------------------------
# Preparar dados para o componente HTML
# ------------------------------------------------------------------
import json

# Garantir que valores NaN/Null sejam convertidos corretamente
if not sinc_df.empty:
    sinc_data_list = sinc_df.where(pd.notnull(sinc_df), None).to_dict(orient="records")
else:
    sinc_data_list = []
sinc_json = json.dumps(sinc_data_list)

focus_seconds = 0.0
if focus_active and isinstance(focus.get("seconds"), (int, float)):
    focus_seconds = float(focus["seconds"])

# ------------------------------------------------------------------
# Layout Unificado: Tocador, Transcrição e Gráficos Sincronizados
# ------------------------------------------------------------------
if not tr_filtered.empty and "seconds" in tr_filtered.columns:
    # Obter speaker colors mapeadas para o widget
    all_speakers_list = sorted(tr_df["SpeakerName"].dropna().unique().tolist())
    SPEAKER_COLORS = ["#636EFA", "#EF553B", "#00CC96", "#AB63FA", "#FFA15A", "#19D3F3", "#FF6692", "#B6E880"]
    spk_color_map = {spk: SPEAKER_COLORS[i % len(SPEAKER_COLORS)] for i, spk in enumerate(all_speakers_list)}

    # Formatar transcrição ordenada por tempo
    tr_sorted = tr_filtered.sort_values("seconds").reset_index(drop=True)
    transcript_items = []
    for idx, row in tr_sorted.iterrows():
        spk = str(row.get("SpeakerName", "Desconhecido"))
        text = str(row.get("Text", ""))
        start_s = float(row.get("seconds", 0.0))
        
        transcript_items.append({
            "speaker": spk,
            "timestamp": str(row.get("Timestamp", "00:00:00")),
            "seconds": start_s,
            "text": text,
            "color": spk_color_map.get(spk, "#FFFFFF")
        })

    # Estimar end_seconds para cada bloco de fala
    for idx, item in enumerate(transcript_items):
        if idx < len(transcript_items) - 1:
            item["end_seconds"] = transcript_items[idx + 1]["seconds"]
        else:
            words = len(item["text"].split())
            item["end_seconds"] = item["seconds"] + max(words * 0.4, 4.0)

    # Extrair audio_api_id se for WhatsApp Sync
    is_wa = str(sid).startswith("wa_")
    audio_api_id = None
    if is_wa:
        parts = sid.split("_")
        if len(parts) >= 3:
            try:
                audio_api_id = int(parts[-1])
            except ValueError:
                pass
    if audio_api_id is not None:
        owned_audio_ids = {
            resource["id"]
            for resource in list_external_resources("whatsapp_audio")
        }
        if str(audio_api_id) not in owned_audio_ids:
            audio_api_id = None

    # Obter áudio da API
    audio_b64 = ""
    audio_html = ""
    if audio_api_id:
        try:
            wa_msg_id = str(audio.get("whatsapp_message_id") or "")
            audio_bytes = _load_audio_bytes(audio_api_id, wa_msg_id)
            if audio_bytes:
                audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        except Exception as e:
            st.warning(f"Não foi possível carregar o áudio da API: {e}")

        audio_html = """
        <div id="audio-loading-placeholder" style="padding: 12px; background: #21262d; border-radius: 8px; margin-bottom: 15px; color: #8b949e; text-align: center; display: none;">
          <span>Carregando áudio...</span>
        </div>
        <audio id="audio-player" controls style="width: 100%; margin-bottom: 15px; border-radius: 8px;">
          <source src="" type="audio/wav">
        </audio>
        """
    else:
        audio_html = '<div style="padding: 10px; background: #262730; border-radius: 8px; margin-bottom: 15px; color: #808495; text-align: center;">Áudio disponível apenas para sincronização WhatsApp API (manual upload)</div>'

    # Gerar os elementos HTML de cada turno
    html_turns = []
    for idx, item in enumerate(transcript_items):
        escaped_text = item["text"].replace('"', '&quot;').replace("'", "&#39;")
        html_turns.append(
            f'<div class="transcript-turn" id="turn-{idx}" data-seconds="{item["seconds"]}" data-end="{item["end_seconds"]}" data-color="{item["color"]}" style="padding: 12px; margin-bottom: 8px; border-radius: 6px; border-left: 4px solid transparent; background-color: #21262d; transition: all 0.25s ease;">'
            f'  <div style="font-weight: bold; color: {item["color"]}; font-size: 0.9em; margin-bottom: 4px; display: flex; justify-content: space-between;">'
            f'    <span>{item["speaker"]}</span>'
            f'    <span style="font-weight: normal; color: #8b949e; font-size: 0.85em;">{item["timestamp"]}</span>'
            f'  </div>'
            f'  <div style="color: #c9d1d9; font-size: 0.95em; line-height: 1.45;">{escaped_text}</div>'
            f'</div>'
        )

    turns_joined = "\n".join(html_turns)

    widget_html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body {
                background-color: #0e1117;
                color: #c9d1d9;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
                margin: 0;
                padding: 0;
                overflow: hidden;
            }
            .dashboard-container {
                display: flex;
                gap: 20px;
                height: 100vh;
                padding: 15px;
                box-sizing: border-box;
            }
            .charts-panel {
                flex: 65;
                display: flex;
                flex-direction: column;
                gap: 15px;
                overflow-y: auto;
                padding-right: 10px;
                height: 100%;
            }
            .player-panel {
                flex: 35;
                display: flex;
                flex-direction: column;
                height: 100%;
            }
            .player-sticky {
                margin-bottom: 15px;
            }
            #transcript-container {
                flex: 1;
                overflow-y: auto;
                padding: 15px;
                border: 1px solid #30363d;
                border-radius: 8px;
                background-color: #161b22;
            }
            .chart-wrapper {
                background-color: #161b22;
                border: 1px solid #30363d;
                border-radius: 8px;
                padding: 15px;
                position: relative;
                box-sizing: border-box;
            }
            .chart-title {
                font-size: 0.9em;
                font-weight: bold;
                margin-bottom: 8px;
                color: #8b949e;
            }
            .transcript-turn {
                cursor: pointer;
                padding: 12px;
                margin-bottom: 8px;
                border-radius: 6px;
                border-left: 4px solid transparent;
                background-color: #21262d;
                transition: all 0.25s ease;
            }
            .transcript-turn:hover {
                background-color: #30363d !important;
            }
            .transcript-turn.active {
                background-color: #1f2937 !important;
                box-shadow: 0 0 10px rgba(0, 0, 0, 0.5);
            }
            ::-webkit-scrollbar {
                width: 6px;
                height: 6px;
            }
            ::-webkit-scrollbar-track {
                background: #161b22;
            }
            ::-webkit-scrollbar-thumb {
                background: #30363d;
                border-radius: 3px;
            }
            ::-webkit-scrollbar-thumb:hover {
                background: #8b949e;
            }
        </style>
    </head>
    <body>
        <div class="dashboard-container">
            <div class="charts-panel">
                <div class="chart-wrapper" id="wrapper-f0">
                    <div class="chart-title">Frequência Fundamental (F0 - Pitch)</div>
                    <div style="height: 180px; position: relative;">
                        <canvas id="chart-f0"></canvas>
                    </div>
                </div>
                <div class="chart-wrapper" id="wrapper-loudness">
                    <div class="chart-title">Intensidade (Loudness - Volume)</div>
                    <div style="height: 180px; position: relative;">
                        <canvas id="chart-loudness"></canvas>
                    </div>
                </div>
                <div class="chart-wrapper" id="wrapper-emotions">
                    <div class="chart-title">Emoções Categoriais</div>
                    <div style="height: 180px; position: relative;">
                        <canvas id="chart-emotions"></canvas>
                    </div>
                </div>
                <div class="chart-wrapper" id="wrapper-dimensions">
                    <div class="chart-title">Dimensões Afetivas (Arousal, Valence, Dominance)</div>
                    <div style="height: 180px; position: relative;">
                        <canvas id="chart-dimensions"></canvas>
                    </div>
                </div>
                <div class="chart-wrapper" id="wrapper-temporal">
                    <div class="chart-title">Dinâmica Temporal (Taxa de fala & Entonação)</div>
                    <div style="height: 180px; position: relative;">
                        <canvas id="chart-temporal"></canvas>
                    </div>
                </div>
            </div>
            <div class="player-panel">
                <div class="player-sticky">
                    __AUDIO_HTML__
                </div>
                <div id="transcript-container">
                    __TURNS_JOINED__
                </div>
            </div>
        </div>
        
        <script>
            const rawData = __ACOUSTIC_DATA__;
            const focusSeconds = parseFloat(__FOCUS_SECONDS__);
            const audio = document.getElementById('audio-player');
            const turns = document.querySelectorAll('.transcript-turn');
            const container = document.getElementById('transcript-container');

            // Custom vertical playhead plugin
            const verticalLinePlugin = {
                id: 'verticalLine',
                afterDraw: (chart) => {
                    const xVal = chart.config.options.plugins.verticalLine?.xVal;
                    if (xVal !== undefined && xVal !== null) {
                        const xScale = chart.scales.x;
                        const yScale = chart.scales.y;
                        const xPos = xScale.getPixelForValue(xVal);
                        if (xPos >= xScale.left && xPos <= xScale.right) {
                            const ctx = chart.ctx;
                            ctx.save();
                            ctx.beginPath();
                            ctx.moveTo(xPos, yScale.top);
                            ctx.lineTo(xPos, yScale.bottom);
                            ctx.lineWidth = 2;
                            ctx.strokeStyle = '#FFD166';
                            ctx.stroke();
                            ctx.restore();
                        }
                    }
                }
            };
            Chart.register(verticalLinePlugin);

            // Time sorting
            rawData.sort((a, b) => {
                const ta = a.start_s !== undefined ? a.start_s : (a.start !== undefined ? a.start : 0);
                const tb = b.start_s !== undefined ? b.start_s : (b.start !== undefined ? b.start : 0);
                return ta - tb;
            });

            const xValues = rawData.map(d => {
                const t = d.start_s !== undefined ? d.start_s : (d.start !== undefined ? d.start : 0);
                return parseFloat(t);
            });

            function hasField(field) {
                return rawData.length > 0 && rawData.some(d => d[field] !== undefined && d[field] !== null);
            }

            function getDataset(label, field, color) {
                if (!hasField(field)) return null;
                return {
                    label: label,
                    data: rawData.map(d => {
                        const t = d.start_s !== undefined ? d.start_s : (d.start !== undefined ? d.start : 0);
                        const val = d[field];
                        return {
                            x: parseFloat(t),
                            y: (val !== null && val !== undefined) ? parseFloat(val) : null
                        };
                    }),
                    borderColor: color,
                    backgroundColor: color + '15',
                    borderWidth: 2,
                    tension: 0.3,
                    pointRadius: 0,
                    pointHoverRadius: 5,
                    fill: false,
                    spanGaps: false
                };
            }

            function createChart(canvasId, wrapperId, datasets) {
                const canvas = document.getElementById(canvasId);
                if (!canvas) return null;
                const wrapper = document.getElementById(wrapperId);
                if (datasets.length === 0) {
                    if (wrapper) wrapper.style.display = 'none';
                    return null;
                }

                const ctx = canvas.getContext('2d');
                const chart = new Chart(ctx, {
                    type: 'line',
                    data: {
                        datasets: datasets
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: {
                            mode: 'index',
                            intersect: false
                        },
                        scales: {
                            x: {
                                type: 'linear',
                                position: 'bottom',
                                title: {
                                    display: false
                                },
                                grid: {
                                    color: '#30363d'
                                },
                                ticks: {
                                    color: '#8b949e'
                                }
                            },
                            y: {
                                grid: {
                                    color: '#30363d'
                                },
                                ticks: {
                                    color: '#8b949e'
                                }
                            }
                        },
                        plugins: {
                            legend: {
                                position: 'top',
                                align: 'end',
                                labels: {
                                    color: '#c9d1d9',
                                    boxWidth: 12,
                                    font: {
                                        size: 11
                                    }
                                }
                            },
                            verticalLine: {
                                xVal: null
                            }
                        }
                    }
                });

                // Click event to seek
                canvas.addEventListener('click', (evt) => {
                    const xScale = chart.scales.x;
                    const rect = canvas.getBoundingClientRect();
                    const clickX = evt.clientX - rect.left;
                    if (clickX >= xScale.left && clickX <= xScale.right) {
                        const timeValue = xScale.getValueForPixel(clickX);
                        if (audio && !isNaN(timeValue)) {
                            audio.currentTime = timeValue;
                            audio.play().catch(e => console.log('Autoplay blocked:', e));
                        }
                    }
                });

                return chart;
            }

            // Fallback for empty data
            if (rawData.length === 0) {
                document.querySelector('.charts-panel').style.display = 'none';
                document.querySelector('.player-panel').style.width = '100%';
                document.querySelector('.player-panel').style.flex = '1';
            }

            // Instantiation
            const f0Datasets = [
                getDataset('Média (Hz)', 'f0_media', '#00CC96'),
                getDataset('Mín (Hz)', 'f0_min', '#636EFA'),
                getDataset('Máx (Hz)', 'f0_max', '#EF553B')
            ].filter(d => d !== null);

            const loudnessDatasets = [
                getDataset('Média', 'loudness_media', '#AB63FA'),
                getDataset('Variação', 'loudness_variacao', '#FFA15A')
            ].filter(d => d !== null);

            const emotionsDatasets = [
                getDataset('Neutro', 'emocao_neutral', '#808495'),
                getDataset('Feliz', 'emocao_happy', '#FFD166'),
                getDataset('Triste', 'emocao_sad', '#19D3F3'),
                getDataset('Raiva', 'emocao_angry', '#FF6692')
            ].filter(d => d !== null);

            const dimensionsDatasets = [
                getDataset('Valência', 'dim_valence', '#00CC96'),
                getDataset('Arousal', 'dim_arousal', '#EF553B'),
                getDataset('Dominância', 'dim_dominance', '#AB63FA')
            ].filter(d => d !== null);

            const temporalDatasets = [
                getDataset('Taxa de Fala', 'speaking_rate', '#FFA15A'),
                getDataset('Entonação', 'intonation_score', '#19D3F3')
            ].filter(d => d !== null);

            const charts = [];
            const c1 = createChart('chart-f0', 'wrapper-f0', f0Datasets); if (c1) charts.push(c1);
            const c2 = createChart('chart-loudness', 'wrapper-loudness', loudnessDatasets); if (c2) charts.push(c2);
            const c3 = createChart('chart-emotions', 'wrapper-emotions', emotionsDatasets); if (c3) charts.push(c3);
            const c4 = createChart('chart-dimensions', 'wrapper-dimensions', dimensionsDatasets); if (c4) charts.push(c4);
            const c5 = createChart('chart-temporal', 'wrapper-temporal', temporalDatasets); if (c5) charts.push(c5);

            // Seek on transcript turn click
            turns.forEach(turn => {
                turn.addEventListener('click', () => {
                    if (audio) {
                        const secs = parseFloat(turn.getAttribute('data-seconds'));
                        audio.currentTime = secs;
                        audio.play().catch(e => console.log('Autoplay blocked:', e));
                    }
                });
            });

            // Update function
            function updatePlayback(curTime) {
                // Update vertical line in all charts
                charts.forEach(chart => {
                    if (chart.config.options.plugins.verticalLine) {
                        chart.config.options.plugins.verticalLine.xVal = curTime;
                        chart.update('none');
                    }
                });

                // Update active turns
                let activeTurn = null;
                turns.forEach(turn => {
                    const start = parseFloat(turn.getAttribute('data-seconds'));
                    const end = parseFloat(turn.getAttribute('data-end'));

                    if (curTime >= start && curTime < end) {
                        turn.classList.add('active');
                        const spkColor = turn.getAttribute('data-color');
                        turn.style.borderLeftColor = spkColor;
                        activeTurn = turn;
                    } else {
                        turn.classList.remove('active');
                        turn.style.borderLeftColor = 'transparent';
                    }
                });

                if (activeTurn && !activeTurn.classList.contains('scrolled')) {
                    turns.forEach(t => t.classList.remove('scrolled'));
                    activeTurn.scrollIntoView({
                        behavior: 'smooth',
                        block: 'center'
                    });
                    activeTurn.classList.add('scrolled');
                }
            }

            // Background audio loading and retry logic
            if (audio && audio.id === 'audio-player') {
                const placeholder = document.getElementById('audio-loading-placeholder');
                const audioB64 = "__AUDIO_B64__";
                if (audioB64 && audioB64.length > 0) {
                    const audioUrl = "data:audio/wav;base64," + audioB64;
                    audio.src = audioUrl;
                    audio.style.display = 'block';
                    if (placeholder) placeholder.style.display = 'none';

                    audio.addEventListener('timeupdate', () => {
                        updatePlayback(audio.currentTime);
                    });

                    if (focusSeconds > 0) {
                        audio.currentTime = focusSeconds;
                        setTimeout(() => {
                            let activeTurn = null;
                            turns.forEach(turn => {
                                const start = parseFloat(turn.getAttribute('data-seconds'));
                                const end = parseFloat(turn.getAttribute('data-end'));

                                if (focusSeconds >= start && focusSeconds < end) {
                                    turn.classList.add('active');
                                    const spkColor = turn.getAttribute('data-color');
                                    turn.style.borderLeftColor = spkColor;
                                    activeTurn = turn;
                                } else {
                                    turn.classList.remove('active');
                                    turn.style.borderLeftColor = 'transparent';
                                }
                            });

                            if (activeTurn) {
                                activeTurn.scrollIntoView({
                                    behavior: 'auto',
                                    block: 'center'
                                });
                                activeTurn.classList.add('scrolled');
                            }
                            audio.play().catch(e => console.log('Autoplay blocked:', e));
                        }, 200);
                    }
                } else if (placeholder) {
                    placeholder.style.display = 'block';
                    placeholder.innerHTML = '<span style="color: #8b949e;">Áudio da API não carregado</span>';
                }
            } else if (audio) {
                // If it's a generic audio (not audio-player)
                audio.addEventListener('timeupdate', () => {
                    updatePlayback(audio.currentTime);
                });
                
                if (focusSeconds > 0) {
                    audio.currentTime = focusSeconds;
                    setTimeout(() => {
                        let activeTurn = null;
                        turns.forEach(turn => {
                            const start = parseFloat(turn.getAttribute('data-seconds'));
                            const end = parseFloat(turn.getAttribute('data-end'));

                            if (focusSeconds >= start && focusSeconds < end) {
                                turn.classList.add('active');
                                const spkColor = turn.getAttribute('data-color');
                                turn.style.borderLeftColor = spkColor;
                                activeTurn = turn;
                            } else {
                                turn.classList.remove('active');
                                turn.style.borderLeftColor = 'transparent';
                            }
                        });

                        if (activeTurn) {
                            activeTurn.scrollIntoView({
                                behavior: 'auto',
                                block: 'center'
                            });
                            activeTurn.classList.add('scrolled');
                        }
                        audio.play().catch(e => console.log('Autoplay blocked:', e));
                    }, 200);
                }
            }
        </script>
    </body>
    </html>
    """

    # Realizar as substituições no template HTML
    widget_html = widget_html.replace("__AUDIO_HTML__", audio_html)
    widget_html = widget_html.replace("__AUDIO_B64__", audio_b64)
    widget_html = widget_html.replace("__TURNS_JOINED__", turns_joined)
    widget_html = widget_html.replace("__ACOUSTIC_DATA__", sinc_json)
    widget_html = widget_html.replace("__FOCUS_SECONDS__", str(focus_seconds))
    widget_html = widget_html.replace("__AUDIO_ID__", str(audio_id))

    # Renderizar o widget customizado unificado
    import streamlit.components.v1 as components
    components.html(widget_html, height=850)

    # ------------------------------------------------------------------
    # Outras Visualizações (Estáticas)
    # ------------------------------------------------------------------
    st.write("")
    with st.expander("📊 Outras Visualizações (Estáticas)", expanded=False):
        # Chart 1: VAD Timeline
        if not vad_df.empty:
            fig_vad = create_vad_timeline(vad_df, session_id=sid, title=f"VAD — {sid}")
            st.plotly_chart(fig_vad, use_container_width=True)

            m1, m2, m3 = st.columns(3)
            m1.metric("Segmentos VAD", len(vad_df))
            m2.metric("Fala total (s)", f"{vad_df['duration'].sum():.1f}")
            if "end" in vad_df.columns and "start" in vad_df.columns:
                total_dur = vad_df["end"].max() - vad_df["start"].min()
                speech = vad_df["duration"].sum()
                m3.metric("% de fala", f"{100*speech/max(total_dur,1):.0f}%")
        else:
            st.info("Dados VAD não disponíveis para este áudio.")

        # Chart 2: Participação por locutor
        fig_stats = create_speaker_stats(tr_filtered, session_id=sid)
        st.plotly_chart(fig_stats, use_container_width=True)

else:
    st.info("Dados de transcrição não disponíveis para este áudio.")

