"""
Teste Sensorial — Uploads.

A pasta do estudo chega pelo script `scripts/nenc_enviar.py --modulo
teste_sensorial` e fica em "Importações pendentes" (utils/sensorial_import_
review.py) até alguém conferir e gravar. Arquivos avulsos e pequenos (planilha
de perfil, registro de campo, um documento) vão por esta tela: cada um é lido
na hora e aparece numa prévia, só com o resumo, antes de ser gravado.

No fim, os arquivos do projeto: ativar, desativar (uma rodada antiga do
pipeline fica guardada sem entrar na análise) ou excluir.
"""

import hashlib
from pathlib import PurePosixPath

import pandas as pd
import streamlit as st
from utils import auth, ui
from utils.icons import page_title

user = auth.require_module_write("teste_sensorial")

from utils import sensorial_db, sensorial_import_review, sensorial_imports, sensorial_ingest
from utils.project_ui import active_project
from utils.sensorial_import_review import ROLE_LABELS

sensorial_db.init_db()
project = active_project(sensorial_db, "ts_project_id", "modules/teste_sensorial/projetos.py")
project_id = project["id"]
MANUAL_LIMIT = 25 * 1024 * 1024
UPLOAD_TYPES = ["csv", "gz", "xlsx", "xls", "json", "png", "jpg", "jpeg", "webp", "pdf", "docx", "pptx", "txt", "md"]


def _nonce(key: str) -> int:
    return st.session_state.setdefault(key, 0)


def _parsed(name: str, content: bytes, role: str):
    """Leitura cacheada na sessão: a prévia reexecuta a cada clique."""
    cache = st.session_state.setdefault("ts_upload_parsed", {})
    key = (name, hashlib.sha256(content).hexdigest(), role)
    if key not in cache:
        cache[key] = sensorial_ingest.parse_file(name, content, role=role or None)
    return cache[key]


ui.inject_theme()
ui.breadcrumb("Teste Sensorial", project["name"], "Uploads")
page_title("upload-simple", "Uploads", "Saídas do pipeline, BASE LIMPA, registro de campo, perfil e documentos.")

sensorial_import_review.render(project, user)

# ==================================================================
# A pasta do estudo, pelo script
# ==================================================================
st.subheader("Enviar a pasta do estudo")
st.markdown(
    "No computador que tem a pasta do estudo, dentro de `nenc-dashboard`, rode primeiro com `--simular` para ver o "
    "que vai e o que fica de fora; depois sem ele. O token da organização vem de `NENC_IMPORT_TOKEN` ou de "
    "`--token-arquivo` (fora da pasta compartilhada).")
st.code('..\\.venv\\Scripts\\python scripts\\nenc_enviar.py "X:\\Cliente\\Estudo" --modulo teste_sensorial '
        '--projeto "{}" --simular'.format(project["name"]), language="bash")
with st.expander("O que o script envia e o que deixa de fora"):
    st.markdown(
        "- **Pipeline (2.2)**: a rodada mais nova de cada modalidade que não falhou — indicadores e PSD por janela, "
        "PSD médio, qualidade do EEG, topomapas, métricas e qualidade dos periféricos, tentativas do teste de "
        "associação e o manifesto de cada rodada. Tabela acima de 5 MB vai comprimida.\n"
        "- **Inventário** mais recente das sessões.\n"
        "- **BASE LIMPA (2.3)**: de cada camada, só as colunas-chave das janelas ou tentativas mantidas — as "
        "colunas com nome de participante nem são lidas.\n"
        "- **Registro de campo** da qualidade do sinal por canal, **briefing**, **relatório final** (só a versão "
        "mais recente), **estímulos** e **literatura**.\n"
        "- **Fica de fora**: recrutamento, fotos e vídeos das coletas, as cópias dos dados brutos (2.0 e 2.1), "
        "rodadas antigas, prosódia e transcrições.")

# ==================================================================
# Arquivos avulsos
# ==================================================================
st.subheader("Arquivos avulsos")
st.markdown("Para um arquivo pequeno de cada vez: a planilha de perfil (código e atributos como sexo e grupo), o "
            "registro de campo, um documento. Acima de 25 MB, use o script.")
upload_key = "ts_upload_{}_{}".format(project_id, _nonce("ts_upload_nonce"))
uploaded = st.file_uploader("Arquivos", type=UPLOAD_TYPES, accept_multiple_files=True, key=upload_key,
                            label_visibility="collapsed")
if uploaded:
    rows, ready = [], []
    for index, upload in enumerate(uploaded):
        content = upload.getvalue()
        if len(content) > MANUAL_LIMIT:
            rows.append({"arquivo": upload.name, "papel": "", "resumo": "", "avisos": "maior que 25 MB: use o script"})
            continue
        detected = sensorial_ingest.detect_role(upload.name) or ""
        options = [""] + list(ROLE_LABELS)
        role = st.selectbox(
            "Papel de {}".format(upload.name), options, index=options.index(detected),
            format_func=lambda key: ROLE_LABELS.get(key, "— escolha —"), key="{}_role_{}".format(upload_key, index))
        if not role:
            rows.append({"arquivo": upload.name, "papel": "", "resumo": "", "avisos": "escolha o papel"})
            continue
        parsed = _parsed(upload.name, content, role)
        resumo = parsed.meta.get("resumo") or {}
        described = ", ".join(part for part in (
            "{} linhas".format(resumo["linhas"]) if resumo.get("linhas") else "",
            "{} sessões".format(resumo["sessoes"]) if resumo.get("sessoes") else "",
            "{} participantes".format(len(resumo["participantes"])) if resumo.get("participantes") else "",
            ", ".join(parsed.meta.get("atributos") or []),
        ) if part)
        rows.append({"arquivo": upload.name, "papel": ROLE_LABELS.get(role, role), "resumo": described,
                     "avisos": "; ".join(issue["message"] for issue in parsed.issues)})
        if parsed.ok:
            ready.append((upload.name, content, parsed))
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if st.button("Gravar {} arquivo(s)".format(len(ready)), type="primary", disabled=not ready,
                 key="ts_upload_save"):
        totals = {"files": 0, "duplicates": 0, "participants": 0, "skipped": [], "warnings": []}
        try:
            for name, content, parsed in ready:
                result = sensorial_imports.record_upload(project_id, name, content, parsed)
                totals["files"] += len(result["added"])
                totals["duplicates"] += len(result["duplicates"])
                totals["participants"] += result["participants"]
        except (auth.AuthorizationError, ValueError, OSError) as error:
            st.error(str(error))
        else:
            st.session_state["ts_import_done"] = totals
            st.session_state["ts_upload_nonce"] = _nonce("ts_upload_nonce") + 1
            st.session_state.pop("ts_upload_parsed", None)
            st.rerun()

# ==================================================================
# Arquivos do projeto
# ==================================================================
st.subheader("Arquivos do projeto")
files = sensorial_db.list_files(project_id)
if not files:
    st.info("Nenhum arquivo gravado ainda.")
else:
    table = pd.DataFrame([{
        "id": item["id"],
        "arquivo": PurePosixPath(item["rel_path"] or item["filename"]).name,
        "papel": ROLE_LABELS.get(item["role"], item["role"]),
        "rodada": item.get("run_id") or "",
        "ativo": item["is_active"],
        "MB": round(item["size_bytes"] / 1024 ** 2, 1),
        "gravado em": str(item["created_at"] or "")[:16],
    } for item in files])
    roles = st.multiselect("Papéis", sorted(table["papel"].unique()), key="ts_files_roles",
                           placeholder="Todos os papéis")
    view = table[table["papel"].isin(roles)] if roles else table
    st.dataframe(view.drop(columns=["id"]), hide_index=True, width="stretch")
    labels = {row.id: "{} · {} · {}".format(row.arquivo, row.papel, row.rodada or "sem rodada")
              for row in view.itertuples()}
    chosen = st.selectbox("Arquivo", [None] + list(labels), format_func=lambda key: labels.get(key, "— escolha —"),
                          key="ts_files_choice")
    if chosen:
        current = next(item for item in files if item["id"] == chosen)
        toggle, remove = st.columns(2)
        if toggle.button("Desativar" if current["is_active"] else "Ativar", key="ts_file_toggle", width="stretch"):
            sensorial_db.set_file_active(project_id, chosen, not current["is_active"])
            st.rerun()
        confirm = "ts_file_del_{}".format(chosen)
        if not st.session_state.get(confirm):
            if remove.button("Excluir", key="ts_file_del_ask", width="stretch"):
                st.session_state[confirm] = True
                st.rerun()
        else:
            st.warning("Excluir **{}**? O original e a tabela saem do servidor.".format(labels[chosen]))
            yes, no = st.columns(2)
            if yes.button("Confirmar exclusão", key="ts_file_del_yes", width="stretch"):
                sensorial_db.delete_file(project_id, chosen)
                st.session_state.pop(confirm, None)
                st.rerun()
            if no.button("Cancelar", key="ts_file_del_no", width="stretch"):
                st.session_state.pop(confirm, None)
                st.rerun()
