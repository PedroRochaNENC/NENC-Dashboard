"""
Prosódia — Dados do Projeto.

Formulário de criação/edição de um projeto: nome, contexto e perguntas da entrevista.
As perguntas serão usadas na verificação automática de qualidade de cada áudio.
"""

import io
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime

import streamlit as st

from utils.prosodia_db import (
    init_db,
    create_project,
    get_project,
    update_project,
)
from utils.ai_provider import get_openai_client, get_prosodia_vector_store_id

init_db()


def _decode_text_bytes(data: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return data.decode(enc)
        except Exception:
            continue
    return data.decode("utf-8", errors="ignore")


def _extract_text_from_docx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml_bytes = zf.read("word/document.xml")

    root = ET.fromstring(xml_bytes)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = []
    for p in root.findall(".//w:p", ns):
        texts = [t.text for t in p.findall(".//w:t", ns) if t.text]
        if texts:
            paragraphs.append("".join(texts))
    return "\n".join(paragraphs)


def _extract_briefing_text(uploaded_file) -> tuple[str, str]:
    """
    Retorna (texto_extraido, erro). Em caso de sucesso, erro="".
    """
    if not uploaded_file:
        return "", ""

    filename = str(uploaded_file.name or "briefing")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    data = uploaded_file.getvalue()
    if not data:
        return "", "O arquivo de briefing está vazio."

    try:
        if ext in {"txt", "md", "csv", "json"}:
            return _decode_text_bytes(data).strip(), ""

        if ext == "docx":
            text = _extract_text_from_docx(data).strip()
            if not text:
                return "", "Não foi possível extrair texto do .docx informado."
            return text, ""

        return "", "Formato não suportado. Use .txt, .md, .csv, .json ou .docx."
    except Exception as e:
        return "", f"Erro ao processar briefing: {e}"


def _cap_briefing(text: str, max_chars: int = 20000) -> str:
    text = str(text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[briefing truncado no armazenamento]"


def _slugify(text: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(text or ""))
    return safe.strip("_")[:80] or "projeto"


def _upload_briefing_to_kb(filename: str, content: bytes) -> tuple[bool, str]:
    client = get_openai_client()
    prosodia_vs_id = get_prosodia_vector_store_id()

    if not client:
        return False, "OpenAI não configurado para envio à base de conhecimento."
    if not prosodia_vs_id:
        return False, "PROSODIA_VECTOR_STORE_ID não configurado."

    try:
        uploaded = client.files.create(
            file=(filename, content),
            purpose="assistants",
        )
        client.vector_stores.files.create(
            vector_store_id=prosodia_vs_id,
            file_id=uploaded.id,
        )
        return True, filename
    except Exception as e:
        return False, str(e)

# ------------------------------------------------------------------
# Modo edição vs criação
# ------------------------------------------------------------------
project_id = st.session_state.get("pros_project_id")
editing = project_id is not None
project = get_project(project_id) if editing else {}

st.title("✏️ Editar Projeto" if editing else "➕ Novo Projeto de Prosódia")

# Navegação
nav_col, _ = st.columns([2, 6])
with nav_col:
    if st.button("← Projetos", width='stretch'):
        st.switch_page("modules/prosodia/projetos.py")

st.divider()

# ==================================================================
# Formulário
# ==================================================================
with st.form("form_projeto"):
    st.subheader("📋 Informações do Projeto")

    nome = st.text_input(
        "Nome do Projeto *",
        value=project.get("name", ""),
        placeholder="Ex: Kynetec — Mão de Obra Rural 2026",
    )

    col_a, col_b = st.columns(2)

    with col_a:
        especialidade = st.text_area(
            "Contexto / Área do estudo",
            value=project.get("especialidade", ""),
            placeholder=(
                "Descreva o objetivo da pesquisa, público-alvo "
                "e condições de coleta..."
            ),
            height=120,
        )
        historico = st.text_area(
            "Histórico / Informações adicionais",
            value=project.get("historico", ""),
            placeholder=(
                "Informações sobre a empresa, produto "
                "ou contexto da pesquisa..."
            ),
            height=120,
        )

    with col_b:
        problemas = st.text_area(
            "Problemas / Hipóteses centrais",
            value=project.get("problemas", ""),
            placeholder=(
                "Quais questões centrais devem ser respondidas pela análise?"
            ),
            height=120,
        )

    st.divider()

    st.subheader("❓ Perguntas da Entrevista")
    st.markdown(
        "Liste as perguntas que **devem ser abordadas** em cada entrevista. "
        "O sistema verificará automaticamente a cobertura ao carregar os uploads. "
        "**Uma pergunta por linha.**"
    )

    questions_raw = st.text_area(
        "Perguntas",
        value=project.get("questions", ""),
        placeholder=(
            "Ex:\n"
            "Como você avalia a qualidade do produto?\n"
            "Quais são suas principais dificuldades no campo?\n"
            "Você recomendaria este serviço para outros produtores?"
        ),
        height=200,
        label_visibility="collapsed",
    )

    st.caption("_Deixe em branco para pular a verificação de cobertura de perguntas._")

    st.divider()
    st.subheader("🧾 Briefing do Projeto (contexto para análises)")
    st.markdown(
        "Adicione um documento de **briefing** para enriquecer o contexto das análises de IA "
        "(individual e geral)."
    )

    current_briefing_filename = project.get("briefing_filename", "")
    current_briefing_text = project.get("briefing_text", "")

    briefing_file = st.file_uploader(
        "Documento de Briefing",
        type=["txt", "md", "csv", "json", "docx"],
        help="Formatos aceitos: .txt, .md, .csv, .json, .docx",
    )

    remove_briefing = st.checkbox(
        "Remover briefing atual",
        value=False,
        disabled=not bool(current_briefing_text),
    )

    if current_briefing_filename:
        st.caption(f"Briefing atual: {current_briefing_filename}")
    if current_briefing_text:
        with st.expander("Prévia do briefing atual"):
            preview = current_briefing_text[:1500]
            if len(current_briefing_text) > 1500:
                preview += "\n...[prévia truncada]"
            st.text(preview)

    # ------------------------------------------------------------------
    # Vincular Campanha do WhatsApp (Opcional)
    # ------------------------------------------------------------------
    st.divider()
    st.subheader("📱 Campanha do WhatsApp (Opcional)")
    st.markdown(
        "Vincule este projeto a uma campanha da API de WhatsApp para "
        "sincronizar áudios automaticamente."
    )

    from utils.whatsapp_api_client import is_configured, get_campaigns

    campaign_options = {}  # id -> display label
    if is_configured():
        try:
            campaigns = get_campaigns()
            campaign_options = {
                c["id"]: f"{c['name']} (ID {c['id']} — {c['status']})"
                for c in campaigns
            }
        except Exception as e:
            st.warning(f"Não foi possível buscar campanhas: {e}")
    else:
        st.caption(
            "⚠️ API de WhatsApp não configurada. "
            "Configure URL e chave na tela de Projetos para habilitar."
        )

    current_campaign_id = project.get("whatsapp_campaign_id")

    if campaign_options:
        options_list = [None] + list(campaign_options.keys())
        labels = ["— Nenhuma —"] + list(campaign_options.values())
        default_idx = 0
        if current_campaign_id in campaign_options:
            default_idx = options_list.index(current_campaign_id)

        selected_campaign = st.selectbox(
            "Campanha vinculada",
            options=options_list,
            index=default_idx,
            format_func=lambda x: labels[options_list.index(x)],
            key="prep_campaign_select",
        )
    else:
        selected_campaign = current_campaign_id
        if current_campaign_id:
            st.caption(f"Campanha vinculada atual: ID {current_campaign_id}")

    submitted = st.form_submit_button(
        "💾 Salvar e ir para Entrevistas",
        type="primary",
        width='stretch',
    )

if submitted:
    if not nome.strip():
        st.error("O **Nome do Projeto** é obrigatório.")
    else:
        briefing_filename = current_briefing_filename
        briefing_text = current_briefing_text
        uploaded_briefing_name = ""
        uploaded_briefing_bytes = b""

        if remove_briefing:
            briefing_filename = ""
            briefing_text = ""

        if briefing_file:
            extracted_text, err = _extract_briefing_text(briefing_file)
            if err:
                st.error(err)
                st.stop()
            briefing_filename = briefing_file.name
            briefing_text = _cap_briefing(extracted_text)
            uploaded_briefing_name = briefing_file.name
            uploaded_briefing_bytes = briefing_file.getvalue()

        saved_project_id = project_id

        if editing:
            update_project(
                project_id,
                name=nome.strip(),
                especialidade=especialidade.strip(),
                historico=historico.strip(),
                problemas=problemas.strip(),
                questions=questions_raw.strip(),
                briefing_filename=briefing_filename,
                briefing_text=briefing_text,
                whatsapp_campaign_id=selected_campaign,
            )
            st.success("Projeto atualizado!")
        else:
            new_id = create_project(
                name=nome.strip(),
                especialidade=especialidade.strip(),
                historico=historico.strip(),
                problemas=problemas.strip(),
                questions=questions_raw.strip(),
                briefing_filename=briefing_filename,
                briefing_text=briefing_text,
                whatsapp_campaign_id=selected_campaign,
            )
            st.session_state["pros_project_id"] = new_id
            saved_project_id = new_id
            st.success("Projeto criado!")

        if uploaded_briefing_name and uploaded_briefing_bytes:
            now_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
            kb_filename = f"briefing_{_slugify(nome.strip())}_{now_tag}_{uploaded_briefing_name}"
            kb_ok, kb_msg = _upload_briefing_to_kb(kb_filename, uploaded_briefing_bytes)
            if kb_ok:
                st.caption(f"Briefing enviado para a base de conhecimento: {kb_msg}")
            else:
                st.warning(
                    f"Projeto salvo, mas não foi possível enviar o briefing para a base: {kb_msg}"
                )
                st.stop()

        if saved_project_id:
            st.session_state["pros_project_id"] = saved_project_id

        st.switch_page("modules/prosodia/entrevistas.py")

