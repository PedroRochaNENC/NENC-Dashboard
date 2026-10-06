"""
Teste Sensorial — Sinais.

O tempo dentro das etapas:

- Curva média: cada condição janela a janela (média das médias por
  participante, ± erro padrão), alinhada no início da etapa ou na primeira
  cheirada, com o basal de referência;
- Linha do tempo individual: uma sessão de um participante, com as etapas da
  análise em sequência e as janelas que ficaram de fora em cinza.
"""

import streamlit as st
from utils import auth, ui
from utils.icons import page_title

user = auth.require_module("teste_sensorial")

from utils import sensorial_charts as charts
from utils import sensorial_db, sensorial_design
from utils.project_ui import active_project
from utils.sensorial_cache import get_curves, get_project_model

sensorial_db.init_db()
project = active_project(sensorial_db, "ts_project_id", "modules/teste_sensorial/projetos.py")

ui.inject_theme()
ui.breadcrumb("Teste Sensorial", project["name"], "Sinais")
page_title("chart-line", "Sinais", "O EEG dentro das etapas: curva média por condição e a sessão de cada um.")

model = get_project_model(project)
eeg = model["eeg"]
if eeg.empty:
    st.info("Ainda não há EEG neste projeto. Envie as saídas do pipeline em **Uploads**.")
    st.stop()

design = model["design"]
names = model["index_names"]
labels = sensorial_design.condition_labels(design)
stage_labels = {s["codigo"]: s["rotulo"] for s in design.get("etapas") or []}
measures = list(model["indices"]) + list(model["pipeline_indicators"])


def _measure_label(measure: str) -> str:
    name = names.get(measure, measure)
    return "{} ({})".format(name, measure) if name != measure else measure


tab_curve, tab_session = st.tabs(["Curva média", "Linha do tempo individual"])

with tab_curve:
    c1, c2 = st.columns([3, 2])
    measure = c1.selectbox("Medida", measures, format_func=_measure_label, key="ts_si_measure")
    alignment = c2.radio("Alinhar em", ["etapa", "olfacao"], horizontal=True, key="ts_si_alignment",
                         format_func=lambda a: "início da etapa" if a == "etapa" else "1ª cheirada")
    conditions = [c["codigo"] for c in design.get("condicoes") or [] if c["papel"] != "basal"]
    chosen = st.multiselect("Condições", conditions, default=conditions, format_func=lambda c: labels.get(c, c),
                            key="ts_si_conditions")
    result = get_curves(project, measure, alignment)
    curve = result["curva"]
    curve = curve[curve["condicao"].isin(chosen)]
    if alignment == "etapa":
        stages = [s for s in sensorial_design.analysis_stages(design) if s in set(curve["etapa"])]
        exposure = next((s["codigo"] for s in design.get("etapas") or []
                         if s.get("papel") == "exposicao" and s["codigo"] in stages), None)
        stage = st.radio("Etapa", stages, horizontal=True, format_func=lambda s: stage_labels.get(s, s),
                         index=stages.index(exposure) if exposure else 0, key="ts_si_stage") if stages else None
        curve = curve[curve["etapa"] == stage] if stage else curve
    elif result["sem_cheirada"]:
        st.caption("{} sessão(ões) sem o evento da 1ª cheirada: alinhadas no início da exposição.".format(
            result["sem_cheirada"]))
    st.plotly_chart(charts.curve_chart(curve, result["referencia"], design, names.get(measure, measure), alignment),
                    width="stretch", key="ts_si_curve")
    with st.expander("Tabela da curva"):
        st.dataframe(curve.assign(condicao=curve["condicao"].map(lambda c: labels.get(c, c)))
                     .rename(columns={"condicao": "condição", "t": "tempo (s)", "media": "média",
                                      "ep": "erro padrão"}),
                     hide_index=True, width="stretch",
                     column_config={"média": st.column_config.NumberColumn(format="%.4g"),
                                    "erro padrão": st.column_config.NumberColumn(format="%.4g")})

with tab_session:
    coded = model["sessions"][model["sessions"]["participant_code"].notna() & (model["sessions"]["janelas_eeg"] > 0)]
    if coded.empty:
        st.info("Nenhuma sessão com código e EEG.")
        st.stop()
    s1, s2 = st.columns(2)
    participant = s1.selectbox("Participante", sorted(coded["participant_code"].unique()), key="ts_si_participant")
    options = coded[coded["participant_code"] == participant]
    session_labels = {row.sessao_id: "{} · {} · {}".format(labels.get(row.condicao, row.condicao or "fora do desenho"),
                                                           row.data or "", row.sessao_id) for row in options.itertuples()}
    session = s2.selectbox("Sessão", list(session_labels), format_func=session_labels.get, key="ts_si_session")
    chosen_measures = st.multiselect("Medidas", measures, default=measures[:2], format_func=_measure_label,
                                     key="ts_si_measures", max_selections=4)
    only_analysis = st.checkbox("Só as etapas da análise", value=True, key="ts_si_only_analysis")
    frame = eeg[eeg["sessao_id"] == session].copy()
    order = sensorial_design.analysis_stages(design)
    if only_analysis:
        frame = frame[frame["Etapa"].isin(order)]
    rank = {stage: index for index, stage in enumerate(order)}
    frame["ordem"] = frame["Etapa"].map(lambda s: rank.get(s, len(rank)))
    frame = frame.sort_values(["ordem", "Bloco", "Tempo"])
    # As etapas em sequência: cada uma começa onde a anterior terminou.
    lengths = frame.groupby("Etapa", sort=False)["Tempo"].max()
    offsets, total = {}, 0.0
    for stage in frame["Etapa"].drop_duplicates():
        offsets[stage] = total
        total += float(lengths.get(stage, 0)) + 0.25
    frame["t"] = frame["Tempo"] + frame["Etapa"].map(offsets)
    if chosen_measures:
        st.plotly_chart(charts.timeline_chart(frame, chosen_measures, names, stage_labels), width="stretch",
                        key="ts_si_timeline")
        out = frame[~frame["incluida"]]
        if not out.empty:
            st.caption("Em cinza, {} janela(s) fora da análise: {}.".format(
                len(out), "; ".join("{} ({})".format(k, v) for k, v in out["motivo"].value_counts().items())))
        with st.expander("Janelas da sessão"):
            st.dataframe(frame[["Etapa", "Bloco", "Tempo", "t", "incluida", "motivo"] + chosen_measures]
                         .assign(Etapa=frame["Etapa"].map(lambda s: stage_labels.get(s, s)))
                         .rename(columns={"Etapa": "etapa", "t": "tempo na sessão (s)", "incluida": "entra"}),
                         hide_index=True, width="stretch")
