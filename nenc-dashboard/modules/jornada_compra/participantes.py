"""
Jornada de Compra — Participantes.

O equivalente da tabela de Áudios do NencBoost: cada gravação (participante ×
tarefa × loja) com duração, taxa de amostragem, unidade do export, situação na
análise e qualidade. É aqui que se decide o que entra — incluir uma gravação
zerada, excluir uma com calibração ruim — e se assiste ao vídeo, inclusive no
instante da primeira olhada de cada marca.
"""

import html

import pandas as pd
import streamlit as st
from utils import auth, ui
from utils.icons import page_title

user = auth.require_module("jornada_compra")
pode_editar = auth.can_write(user)

from utils import jornada_db, jornada_media, static_media
from utils.jornada_cache import get_project_model
from utils.jornada_ingest import TASK_LABELS
from utils.jornada_model import RECORDING_STATUS_LABELS, UNIT_LABELS
from utils.jornada_quality import run_quality, status_badge
from utils.jornada_ui import STATUS_COLORS, active_project, fmt_number, fmt_seconds

jornada_db.init_db()
project = active_project()
project_id = project["id"]

SECTIONS = ["Gravações", "Participantes", "Cobertura", "Vídeos"]
_VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".webm")

ui.inject_theme()
ui.breadcrumb("Jornada de Compra", project["name"], "Participantes")
page_title("users-three", "Participantes", "Gravações, qualidade, cobertura e vídeos.")

model = get_project_model(project)
recordings = model["recordings"]
if recordings.empty:
    st.info("Nenhuma gravação ainda. Envie os arquivos do estudo em **Uploads**.")
    st.stop()

quality = run_quality(model, project)
summary = quality["summary"].set_index("recording_key")
checks = quality["checks"]

included = recordings[recordings["status"] == "incluida"]
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Participantes", model["participants"]["participant"].nunique())
k2.metric("Gravações", len(recordings))
k3.metric("Na análise individual", len(included))
k4.metric("Não codificadas", int((recordings["status"] == "nao_codificada").sum()))
k5.metric("Com alerta de qualidade", int((summary["quality"] != "pass").sum()))

focus = st.session_state.get("jc_media_focus")
if focus and st.session_state.get("jc_part_section") != "Vídeos":
    st.session_state["jc_part_section"] = "Vídeos"
section = st.segmented_control(
    "Seção", SECTIONS, default="Gravações", key="jc_part_section", label_visibility="collapsed"
) or "Gravações"

# ==================================================================
# Gravacoes
# ==================================================================
if section == "Gravações":
    table = pd.DataFrame(
        {
            "Participante": recordings["participant"],
            "Tarefa": recordings["task_label"],
            "Loja": recordings["store_label"],
            "Perfil": recordings["profile"],
            "Duração": recordings["duration_s"].map(fmt_seconds),
            "Hz": recordings["hz"].map(lambda v: fmt_number(v, 1)),
            "Perda": recordings["loss_pct"].map(lambda v: fmt_number(v, 1, "%")),
            "Unidade": recordings["unit"].map(lambda u: UNIT_LABELS.get(u, "—")),
            "Situação": recordings["status"].map(RECORDING_STATUS_LABELS),
            "Qualidade": recordings["recording_key"].map(summary["quality_label"]),
            "Alertas": recordings["recording_key"].map(summary["alerts"]),
        }
    )
    tasks = ["Todas"] + sorted(recordings["task_label"].unique())
    task_filter = st.radio("Tarefa", tasks, horizontal=True, key="jc_part_task")
    visible = table if task_filter == "Todas" else table[table["Tarefa"] == task_filter]
    event = st.dataframe(
        visible,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        key="jc_recordings_table_{}".format(project_id),
    )
    rows = list(getattr(getattr(event, "selection", None), "rows", []) or [])
    if not rows:
        st.caption("Selecione uma gravação para ver as checagens e decidir se ela entra na análise.")
    else:
        record = recordings.loc[visible.index[rows[0]]]
        key = record["recording_key"]
        st.markdown(
            "**{} · {} · {}** — {}".format(
                record["participant"], record["task_label"], record["store_label"],
                RECORDING_STATUS_LABELS.get(record["status"], record["status"]),
            )
        )
        if record.get("status_reason"):
            st.caption(record["status_reason"])
        detail = checks[checks["recording_key"] == key]
        for _, check in detail.iterrows():
            marker = {"pass": "✓", "warn": "!", "fail": "✕"}.get(check["status"], "·")
            st.markdown(
                "`{}` **{}** — {}".format(marker, html.escape(check["label"]), html.escape(check["detail"]))
            )
        if record.get("unit_evidence"):
            st.caption("Unidade: {} — {}".format(record["unit"] or "—", record["unit_evidence"]))

        if pode_editar:
            with st.form("jc_recording_form_{}".format(key)):
                choices = {"auto": "Automática", "incluida": "Incluir na análise",
                           "excluida": "Excluir da análise"}
                current = record.get("override_status") or "auto"
                decision = st.radio(
                    "Situação na análise", list(choices), index=list(choices).index(current),
                    format_func=choices.get, horizontal=True,
                    help="Automática: entra se tiver AOIs codificadas. Incluir uma gravação "
                         "zerada conta como 'não olhou nada'.",
                )
                reason = st.text_input(
                    "Motivo (obrigatório para excluir)",
                    value=(record.get("status_reason") or "") if current == "excluida" else "",
                )
                unit_choices = {"": "Detectar", "segundos": "segundos", "amostras": "amostras (quadros)"}
                fixed_unit = (
                    record["unit"] if record["unit_evidence"] == "definida manualmente" else ""
                )
                unit_override = st.selectbox(
                    "Unidade do export", list(unit_choices),
                    index=list(unit_choices).index(fixed_unit),
                    format_func=unit_choices.get,
                )
                if st.form_submit_button("Salvar decisão", type="primary"):
                    try:
                        jornada_db.set_recording_status(
                            project_id, record["participant"], record["task"], record["store"],
                            status=decision, reason=reason, unit_override=unit_override or None,
                        )
                    except (auth.AuthorizationError, ValueError) as error:
                        st.error(str(error))
                    else:
                        st.toast("Decisão salva; a análise foi recalculada.")
                        st.rerun()

# ==================================================================
# Participantes
# ==================================================================
elif section == "Participantes":
    people = model["participants"].copy()
    people["tempo_decisao"] = people["tempo_decisao_s"].map(fmt_seconds)
    editor = st.data_editor(
        people[["participant", "profile", "tempo_informado", "tempo_decisao", "stores", "channel", "notes"]],
        hide_index=True,
        width="stretch",
        disabled=True if not pode_editar else ["participant", "tempo_decisao", "stores", "channel"],
        column_config={
            "participant": st.column_config.TextColumn("Participante"),
            "profile": st.column_config.TextColumn("Perfil"),
            "tempo_informado": st.column_config.TextColumn(
                "Tempo até a decisão (informado)", help="Como veio da planilha: 17s, 1m21s..."
            ),
            "tempo_decisao": st.column_config.TextColumn("Em m:ss"),
            "stores": st.column_config.TextColumn("Loja"),
            "channel": st.column_config.TextColumn("Canal"),
            "notes": st.column_config.TextColumn("Notas"),
        },
        key="jc_people_editor_{}".format(project_id),
    )
    if pode_editar and st.button("Salvar participantes", type="primary"):
        original = people.set_index("participant")
        changed = []
        for _, row in editor.iterrows():
            before = original.loc[row["participant"]]
            if any(
                str(row[field] or "") != str(before[field] or "")
                for field in ("profile", "tempo_informado", "notes")
            ):
                changed.append({
                    "code": row["participant"],
                    "profile": row["profile"],
                    "tempo_informado": row["tempo_informado"],
                    "notes": row["notes"],
                })
        try:
            count = jornada_db.upsert_participants(project_id, changed, source="manual")
        except (auth.AuthorizationError, ValueError) as error:
            st.error(str(error))
        else:
            st.toast("{} participante(s) atualizado(s).".format(count))
            st.rerun()
    st.caption(
        "O perfil define os grupos das comparações e dos agregados; o tempo até a decisão "
        "entra como KPI por loja, canal e perfil."
    )

# ==================================================================
# Cobertura
# ==================================================================
elif section == "Cobertura":
    coverage = model["coverage"]
    labels = dict(RECORDING_STATUS_LABELS, ausente="—")
    matrix = coverage.pivot(index="participant", columns="task", values="status").fillna("ausente")
    matrix = matrix.rename(columns=lambda task: TASK_LABELS.get(task, task))
    matrix.index.name = "Participante"
    matrix.columns.name = None
    styled = matrix.style.map(
        lambda status: "background-color: {}".format(STATUS_COLORS.get(status, "transparent"))
    ).format(lambda status: labels.get(status, status))
    st.dataframe(styled, width="stretch")
    counts = coverage[coverage["status"] != "ausente"].groupby(["task", "status"]).size().unstack(fill_value=0)
    counts = counts.rename(index=lambda task: TASK_LABELS.get(task, task),
                           columns=lambda status: labels.get(status, status))
    counts.index.name = "Tarefa"
    counts.columns.name = None
    st.markdown("**Gravações por situação**")
    st.dataframe(counts, width="stretch")
    st.caption(
        "Só entram nas métricas individuais as gravações **incluídas**. As **não codificadas** "
        "(todas as AOIs zeradas) ficam fora dos denominadores; **só agregado** entra nas análises "
        "por grupo."
    )

# ==================================================================
# Videos
# ==================================================================
else:
    media = jornada_db.list_media(project_id)
    if not media:
        st.info("Nenhum vídeo no projeto. Envie os vídeos das gravações em **Uploads**.")
        st.stop()
    labels = {
        item["id"]: "{} · {} · {}".format(
            item["participant_code"], TASK_LABELS.get(item["task"], item["task"]),
            item["store"] or "—",
        )
        for item in media
    }
    by_recording = {
        "{}|{}|{}".format(item["participant_code"], item["task"], item["store"]): item["id"]
        for item in media
    }
    default_id = media[0]["id"]
    start_at = 0.0
    if focus:
        default_id = by_recording.get(focus.get("recording_key"), default_id)
        start_at = float(focus.get("seconds") or 0.0)
        st.session_state.pop("jc_media_focus", None)
        st.session_state["jc_video_choice"] = default_id
        st.session_state["jc_video_start"] = start_at
    ids = [item["id"] for item in media]
    # O valor vive na sessao (o salto vindo da Analise Geral grava ali); passar
    # tambem `index` faria o Streamlit reclamar de dois valores iniciais.
    if st.session_state.get("jc_video_choice") not in ids:
        st.session_state["jc_video_choice"] = default_id if default_id in ids else ids[0]
    chosen = st.selectbox("Gravação", ids, format_func=labels.get, key="jc_video_choice")
    item = next(entry for entry in media if entry["id"] == chosen)
    recording = "{}|{}|{}".format(item["participant_code"], item["task"], item["store"])

    gaze = model["gaze"]
    firsts = pd.DataFrame()
    if not gaze.empty:
        cell = gaze[(gaze["recording_key"] == recording) & (gaze["looked"]) & gaze["ttff_s"].notna()]
        if not cell.empty:
            firsts = cell.sort_values("ttff_s").groupby("brand", as_index=False).first()
    if not firsts.empty:
        st.markdown("**Ir para a primeira olhada de cada marca**")
        columns = st.columns(min(len(firsts), 6) + 1)
        if columns[0].button("Início", key="jc_video_start_0"):
            st.session_state["jc_video_start"] = 0.0
        for position, (_, row) in enumerate(firsts.head(6).iterrows(), 1):
            if columns[position].button(
                "{} · {}".format(row["brand"], fmt_seconds(row["ttff_s"])),
                key="jc_video_jump_{}_{}".format(chosen, position),
            ):
                st.session_state["jc_video_start"] = float(row["ttff_s"])
    start = float(st.session_state.get("jc_video_start", start_at) or 0.0)

    try:
        source = jornada_media.resolve(item["rel_path"])
    except ValueError:
        source = None
    if source is None or not source.exists():
        st.error("O arquivo deste vídeo não está no servidor.")
    else:
        static_media.purge_stale("video_", _VIDEO_SUFFIXES)
        name_key = "jc_video_static_{}".format(chosen)
        if name_key not in st.session_state:
            st.session_state[name_key] = static_media.new_name("video_", source.suffix.lower())
        with st.spinner("Preparando o vídeo..."):
            url = static_media.publish(source, st.session_state[name_key])
        st.html(
            '<video controls preload="metadata" style="width:100%;max-height:70vh;'
            'border-radius:8px;background:#000" src="{}#t={:.1f}"></video>'.format(
                html.escape(url), start
            )
        )
        if start:
            st.caption("Começando em {}.".format(fmt_seconds(start)))

    if not gaze.empty:
        detail = gaze[gaze["recording_key"] == recording]
        if not detail.empty:
            st.markdown("**AOIs desta gravação**")
            st.dataframe(
                pd.DataFrame({
                    "AOI": detail["aoi"],
                    "Marca": detail["brand"],
                    "Tipo": detail["kind"],
                    "Tempo (s)": detail["dwell_s"].map(lambda v: fmt_number(v, 2)),
                    "Visitas": detail["visits"].astype(int),
                    "1ª olhada": detail["ttff_s"].map(fmt_seconds),
                }).sort_values("1ª olhada"),
                hide_index=True,
                width="stretch",
            )
