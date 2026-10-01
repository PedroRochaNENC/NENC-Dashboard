"""
Revisão das importações pendentes da Jornada de Compra, na tela de Uploads.

O script `scripts/jornada_enviar.py` manda a pasta do projeto para a API, que
guarda tudo como importação pendente. Aqui quem tem escrita confere a prévia
(arquivos de dados, escolhas do registro de campo, vídeos, imagens,
documentos e o que ficou de fora), completa o que faltar e grava ou descarta.
Nada entra na análise antes disso.

Também ficam aqui os campos de completar tarefa, loja, grupo e participante,
que o upload manual da mesma tela usa.
"""

import base64
from pathlib import PurePosixPath
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import pandas as pd
import streamlit as st

from utils import auth, jornada_db, jornada_imports
from utils.jornada_format import fmt_pct, fmt_seconds
from utils.jornada_gallery import downscale
from utils.jornada_ingest import KIND_LABELS, TASK_LABELS, parse_upload
from utils.jornada_model import build_model

TASK_OPTIONS = [""] + list(TASK_LABELS)
IMAGE_CATEGORIES = ["", "gôndola", "heatmap", "embalagem", "outra"]
STATUS_LABELS = {"recebendo": "ainda recebendo arquivos", "pronta": "pronta para revisar"}
DOC_TYPE_LABELS = {"briefing": "briefing", "relatorio": "relatório"}
_PARSED_ROLES = ("registro_campo", "dados", "quadros", "imagem")


# ---------------------------------------------------------------------------
# Campos de completar (upload manual e importação)
# ---------------------------------------------------------------------------

def completion_fields(entries: Sequence[Tuple[object, str, object]], key: str) -> Dict[object, Dict[str, str]]:
    """O que o nome do arquivo não disse, em campos explícitos, por arquivo.

    `entries` traz (chave, nome, leitura) só dos arquivos que precisam: os que
    têm `needs` e as imagens. A grade da prévia serve para conferir e escolher
    o que entra; digitar fica aqui.
    """
    corrections: Dict[object, Dict[str, str]] = {}
    if not entries:
        return corrections
    st.markdown("**Completar antes de gravar**")
    for entry_key, name, parsed in entries:
        needs = parsed.meta.get("needs") or []
        columns = st.columns([3, 2, 2, 2])
        columns[0].markdown(
            '<div style="padding-top:1.9rem;font-size:.85rem">{}</div>'.format(name),
            unsafe_allow_html=True,
        )
        values: Dict[str, str] = {}
        widget = "{}_{}".format(key, entry_key)
        if parsed.kind == "image":
            values["category"] = columns[1].selectbox("Categoria", IMAGE_CATEGORIES, key=widget + "_cat")
            values["store"] = columns[2].text_input("Loja (opcional)", key=widget + "_store")
            values["brand"] = columns[3].text_input("Marca (opcional)", key=widget + "_brand")
        else:
            if "task" in needs:
                values["task"] = columns[1].selectbox(
                    "Tarefa", TASK_OPTIONS, format_func=lambda task: TASK_LABELS.get(task, "— escolha —"),
                    key=widget + "_task",
                )
            if "store" in needs:
                values["store"] = columns[2].text_input("Loja", key=widget + "_store")
            if "group" in needs:
                values["group"] = columns[3].text_input("Grupo", placeholder="PERFIL 1, TODOS...",
                                                        key=widget + "_group")
            if "participant" in needs:
                values["participant"] = columns[3].text_input("Participante", placeholder="Pt01",
                                                              key=widget + "_part")
        corrections[entry_key] = values
    return corrections


# ---------------------------------------------------------------------------
# Prévia
# ---------------------------------------------------------------------------

def _mb(size) -> Optional[float]:
    try:
        return round(float(size) / (1024 * 1024), 1)
    except (TypeError, ValueError):
        return None


def _task(meta: Dict) -> str:
    task = meta.get("task") or ""
    tasks = meta.get("tasks") or []
    if not task and len(tasks) == 1:
        task = tasks[0]
    return TASK_LABELS.get(task, task)


def data_rows(files: Sequence[Dict], parsed: Dict[int, object], existing: set) -> pd.DataFrame:
    """Uma linha por arquivo de dados, como na prévia do upload manual."""
    rows = []
    for item in files:
        reading = parsed[item["id"]]
        meta = reading.meta
        duplicate = item["sha256"] in existing
        messages = [issue["message"] for issue in reading.issues]
        if duplicate:
            messages.insert(0, "já está no projeto")
        store = meta.get("store_label") or meta.get("store") or ""
        if meta.get("stores"):
            store = ", ".join(sorted(meta["stores"].values()))
        rows.append({
            "id": item["id"],
            "incluir": bool(reading.ok and not duplicate),
            "arquivo": PurePosixPath(item["rel_path"]).name,
            "tipo": KIND_LABELS.get(reading.kind, "não reconhecido"),
            "participante": meta.get("participant") or ", ".join(meta.get("participants") or [])[:40],
            "tarefa": _task(meta),
            "loja": store,
            "grupo": meta.get("group") or "",
            "linhas": meta.get("n_rows") or meta.get("n_choices"),
            "avisos": "; ".join(messages),
            "pasta": str(PurePosixPath(item["rel_path"]).parent),
        })
    return pd.DataFrame(rows)


def video_rows(files: Sequence[Dict], existing_media: set) -> pd.DataFrame:
    rows = []
    for item in files:
        meta = item["meta"]
        duplicate = bool({item["sha256"], item.get("source_sha256")} & existing_media)
        rows.append({
            "id": item["id"],
            "incluir": bool(meta.get("participant") and meta.get("task") and not duplicate),
            "arquivo": PurePosixPath(item["rel_path"]).name,
            "tipo": "heatmap" if item["role"] == "video_heatmap" else "cena",
            "participante": meta.get("participant") or "",
            "tarefa": TASK_LABELS.get(meta.get("task"), meta.get("task") or ""),
            "loja": meta.get("store_label") or meta.get("store") or "",
            "tamanho (MB)": _mb(item["size_bytes"]),
            "original (MB)": _mb(meta.get("original_size")) if meta.get("compactado") else None,
            "avisos": "já está no projeto" if duplicate else "",
        })
    return pd.DataFrame(rows)


def thumbnail(content: bytes, size: int = 160) -> str:
    """Miniatura em data URI para a grade (as fotos originais passam de 1 MB)."""
    small = downscale(content, size, quality=70)
    return "data:image/jpeg;base64," + base64.b64encode(small).decode("ascii") if small else ""


def image_rows(files: Sequence[Dict], thumbnails: Dict[int, str], existing: set) -> pd.DataFrame:
    labels = {"gondola": "gôndola"}
    rows = []
    for item in files:
        meta = item["meta"]
        rows.append({
            "id": item["id"],
            "incluir": item["sha256"] not in existing,
            "miniatura": thumbnails.get(item["id"], ""),
            "arquivo": PurePosixPath(item["rel_path"]).name,
            "categoria": labels.get(meta.get("category"), meta.get("category") or ""),
            "loja": meta.get("store_label") or meta.get("store") or "",
            "marca": meta.get("brand") or "",
            "vista": meta.get("view") or "",
            "editada": "sim" if meta.get("edited") else "",
        })
    return pd.DataFrame(rows)


def document_rows(files: Sequence[Dict], context_empty: bool, kb_ready: bool) -> pd.DataFrame:
    rows = []
    briefing_free = context_empty
    for item in files:
        meta = item["meta"]
        briefing = meta.get("doc_type") == "briefing"
        destinations = []
        if briefing and briefing_free:
            destinations.append("Contexto do projeto")
            briefing_free = False
        if kb_ready:
            destinations.append("base de conhecimento")
        nowhere = ("nenhum (o Contexto já existe e a base está indisponível)" if briefing
                   else "nenhum (a base de conhecimento está indisponível)")
        rows.append({
            "id": item["id"],
            "incluir": bool(destinations),
            "documento": meta.get("original_name") or PurePosixPath(item["rel_path"]).name,
            "tipo": DOC_TYPE_LABELS.get(meta.get("doc_type"), "documento"),
            "caracteres": meta.get("text_chars"),
            "destino": " e ".join(destinations) or nowhere,
        })
    return pd.DataFrame(rows)


def choice_rows(choices: pd.DataFrame) -> pd.DataFrame:
    """Escolhas como a análise vai lê-las, para conferir a normalização."""
    if choices is None or choices.empty:
        return pd.DataFrame()
    frame = choices.sort_values(["task", "store", "participant"])
    return pd.DataFrame({
        "participante": frame["participant"],
        "tarefa": frame["task_label"],
        "loja": frame["store_label"],
        "escolha (campo)": frame["chosen_text"],
        "marca": [", ".join(brands) if brands else ("não reconhecida" if str(text or "").strip() else "")
                  for brands, text in zip(frame["chosen_brands"], frame["chosen_text"])],
        "variante": frame["chosen_values_text"],
        "consideradas": frame["considered_brands"].map(lambda brands: ", ".join(brands or [])),
        "embalagens": frame["packs"].map(lambda packs: ", ".join(str(p) for p in packs or [])),
        "tempo de compra": frame["purchase_time_s"].map(fmt_seconds),
        "fração na categoria": frame["category_fraction"].map(fmt_pct),
    })


def preview_model(project: Dict, files: Sequence[Dict], contents: Dict[int, bytes], parsed: Dict[int, object]) -> Dict:
    """O modelo do projeto como fica depois de gravar estes arquivos de dados."""
    bundle = jornada_db.load_project_bundle(project["id"], project["organization_id"])
    known = {item["sha256"] for item in bundle["files"]}
    extra = []
    for item in files:
        reading = parsed.get(item["id"])
        if item["role"] == "imagem" or reading is None or not reading.ok or item["sha256"] in known:
            continue
        extra.append({
            "id": -int(item["id"]),
            "filename": PurePosixPath(item["rel_path"]).name,
            "kind": reading.kind,
            "sha256": item["sha256"],
            "meta": {"overrides": {"kind": reading.kind}},
            "content": contents[item["id"]],
            "created_at": "9999",
        })
    bundle["files"] = list(bundle["files"]) + extra
    return build_model(bundle)


def document_sender(project: Dict, user) -> Tuple[Optional[Callable[[str, str, Dict], None]], str]:
    """Quem manda o texto dos documentos para a base da Jornada, ou o motivo de não haver."""
    if auth.active_organization_id(user) == 0:
        return None, "em \"Todas as organizações\" a base seria a da sua organização, não a do projeto"
    from utils.ai_provider import add_document_to_vector_store, get_openai_client, get_vector_store_id
    from utils.kb_attributes import project_document

    if get_openai_client() is None:
        return None, "OpenAI não configurado"
    vector_store_id = get_vector_store_id()
    if not vector_store_id:
        return None, "a base de conhecimento da Jornada não está configurada nesta organização"

    def send(name: str, text: str, meta: Dict) -> None:
        filename = name if name.lower().endswith((".txt", ".md")) else name + ".txt"
        add_document_to_vector_store(
            vector_store_id, filename, text.encode("utf-8"),
            project_document("jornada_compra", project["id"], tipo=meta.get("doc_type") or "briefing"),
            wait=False,
        )

    return send, ""


# ---------------------------------------------------------------------------
# Tela
# ---------------------------------------------------------------------------

def _cache(name: str) -> Dict:
    return st.session_state.setdefault(name, {})


def _size(size: float) -> str:
    if size < 1024 ** 2:
        return "{:.0f} KB".format(max(size / 1024, 1))
    if size < 1024 ** 3:
        return "{:.1f} MB".format(size / 1024 ** 2).replace(".", ",")
    return "{:.2f} GB".format(size / 1024 ** 3).replace(".", ",")


def _batch_label(batch: Dict) -> str:
    files = sum(values["files"] for values in batch["roles"].values())
    size = sum(values["bytes"] or 0 for values in batch["roles"].values())
    return "Importação {} · {} · recebida em {} · {} arquivo(s), {}".format(
        batch["id"], batch["source_label"] or "origem não informada", str(batch["created_at"])[:16], files,
        _size(size))


def _editor(frame: pd.DataFrame, key: str, **column_config) -> pd.DataFrame:
    config = {"id": None, "incluir": st.column_config.CheckboxColumn("Incluir", width="small")}
    config.update(column_config)
    return st.data_editor(
        frame, hide_index=True, width="stretch", key=key,
        disabled=[column for column in frame.columns if column != "incluir"], column_config=config,
    )


def _discard_controls(project: Dict, batch: Dict) -> None:
    confirm = "jc_import_discard_{}".format(batch["id"])
    if not st.session_state.get(confirm):
        if st.button("Descartar importação", key=confirm + "_ask", icon=":material/delete:"):
            st.session_state[confirm] = True
            st.rerun()
        return
    st.warning("Descartar a importação {}? Os arquivos recebidos saem do servidor; para trazê-los de volta, "
               "rode o script de novo.".format(batch["id"]))
    yes, no = st.columns(2)
    if yes.button("Confirmar descarte", key=confirm + "_yes", width="stretch"):
        try:
            jornada_imports.discard_batch(project["id"], batch["id"])
        except (auth.AuthorizationError, ValueError) as error:
            st.error(str(error))
        else:
            st.session_state.pop(confirm, None)
            st.session_state["jc_import_done"] = "Importação {} descartada.".format(batch["id"])
            st.rerun()
    if no.button("Cancelar", key=confirm + "_no", width="stretch"):
        st.session_state.pop(confirm, None)
        st.rerun()


def _report_text(report: Dict) -> str:
    parts = ["{} arquivo(s)".format(report["files"]), "{} vídeo(s)".format(report["videos"]),
             "{} documento(s)".format(report["documents"])]
    text = "Gravado: " + ", ".join(parts)
    if report["duplicates"]:
        text += "; {} já estava(m) no projeto".format(report["duplicates"])
    return text + "."


def render(project: Dict, user) -> None:
    """Seção "Importações pendentes" (só aparece quando há alguma)."""
    done = st.session_state.pop("jc_import_done", None)
    if done:
        message, skipped, warnings = (done, [], []) if isinstance(done, str) else (
            _report_text(done), done["skipped"], done["warnings"])
        st.success(message)
        if skipped:
            st.warning("Não gravados (faltou tarefa, loja, grupo ou participante): {}.".format(", ".join(skipped)))
        for warning in warnings:
            st.warning(warning)
    batches = jornada_imports.list_batches(project["id"])
    if not batches:
        return
    st.subheader("Importações pendentes")
    st.caption("Enviadas da pasta do projeto pelo script `jornada_enviar.py`. Nada entra na análise antes de "
               "alguém gravar.")
    by_id = {batch["id"]: batch for batch in batches}
    batch_id = batches[0]["id"]
    if len(batches) > 1:
        batch_id = st.selectbox("Importação", list(by_id), format_func=lambda key: _batch_label(by_id[key]),
                                key="jc_import_choice_{}".format(project["id"]))
    batch = by_id[batch_id]
    st.markdown("**{}** — {}".format(_batch_label(batch), STATUS_LABELS.get(batch["status"], batch["status"])))
    if batch["status"] == "recebendo":
        st.info("Os arquivos ainda estão chegando. Se o envio parou, rode o mesmo comando do script: ele "
                "continua de onde parou e fecha a importação para revisão.")
        _discard_controls(project, batch)
        st.divider()
        return

    files = [item for item in jornada_imports.batch_files(project["id"], batch_id) if item["status"] == "completo"]
    by_role: Dict[str, List[Dict]] = {}
    for item in files:
        by_role.setdefault(item["role"], []).append(item)
    contents, parsed, thumbs = _cache("jc_import_contents"), _cache("jc_import_parsed"), _cache("jc_import_thumbs")
    with st.spinner("Lendo os arquivos da importação..."):
        for item in files:
            key = (batch_id, item["id"])
            if item["role"] not in _PARSED_ROLES or key in parsed:
                continue
            content = jornada_imports.read_file(project["id"], batch_id, item["id"])
            name = PurePosixPath(item["rel_path"]).name
            parsed[key] = parse_upload(name, content, {"kind": "image"} if item["role"] == "imagem" else None)
            if item["role"] == "imagem":
                thumbs[key] = thumbnail(content)
            else:
                contents[key] = content
    batch_parsed = {item["id"]: parsed[(batch_id, item["id"])] for item in files if (batch_id, item["id"]) in parsed}
    batch_contents = {item["id"]: contents[(batch_id, item["id"])] for item in files
                      if (batch_id, item["id"]) in contents}

    existing = {item["sha256"] for item in jornada_db.list_files(project["id"])}
    existing_media = set()
    for item in jornada_db.list_media(project["id"]):
        existing_media.update(value for value in (item.get("sha256"), item.get("source_sha256")) if value)
    sender, sender_reason = document_sender(project, user) if by_role.get("documento") else (None, "")
    context_empty = not str(project.get("briefing_text") or "").strip()

    data_files = by_role.get("registro_campo", []) + by_role.get("dados", [])
    sections = [
        ("data", "Dados ({})".format(len(data_files)), data_files),
        ("choices", "Escolhas", by_role.get("registro_campo", [])),
        ("frames", "Quadros ({})".format(len(by_role.get("quadros", []))), by_role.get("quadros", [])),
        ("videos", "Vídeos ({})".format(len(by_role.get("video_cena", [])) + len(by_role.get("video_heatmap", []))),
         by_role.get("video_cena", []) + by_role.get("video_heatmap", [])),
        ("images", "Imagens ({})".format(len(by_role.get("imagem", []))), by_role.get("imagem", [])),
        ("documents", "Documentos ({})".format(len(by_role.get("documento", []))), by_role.get("documento", [])),
        ("ignored", "Fica de fora ({})".format(len(batch["ignored"])), batch["ignored"]),
    ]
    sections = [section for section in sections if section[2]]
    tabs = st.tabs([label for _, label, _ in sections])
    key = "jc_import_{}".format(batch_id)
    chosen: List[pd.DataFrame] = []
    fixes: Dict[int, Dict[str, str]] = {}
    send_documents = False
    for tab, (section, _, items) in zip(tabs, sections):
        with tab:
            if section in ("data", "frames"):
                frame = data_rows(items, batch_parsed, existing)
                chosen.append(_editor(frame, "{}_{}".format(key, section),
                                      linhas=st.column_config.NumberColumn("Linhas", format="%d")))
                needing = [(item["id"], PurePosixPath(item["rel_path"]).name, batch_parsed[item["id"]])
                           for item in items
                           if batch_parsed[item["id"]].ok and batch_parsed[item["id"]].meta.get("needs")]
                fixes.update(completion_fields(needing, "{}_{}_fix".format(key, section)))
            elif section == "choices":
                cache = _cache("jc_import_choices")
                version = (batch_id, int(project.get("data_version") or 0))
                if version not in cache:
                    with st.spinner("Normalizando as escolhas..."):
                        model = preview_model(project, files, batch_contents, batch_parsed)
                    cache[version] = (model["choices"], [issue for issue in model["issues"]
                                                         if issue.get("code") == "escolha_sem_marca"])
                choices, problems = cache[version]
                st.caption("Como a análise vai ler o registro de campo, normalizado pelas marcas e dimensões do "
                           "projeto. Marca não reconhecida? Cadastre-a em Dados do Projeto: a leitura refaz a "
                           "normalização a cada análise.")
                table = choice_rows(choices)
                if table.empty:
                    st.info("O registro de campo não trouxe escolhas.")
                else:
                    st.dataframe(table, hide_index=True, width="stretch")
                for problem in problems:
                    st.warning(problem["message"])
            elif section == "videos":
                st.caption("Vão para o disco do servidor e abrem em Participantes, com seletor cena/heatmap.")
                chosen.append(_editor(video_rows(items, existing_media), "{}_videos".format(key)))
            elif section == "images":
                frame = image_rows(items, {item["id"]: thumbs.get((batch_id, item["id"]), "") for item in items},
                                   existing)
                chosen.append(_editor(frame, "{}_images".format(key),
                                      miniatura=st.column_config.ImageColumn("Miniatura", width="small")))
            elif section == "documents":
                frame = document_rows(items, context_empty, sender is not None)
                chosen.append(_editor(frame, "{}_documents".format(key)))
                if sender is None:
                    st.caption("Base de conhecimento indisponível: {}.".format(sender_reason))
                send_documents = sender is not None and st.checkbox(
                    "Mandar o texto dos documentos para a base de conhecimento da Jornada", value=True,
                    key="{}_send_docs".format(key))
            elif section == "ignored":
                st.caption("O script deixou estes arquivos no computador, com o motivo de cada um.")
                st.dataframe(pd.DataFrame(items).rename(columns={"rel_path": "arquivo", "reason": "motivo"}),
                             hide_index=True, width="stretch")

    selected = [int(file_id) for frame in chosen if not frame.empty
                for file_id in frame.loc[frame["incluir"].astype(bool), "id"]]
    st.caption("{} de {} arquivo(s) marcados para gravar.".format(len(selected), len(files)))
    record, discard = st.columns([1, 1])
    if record.button("Gravar no projeto", type="primary", key="{}_apply".format(key), disabled=not selected,
                     width="stretch"):
        try:
            with st.spinner("Gravando a importação..."):
                report = jornada_imports.apply_batch(
                    project["id"], batch_id, fixes, selected=selected,
                    send_document=sender if send_documents else None,
                )
        except (auth.AuthorizationError, ValueError, OSError) as error:
            st.error(str(error))
        else:
            for name in ("jc_import_contents", "jc_import_parsed", "jc_import_thumbs", "jc_import_choices"):
                st.session_state.pop(name, None)
            st.session_state["jc_import_done"] = report
            st.rerun()
    with discard:
        _discard_controls(project, batch)
    st.divider()
