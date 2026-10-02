"""
Jornada de Compra — Uploads.

Envia os arquivos do estudo para o projeto. Cada arquivo é lido na hora e
aparece numa prévia (tipo, participante, tarefa, loja, grupo, linhas,
avisos) antes de ser gravado; o que o nome do arquivo não diz é completado
ali, e a correção fica guardada junto do arquivo.

Os vídeos têm seção própria: vão para o disco do servidor, não para o banco.

A pasta inteira do projeto chega pelo script `scripts/jornada_enviar.py` e
fica em "Importações pendentes" (utils/jornada_import_review.py) até alguém
conferir e gravar.
"""

import hashlib
from typing import Dict

import pandas as pd
import streamlit as st
from utils import auth, ui
from utils.icons import page_title

user = auth.require_module_write("jornada_compra")

from utils import jornada_db, jornada_import_review, jornada_imports, jornada_media
from utils.jornada_import_review import TASK_OPTIONS, completion_fields
from utils.jornada_ingest import (
    KIND_LABELS,
    TASK_LABELS,
    normalize_participant,
    parse_recording_filename,
    parse_upload,
    store_key,
)
from utils.jornada_ui import active_project

jornada_db.init_db()
project = active_project()
project_id = project["id"]


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _parsed(name: str, content: bytes, digest: str):
    """Leitura cacheada na sessão: a prévia reexecuta a cada clique."""
    cache = st.session_state.setdefault("jc_upload_parsed", {})
    key = (name, digest)
    if key not in cache:
        cache[key] = parse_upload(name, content)
    return cache[key]


def _preview_task(meta: Dict) -> str:
    """Tarefa unica do arquivo, ou vazio quando ele traz mais de uma."""
    if meta.get("task"):
        return meta["task"]
    tasks = meta.get("tasks") or []
    return tasks[0] if len(tasks) == 1 else ""


def _nonce(key: str) -> int:
    return st.session_state.setdefault(key, 0)


def _describe_meta(meta: Dict) -> str:
    overrides = meta.get("overrides") or {}
    detected = meta.get("detected") or {}
    parts = []
    task = overrides.get("task") or detected.get("task")
    if task:
        parts.append(TASK_LABELS.get(task, task))
    store = overrides.get("store_label") or overrides.get("store") or detected.get("store_label")
    if store:
        parts.append(str(store))
    group = overrides.get("group") or detected.get("group")
    if group:
        parts.append(str(group))
    participant = overrides.get("participant") or detected.get("participant")
    if participant:
        parts.append(str(participant))
    if meta.get("caption"):
        parts.append(str(meta["caption"]))
    return " · ".join(parts)


ui.inject_theme()
ui.breadcrumb("Jornada de Compra", project["name"], "Uploads")
page_title(
    "upload-simple",
    "Uploads",
    "Exports do Blickshift, quadros das gravações, planilha da equipe, imagens e vídeos.",
)

jornada_import_review.render(project, user)

existing_files = jornada_db.list_files(project_id)
existing_hashes = {item["sha256"] for item in existing_files}

# ==================================================================
# Arquivos de dados
# ==================================================================
st.subheader("Arquivos de dados")
st.markdown(
    "Envie de uma vez os arquivos do estudo. O tipo de cada um é reconhecido pelo "
    "conteúdo; confira a prévia e complete **tarefa, loja e grupo** quando o nome do "
    "arquivo não disser."
)
with st.expander("O que enviar"):
    st.markdown(
        "- **Blickshift por participante** (`*-INDIVIDUAL*.csv`): uma linha por "
        "participante × AOI. É a base da análise de gôndola.\n"
        "- **Blickshift agregado** (`*_Gaze Statistics_PERFIL n.csv`, `*_TODOS.csv`): "
        "usado onde não há dado por participante (ex.: embalagens).\n"
        "- **Quadros das gravações** (`Pt04-JEstimulada-LOJA.csv`, colunas "
        "`frame,timestamp,x,y`): dão a duração, a taxa de amostragem e a conversão de "
        "amostras em segundos. Envie todos.\n"
        "- **Planilha enriquecida** (`.xlsx` com LOJA, CANAL, Perfil, Tempo): preenche "
        "perfil, canal e o Tempo da planilha de cada participante.\n"
        "- **Registro de campo** (`Relação Coletas.xlsx`, abas Controle e Estimuladas): "
        "produto escolhido, marcas consideradas, tempo de compra e observações.\n"
        "- **Imagens**: fotos de gôndola, heatmaps e embalagens (com a marca), mostradas "
        "nas seções Gôndola e Embalagens.\n"
        "- **Entrevistas** (`arquivo, ep, identificacao, texto`).\n\n"
        "**A pasta inteira do projeto** vai de uma vez pelo script "
        "`scripts/jornada_enviar.py`, no computador que tem a pasta (veja o README); o que "
        "ele envia aparece no topo desta página, em Importações pendentes.\n\n"
        "Não envie fotos de participantes: são dado pessoal e não entram na análise."
    )

upload_key = "jc_data_upload_{}_{}".format(project_id, _nonce("jc_data_upload_nonce"))
uploaded = st.file_uploader(
    "Arquivos",
    type=["csv", "tsv", "txt", "xlsx", "xls", "png", "jpg", "jpeg", "webp"],
    accept_multiple_files=True,
    key=upload_key,
    label_visibility="collapsed",
)

if uploaded:
    preview_rows = []
    payloads = {}
    for index, file in enumerate(uploaded):
        content = file.getvalue()
        digest = _sha(content)
        parsed = _parsed(file.name, content, digest)
        meta = parsed.meta
        duplicate = digest in existing_hashes
        messages = [issue["message"] for issue in parsed.issues]
        if duplicate:
            messages.insert(0, "já está no projeto")
        store_value = meta.get("store_label") or meta.get("store") or ""
        if parsed.kind in ("bs_individual", "bs_enriched_xlsx") and meta.get("stores"):
            store_value = ", ".join(sorted(meta["stores"].values()))
        preview_rows.append(
            {
                "incluir": parsed.ok and not duplicate,
                "arquivo": file.name,
                "tipo": KIND_LABELS.get(parsed.kind, "não reconhecido"),
                "participante": meta.get("participant") or ", ".join(meta.get("participants") or [])[:40],
                "tarefa": _preview_task(meta),
                "loja": store_value,
                "grupo": meta.get("group") or "",
                "linhas": meta.get("n_rows"),
                "avisos": "; ".join(messages),
            }
        )
        payloads[index] = (file.name, content, parsed)

    preview = pd.DataFrame(preview_rows)
    nonce = _nonce("jc_data_upload_nonce")
    edited = st.data_editor(
        preview,
        hide_index=True,
        width="stretch",
        key="jc_upload_preview_{}".format(nonce),
        disabled=[column for column in preview.columns if column != "incluir"],
        column_config={
            "incluir": st.column_config.CheckboxColumn("Incluir", width="small"),
            "linhas": st.column_config.NumberColumn("Linhas", format="%d"),
        },
    )

    # O que o nome do arquivo nao disse, completado em campos explicitos: a
    # grade serve para conferir e escolher o que entra, nao para digitar.
    corrections = completion_fields(
        [
            (index, name, parsed)
            for index, (name, _, parsed) in payloads.items()
            if parsed.ok and (parsed.meta.get("needs") or parsed.kind == "image")
        ],
        "jc_fix_{}".format(nonce),
    )

    selected = edited[edited["incluir"]]
    st.caption(
        "{} de {} arquivo(s) selecionado(s) para gravar.".format(len(selected), len(edited))
    )
    if st.button("Salvar no projeto", type="primary", disabled=selected.empty):
        items = []
        interview_rows = []
        seed = []
        skipped = []
        for index in selected.index:
            name, content, parsed = payloads[index]
            if parsed.kind == "interviews" and parsed.table is not None:
                interview_rows.extend(parsed.table.to_dict("records"))
                continue
            # Mesmo formato que a revisão das importações grava; a loja digitada
            # passa pela normalização do leitor ("DSP 2250" cai na loja "2250").
            item = jornada_imports.file_item(name, content, parsed, corrections.get(index, {}))
            if item is None:
                skipped.append(name)
                continue
            if parsed.kind == "bs_enriched_xlsx":
                seed.extend(parsed.meta.get("participant_info") or [])
            items.append(item)

        if skipped:
            st.warning(
                "Não gravados por falta de tarefa, grupo ou participante: {}.".format(", ".join(skipped))
            )
        try:
            result = jornada_db.add_files(project_id, items) if items else {"added": [], "duplicates": []}
            for interview in interview_rows:
                jornada_db.add_interview(
                    project_id,
                    interview.get("titulo") or "Entrevista",
                    interview.get("texto") or "",
                    interview.get("participante_id") or "",
                )
            if seed:
                jornada_db.upsert_participants(project_id, seed, only_missing=True, source="upload")
        except (auth.AuthorizationError, ValueError) as error:
            st.error(str(error))
        else:
            st.session_state["jc_data_upload_nonce"] = _nonce("jc_data_upload_nonce") + 1
            st.session_state.pop("jc_upload_parsed", None)
            summary = "{} arquivo(s) gravado(s)".format(len(result["added"]))
            if result["duplicates"]:
                summary += ", {} já existia(m)".format(len(result["duplicates"]))
            if interview_rows:
                summary += ", {} entrevista(s)".format(len(interview_rows))
            if seed:
                summary += ", perfis de {} participante(s)".format(len(seed))
            st.toast(summary + ".")
            st.rerun()

# ==================================================================
# Videos
# ==================================================================
st.divider()
st.subheader("Vídeos das gravações")
st.markdown(
    "Vídeos com o olhar sobreposto (`Pt04-JEstimulada-LOJA-out.mp4`) e vídeos de heatmap "
    "do Blickshift. Ficam no disco do servidor, fora do banco, e abrem na página "
    "**Participantes** — inclusive no momento da primeira olhada de cada marca."
)
video_key = "jc_video_upload_{}_{}".format(project_id, _nonce("jc_video_upload_nonce"))
videos = st.file_uploader(
    "Vídeos",
    type=[extension.lstrip(".") for extension in jornada_media.VIDEO_EXTENSIONS],
    accept_multiple_files=True,
    key=video_key,
    label_visibility="collapsed",
)
if videos:
    rows = []
    for video in videos:
        key = parse_recording_filename(video.name.replace("-out", "")) or {}
        rows.append(
            {
                "incluir": bool(key.get("participant") and key.get("task")),
                "arquivo": video.name,
                # O vídeo de cena sai do rastreador como "-out"; o heatmap, sem o sufixo.
                "tipo": "cena" if "-out" in video.name else "heatmap",
                "participante": key.get("participant", ""),
                "tarefa": key.get("task", ""),
                "loja": key.get("store_label", ""),
                "tamanho (MB)": round(video.size / (1024 * 1024), 1),
            }
        )
    video_preview = pd.DataFrame(rows)
    edited_videos = st.data_editor(
        video_preview,
        hide_index=True,
        width="stretch",
        key="jc_video_preview_{}".format(_nonce("jc_video_upload_nonce")),
        disabled=["arquivo", "tamanho (MB)"],
        column_config={
            "incluir": st.column_config.CheckboxColumn("Incluir", width="small"),
            "tipo": st.column_config.SelectboxColumn("Tipo", options=list(jornada_db.MEDIA_KINDS)),
            "tarefa": st.column_config.SelectboxColumn("Tarefa", options=TASK_OPTIONS),
        },
    )
    chosen = edited_videos[edited_videos["incluir"]]
    if st.button("Salvar vídeos", type="primary", disabled=chosen.empty):
        saved, repeated = 0, 0
        progress = st.progress(0.0)
        for position, (index, row) in enumerate(chosen.iterrows(), 1):
            video = videos[index]
            participant = normalize_participant(row["participante"])
            if not participant or not row["tarefa"]:
                st.warning("{}: informe participante e tarefa.".format(video.name))
                continue
            try:
                stored = jornada_media.save_video(
                    project["organization_id"], project_id, video.name, video
                )
                _, created = jornada_db.add_media(
                    project_id,
                    participant_code=participant,
                    task=row["tarefa"],
                    store=store_key(row["loja"]),
                    filename=video.name,
                    kind=row["tipo"] if row["tipo"] in jornada_db.MEDIA_KINDS else "cena",
                    **stored,
                )
            except (auth.AuthorizationError, ValueError, OSError) as error:
                st.error("{}: {}".format(video.name, error))
                continue
            saved += int(created)
            repeated += int(not created)
            progress.progress(position / len(chosen))
        st.session_state["jc_video_upload_nonce"] = _nonce("jc_video_upload_nonce") + 1
        st.toast("{} vídeo(s) salvo(s){}.".format(
            saved, ", {} já existia(m)".format(repeated) if repeated else ""))
        st.rerun()

# ==================================================================
# O que ja esta no projeto
# ==================================================================
st.divider()
st.subheader("Arquivos no projeto")
if not existing_files:
    st.info("Nenhum arquivo gravado ainda.")
else:
    listing = pd.DataFrame(
        [
            {
                "id": item["id"],
                "arquivo": item["filename"],
                "tipo": KIND_LABELS.get(item["kind"], item["kind"]),
                "conteúdo": _describe_meta(item["meta"]),
                "tamanho (KB)": round(item["size_bytes"] / 1024, 1),
                "ativo": "sim" if item["is_active"] else "não",
                "enviado em": str(item["created_at"])[:16],
            }
            for item in existing_files
        ]
    )
    counts = listing["tipo"].value_counts()
    st.caption(" · ".join("{} {}".format(count, kind) for kind, count in counts.items()))
    selection = st.dataframe(
        listing.drop(columns=["id"]),
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="multi-row",
        key="jc_files_table_{}".format(project_id),
    )
    rows_selected = list(getattr(getattr(selection, "selection", None), "rows", []) or [])
    ids_selected = [int(listing.iloc[position]["id"]) for position in rows_selected if position < len(listing)]
    action = ui.selection_bar(
        len(ids_selected),
        (
            ("toggle", "arrows-clockwise", "Ativar / desativar"),
            ("delete", "trash", "Excluir"),
        ),
        noun="arquivo",
        empty_hint="Selecione arquivos na tabela para ativar, desativar ou excluir.",
    )
    confirm_key = "jc_files_confirm_{}".format(project_id)
    if action == "toggle":
        by_id = {item["id"]: item for item in existing_files}
        for file_id in ids_selected:
            jornada_db.set_file_active(project_id, file_id, not by_id[file_id]["is_active"])
        st.rerun()
    if action == "delete":
        st.session_state[confirm_key] = ids_selected
        st.rerun()
    pending = st.session_state.get(confirm_key)
    if pending:
        st.warning(
            "Excluir {} arquivo(s) do projeto? A análise é recalculada sem eles.".format(len(pending))
        )
        cc1, cc2 = st.columns(2)
        with cc1:
            if st.button("Confirmar exclusão", key="jc_files_del_yes", width="stretch"):
                for file_id in pending:
                    jornada_db.delete_file(project_id, file_id)
                st.session_state.pop(confirm_key, None)
                st.rerun()
        with cc2:
            if st.button("Cancelar", key="jc_files_del_no", width="stretch"):
                st.session_state.pop(confirm_key, None)
                st.rerun()

media = jornada_db.list_media(project_id)
if media:
    st.subheader("Vídeos no projeto")
    media_table = pd.DataFrame(
        [
            {
                "id": item["id"],
                "tipo": item.get("kind") or "cena",
                "participante": item["participant_code"],
                "tarefa": TASK_LABELS.get(item["task"], item["task"]),
                "loja": item["store"],
                "arquivo": item["filename"],
                "tamanho (MB)": round(item["size_bytes"] / (1024 * 1024), 1),
            }
            for item in media
        ]
    )
    st.caption("{} vídeo(s), {:.1f} MB no servidor.".format(len(media_table), media_table["tamanho (MB)"].sum()))
    media_selection = st.dataframe(
        media_table.drop(columns=["id"]),
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="multi-row",
        key="jc_media_table_{}".format(project_id),
    )
    media_rows = list(getattr(getattr(media_selection, "selection", None), "rows", []) or [])
    media_ids = [int(media_table.iloc[position]["id"]) for position in media_rows if position < len(media_table)]
    media_confirm = "jc_media_confirm_{}".format(project_id)
    if media_ids and st.button("Excluir vídeos selecionados", key="jc_media_del"):
        st.session_state[media_confirm] = media_ids
        st.rerun()
    pending_media = st.session_state.get(media_confirm)
    if pending_media:
        st.warning("Excluir {} vídeo(s)? Os arquivos saem do servidor.".format(len(pending_media)))
        mc1, mc2 = st.columns(2)
        with mc1:
            if st.button("Confirmar exclusão", key="jc_media_del_yes", width="stretch"):
                for media_id in pending_media:
                    jornada_db.delete_media(project_id, media_id)
                st.session_state.pop(media_confirm, None)
                st.rerun()
        with mc2:
            if st.button("Cancelar", key="jc_media_del_no", width="stretch"):
                st.session_state.pop(media_confirm, None)
                st.rerun()
