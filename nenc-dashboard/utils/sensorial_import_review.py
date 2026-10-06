"""
Revisão das importações pendentes do Teste Sensorial, na tela de Uploads.

O script `scripts/nenc_enviar.py --modulo teste_sensorial` manda a pasta do
estudo para a API, que guarda tudo como importação pendente. Aqui quem tem
escrita confere a prévia, corrige o papel de um arquivo, marca o briefing e
grava ou descarta. Nada entra na análise antes disso.

A prévia mostra só resumos normalizados — papel, rodada, sessões, códigos de
participante, etapas, experimentos, cobertura da BASE LIMPA e avisos —, lidos
pelo mesmo leitor da gravação. Linha crua nunca aparece: ela pode ter o nome
de quem participou.
"""

from pathlib import PurePosixPath
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import pandas as pd
import streamlit as st

from utils import auth, sensorial_db, sensorial_imports, sensorial_ingest

ROLE_LABELS = {role: str(spec["label"]) for role, spec in sensorial_ingest.ROLES.items()}
LABEL_ROLES = {label: role for role, label in ROLE_LABELS.items()}
STATUS_LABELS = {"recebendo": "ainda recebendo arquivos", "pronta": "pronta para revisar"}
DOC_TYPE_LABELS = {"briefing": "briefing", "relatorio": "relatório"}
_EEG_ROLES = ("eeg_psd", "eeg_indicadores")


def _kind(role: str) -> str:
    return str(sensorial_ingest.ROLES.get(role, {}).get("kind") or "")


def is_image(role: str, name: str) -> bool:
    return _kind(role) == "image" or (role == "estimulo" and PurePosixPath(name).suffix.lower() in
                                      sensorial_ingest.IMAGE_EXTENSIONS)


def summary(parsed: sensorial_ingest.ParsedFile) -> Dict:
    """O que a prévia guarda de uma leitura: resumo e avisos, sem a tabela."""
    return {"ok": parsed.ok, "role": parsed.role, "resumo": dict(parsed.meta.get("resumo") or {}),
            "run_id": parsed.meta.get("run_id") or "", "issues": list(parsed.issues),
            "experimento": parsed.meta.get("experimento") or "", "etapa": parsed.meta.get("etapa_arquivo") or ""}


def _experiments(resumo: Dict) -> str:
    counts = resumo.get("experimentos") or {}
    return ", ".join("{} ({})".format(name, value) for name, value in list(counts.items())[:6])


def data_rows(files: Sequence[Tuple[int, str, str, Dict]], existing: set, eeg_sessions: Optional[int]) -> pd.DataFrame:
    """Uma linha por tabela: (id, nome, sha, resumo da leitura)."""
    rows = []
    for file_id, name, sha256, read in files:
        resumo = read["resumo"]
        messages = [issue["message"] for issue in read["issues"]]
        if sha256 in existing:
            messages.insert(0, "já está no projeto")
        if str(read["role"] or "").startswith("base_limpa") and resumo.get("sessoes") and eeg_sessions:
            messages.append("cobre {} das {} sessões do EEG".format(resumo["sessoes"], eeg_sessions)
                            if read["role"] == "base_limpa_eeg" else "{} sessões".format(resumo["sessoes"]))
        rows.append({
            "id": file_id,
            "incluir": bool(read["ok"] and sha256 not in existing),
            "arquivo": name,
            "papel": ROLE_LABELS.get(read["role"], "não reconhecido"),
            "rodada": read["run_id"],
            "linhas": resumo.get("linhas"),
            "sessoes": resumo.get("sessoes"),
            "participantes": len(resumo.get("participantes") or []) or None,
            "sem_codigo": resumo.get("linhas_sem_codigo"),
            "etapas": len(resumo.get("etapas") or []) or None,
            "experimentos": _experiments(resumo),
            "avisos": "; ".join(messages),
        })
    return pd.DataFrame(rows)


def file_rows(files: Sequence[Tuple[int, str, str, Dict]], existing: set, documents: bool) -> pd.DataFrame:
    rows = []
    for file_id, name, sha256, read in files:
        row = {"id": file_id, "incluir": bool(read["ok"] and sha256 not in existing), "arquivo": name,
               "papel": ROLE_LABELS.get(read["role"], "não reconhecido"), "rodada": read["run_id"],
               "avisos": "; ".join((["já está no projeto"] if sha256 in existing else [])
                                   + [issue["message"] for issue in read["issues"]])}
        if documents:
            row["briefing"] = read.get("doc_type") == "briefing"
        else:
            row["experimento"], row["etapa"] = read["experimento"], read["etapa"]
        rows.append(row)
    return pd.DataFrame(rows)


def document_sender(project: Dict, user) -> Tuple[Optional[Callable[[str, bytes, Dict], None]], str]:
    """Quem manda os documentos para a base do Teste Sensorial, ou o motivo de não haver."""
    if auth.active_organization_id(user) == 0:
        return None, "em \"Todas as organizações\" a base seria a da sua organização, não a do projeto"
    from utils.ai_provider import add_document_to_vector_store, get_openai_client, get_sensorial_vector_store_id
    from utils.kb_attributes import project_document

    if get_openai_client() is None:
        return None, "OpenAI não configurado"
    vector_store_id = get_sensorial_vector_store_id()
    if not vector_store_id:
        return None, "a base de conhecimento do Teste Sensorial não está configurada nesta organização"

    def send(name: str, content: bytes, meta: Dict) -> None:
        add_document_to_vector_store(
            vector_store_id, name, content,
            project_document("teste_sensorial", project["id"], tipo=meta.get("doc_type") or meta.get("role")),
            wait=False)

    return send, ""


def report_text(report: Dict) -> str:
    text = "Gravado: {} arquivo(s)".format(report["files"])
    if report.get("duplicates"):
        text += "; {} já estava(m) no projeto".format(report["duplicates"])
    if report.get("participants"):
        text += "; {} participante(s) novo(s)".format(report["participants"])
    if report.get("documents"):
        text += "; {} documento(s) na base de conhecimento".format(report["documents"])
    return text + "."


# ---------------------------------------------------------------------------
# Tela
# ---------------------------------------------------------------------------

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


def _editor(frame: pd.DataFrame, key: str, editable: Sequence[str] = ("incluir",), **column_config) -> pd.DataFrame:
    config = {"id": None, "incluir": st.column_config.CheckboxColumn("Incluir", width="small"),
              "papel": st.column_config.SelectboxColumn("Papel", options=list(LABEL_ROLES), width="medium"),
              "sem_codigo": st.column_config.NumberColumn("Linhas sem código"),
              "sessoes": st.column_config.NumberColumn("Sessões")}
    config.update(column_config)
    return st.data_editor(frame, hide_index=True, width="stretch", key=key,
                          disabled=[column for column in frame.columns if column not in editable],
                          column_config=config)


def _discard_controls(project: Dict, batch: Dict) -> None:
    confirm = "ts_import_discard_{}".format(batch["id"])
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
            sensorial_imports.discard_batch(project["id"], batch["id"])
        except (auth.AuthorizationError, ValueError) as error:
            st.error(str(error))
        else:
            st.session_state.pop(confirm, None)
            st.session_state["ts_import_done"] = "Importação {} descartada.".format(batch["id"])
            st.rerun()
    if no.button("Cancelar", key=confirm + "_no", width="stretch"):
        st.session_state.pop(confirm, None)
        st.rerun()


def _read(project: Dict, batch: Dict, item: Dict, role: str) -> Dict:
    """Leitura cacheada na sessão: a prévia reexecuta a cada clique, e o PSD leva segundos."""
    cache = st.session_state.setdefault("ts_import_preview", {})
    key = (batch["id"], item["id"], role)
    if key not in cache:
        path = sensorial_imports.file_path(batch, item["id"])
        parsed = sensorial_ingest.parse_file(PurePosixPath(item["rel_path"]).name, path, role=role,
                                             rel_path=item["rel_path"])
        read = summary(parsed)
        read["doc_type"] = item["meta"].get("doc_type") or ""
        cache[key] = read
    return cache[key]


def render(project: Dict, user) -> None:
    """Seção "Importações pendentes" (só aparece quando há alguma)."""
    done = st.session_state.pop("ts_import_done", None)
    if done:
        if isinstance(done, str):
            st.success(done)
        else:
            st.success(report_text(done))
            if done["skipped"]:
                st.warning("Não gravados: {}.".format("; ".join(done["skipped"])))
            for warning in done["warnings"]:
                st.warning(warning)
    batches = sensorial_imports.list_batches(project["id"])
    if not batches:
        return
    st.subheader("Importações pendentes")
    st.caption("Enviadas da pasta do estudo pelo script `nenc_enviar.py --modulo teste_sensorial`. Nada entra na "
               "análise antes de alguém gravar.")
    by_id = {batch["id"]: batch for batch in batches}
    batch_id = batches[0]["id"]
    if len(batches) > 1:
        batch_id = st.selectbox("Importação", list(by_id), format_func=lambda key: _batch_label(by_id[key]),
                                key="ts_import_choice_{}".format(project["id"]))
    batch = by_id[batch_id]
    st.markdown("**{}** — {}".format(_batch_label(batch), STATUS_LABELS.get(batch["status"], batch["status"])))
    if batch["status"] == "recebendo":
        st.info("Os arquivos ainda estão chegando. Se o envio parou, rode o mesmo comando do script: ele continua "
                "de onde parou e fecha a importação para revisão.")
        _discard_controls(project, batch)
        st.divider()
        return
    missing = sensorial_imports.missing_files(project["id"], batch_id)
    if missing:
        st.error("{} arquivo(s) desta importação não estão mais no servidor (ex.: {}). Descarte-a e envie a pasta "
                 "de novo.".format(len(missing), missing[0]))
        _discard_controls(project, batch)
        st.divider()
        return

    files = [item for item in sensorial_imports.batch_files(project["id"], batch_id) if item["status"] == "completo"]
    existing = set()
    for item in sensorial_db.list_files(project["id"]):
        existing.update(value for value in (item["sha256"], item.get("source_sha256")) if value)
    key = "ts_import_{}".format(batch_id)
    roles = st.session_state.setdefault(key + "_roles", {})
    reads: Dict[int, Dict] = {}
    with st.spinner("Lendo os arquivos da importação (o PSD leva alguns segundos)..."):
        for item in files:
            reads[item["id"]] = _read(project, batch, item, roles.get(item["id"], item["role"]))
    eeg_sessions = max([reads[item["id"]]["resumo"].get("sessoes") or 0 for item in files
                        if reads[item["id"]]["role"] in _EEG_ROLES] + [0]) or None

    def entries(predicate) -> List[Tuple[int, str, str, Dict]]:
        chosen = []
        for item in files:
            read = reads[item["id"]]
            if predicate(read["role"] or item["role"], item):
                original = {item["sha256"], item.get("source_sha256")} & existing
                chosen.append((item["id"], PurePosixPath(item["rel_path"]).name,
                               next(iter(original)) if original else item["sha256"], read))
        return chosen

    tables = entries(lambda role, item: _kind(role) in ("table", "json"))
    images = entries(lambda role, item: is_image(role, item["rel_path"]))
    documents = entries(lambda role, item: _kind(role) == "file" and not is_image(role, item["rel_path"]))
    sender, sender_reason = document_sender(project, user) if documents else (None, "")
    sections = [(name, label, items) for name, label, items in (
        ("tables", "Dados ({})".format(len(tables)), tables),
        ("images", "Imagens ({})".format(len(images)), images),
        ("documents", "Documentos ({})".format(len(documents)), documents),
        ("ignored", "Fica de fora ({})".format(sum(int(i.get("arquivos") or 1) for i in batch["ignored"])),
         batch["ignored"]),
    ) if items]
    chosen: List[pd.DataFrame] = []
    fixes: Dict[int, Dict[str, str]] = {}
    send_documents = False
    for tab, (section, _, items) in zip(st.tabs([label for _, label, _ in sections]), sections):
        with tab:
            if section == "tables":
                st.caption("Resumo de cada tabela como a análise vai lê-la: o nome de quem participou vira o "
                           "código. Papel errado? Troque na coluna e a leitura é refeita.")
                frame = _editor(data_rows(items, existing, eeg_sessions), key + "_tables", ("incluir", "papel"),
                                linhas=st.column_config.NumberColumn("Linhas", format="%d"))
                chosen.append(frame)
            elif section == "images":
                chosen.append(_editor(file_rows(items, existing, documents=False), key + "_images",
                                      ("incluir", "papel")))
            elif section == "documents":
                frame = _editor(file_rows(items, existing, documents=True), key + "_documents",
                                ("incluir", "papel", "briefing"),
                                briefing=st.column_config.CheckboxColumn("Briefing", help="O texto vira o "
                                                                         "Contexto do projeto, se ele estiver vazio."))
                chosen.append(frame)
                for row in frame.itertuples():
                    if row.briefing:
                        fixes.setdefault(int(row.id), {})["doc_type"] = "briefing"
                if sender is None:
                    st.caption("Base de conhecimento indisponível: {}.".format(sender_reason))
                send_documents = sender is not None and st.checkbox(
                    "Mandar os documentos para a base de conhecimento do Teste Sensorial", value=True,
                    key=key + "_send_docs")
            else:
                st.caption("O script deixou estes arquivos no computador, agrupados por pasta e motivo.")
                st.dataframe(pd.DataFrame(items).rename(columns={"rel_path": "pasta", "reason": "motivo"}),
                             hide_index=True, width="stretch")

    changed = False
    for frame in chosen:
        if frame.empty:
            continue
        for row in frame.itertuples():
            role = LABEL_ROLES.get(row.papel)
            original = next(item["role"] for item in files if item["id"] == row.id)
            if role and role != roles.get(int(row.id), original):
                roles[int(row.id)] = role
                changed = True
            if role and role != original:
                fixes.setdefault(int(row.id), {})["role"] = role
    if changed:
        st.rerun()

    selected = [int(file_id) for frame in chosen if not frame.empty
                for file_id in frame.loc[frame["incluir"].astype(bool), "id"]]
    st.caption("{} de {} arquivo(s) marcados para gravar.".format(len(selected), len(files)))
    record, discard = st.columns(2)
    if record.button("Gravar no projeto", type="primary", key=key + "_apply", disabled=not selected,
                     width="stretch"):
        try:
            with st.spinner("Gravando a importação (as tabelas grandes levam alguns segundos)..."):
                report = sensorial_imports.apply_batch(project["id"], batch_id, fixes, selected=selected,
                                                       send_document=sender if send_documents else None)
        except (auth.AuthorizationError, ValueError, OSError) as error:
            st.error(str(error))
        else:
            for name in ("ts_import_preview", key + "_roles"):
                st.session_state.pop(name, None)
            st.session_state["ts_import_done"] = report
            st.rerun()
    with discard:
        _discard_controls(project, batch)
    st.divider()
