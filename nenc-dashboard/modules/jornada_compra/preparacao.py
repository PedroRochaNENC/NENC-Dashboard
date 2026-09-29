"""
Jornada de Compra — Novo projeto / Dados do Projeto.

Sem projeto aberto, é o formulário de criação; com projeto aberto, reúne o que
define a análise: o contexto e o briefing (que também vão para a base de
conhecimento, marcados como material deste projeto).
"""

from datetime import datetime

import streamlit as st
from utils import auth, ui
from utils.icons import page_title

user = auth.require_module("jornada_compra")
pode_editar = auth.can_write(user)

from utils import jornada_db
from utils.ai_provider import (
    add_document_to_vector_store,
    get_openai_client,
    get_vector_store_id,
)
from utils.briefing import BRIEFING_EXTENSIONS, cap_text, extract_briefing_text
from utils.kb_attributes import project_document

jornada_db.init_db()


def _slugify(text: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(text or ""))
    return safe.strip("_")[:80] or "projeto"


def _lines(text: str) -> list:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _upload_briefing_to_kb(filename: str, content: bytes, project_id: int) -> tuple:
    """Manda o briefing para a base da Jornada, marcado como material do projeto."""
    if auth.active_organization_id(user) == 0:
        return False, (
            "em \"Todas as organizações\" a base resolvida seria a da sua "
            "organização, não a do projeto"
        )
    client = get_openai_client()
    vector_store_id = get_vector_store_id()
    if not client:
        return False, "OpenAI não configurado."
    if not vector_store_id:
        return False, "a base de conhecimento da Jornada não está configurada nesta organização"
    try:
        add_document_to_vector_store(
            vector_store_id,
            filename,
            content,
            project_document("jornada_compra", project_id, tipo="briefing"),
            wait=False,
        )
        return True, filename
    except Exception as error:
        return False, str(error)


# ------------------------------------------------------------------
# Modo edição vs criação
# ------------------------------------------------------------------
project_id = st.session_state.get("jc_project_id")
project = jornada_db.get_project(project_id) if project_id else None
if project_id and project is None:
    st.session_state.pop("jc_project_id", None)
    project_id = None
editing = project is not None
project = project or {}

if editing:
    # Ao editar, a autoria entra na conta; ao criar, basta o papel.
    pode_editar = jornada_db.user_can_modify_project(project, user)

ui.inject_theme()
ui.breadcrumb(
    "Jornada de Compra",
    project.get("name", "") if editing else "Projetos",
    "Dados do Projeto" if editing else "Novo",
)
page_title(
    "note-pencil" if editing else "plus",
    "Dados do Projeto" if editing else "Novo Projeto",
    project.get("name") if editing else "Contexto, perguntas e marcas do estudo.",
)

if not pode_editar:
    if editing and auth.can_write(user):
        st.info("Este projeto foi criado por outro administrador da organização.")
    else:
        st.info("Sua conta tem acesso somente de leitura à Jornada de Compra.")


def _render_context() -> None:
    """Contexto do estudo: vira briefing da IA e define as marcas analisadas."""

    nome = st.text_input(
        "Nome do Projeto *",
        value=project.get("name", ""),
        placeholder="Ex: Jornada de compra da categoria — lojas farma e atacarejo",
        disabled=not pode_editar,
    )
    categoria = st.text_input(
        "Categoria",
        value=project.get("categoria") or "",
        placeholder="Ex: Absorventes",
        disabled=not pode_editar,
    )

    col_a, col_b = st.columns(2)
    with col_a:
        especialidade = st.text_area(
            "Contexto / objetivo do estudo",
            value=project.get("especialidade") or "",
            placeholder=(
                "Cliente, categoria, lojas, tarefas (jornada livre, estimulada, "
                "embalagens) e o que a marca quer decidir com o estudo."
            ),
            height=140,
            disabled=not pode_editar,
        )
        historico = st.text_area(
            "Histórico / informações adicionais",
            value=project.get("historico") or "",
            placeholder="Mudanças de embalagem, planograma, promoções no período da coleta...",
            height=140,
            disabled=not pode_editar,
        )
    with col_b:
        problemas = st.text_area(
            "Perguntas centrais (uma por linha)",
            value=project.get("problemas") or "",
            placeholder=(
                "A marca foco é vista e examinada frente às concorrentes?\n"
                "Quais elementos da embalagem são vistos, por perfil?\n"
                "Há diferença entre canais e perfis de shopper?\n"
                "Como o shopper navega e decide (linhas, preço)?"
            ),
            height=140,
            disabled=not pode_editar,
        )
        marcas = st.text_area(
            "Marcas do estudo (uma por linha)",
            value=project.get("marcas") or "",
            placeholder="Marca A\nMarca B\nMarca C",
            height=140,
            help=(
                "Usadas para reconhecer a marca de cada AOI pelo nome. Marcas que não "
                "estiverem aqui ainda são sugeridas a partir dos dados."
            ),
            disabled=not pode_editar,
        )

    marca_options = [""] + _lines(marcas)
    saved_focus = project.get("marca_foco") or ""
    if saved_focus and saved_focus not in marca_options:
        marca_options.append(saved_focus)
    marca_foco = st.selectbox(
        "Marca foco (cliente)",
        marca_options,
        index=marca_options.index(saved_focus) if saved_focus in marca_options else 0,
        format_func=lambda value: value or "— nenhuma —",
        help="Destacada nos gráficos, nos achados e no relatório.",
        disabled=not pode_editar,
    )

    st.divider()
    st.subheader("Briefing do projeto")
    st.markdown(
        "Documento de contexto para a análise de IA. O texto fica no projeto e o "
        "arquivo vai para a base de conhecimento da Jornada, visível só para este projeto."
    )
    current_filename = project.get("briefing_filename") or ""
    current_text = project.get("briefing_text") or ""
    briefing_file = st.file_uploader(
        "Documento de briefing",
        type=list(BRIEFING_EXTENSIONS),
        disabled=not pode_editar,
    )
    remove_briefing = st.checkbox(
        "Remover briefing atual", value=False, disabled=not (pode_editar and current_text)
    )
    if current_filename:
        st.caption("Briefing atual: {}".format(current_filename))
    if current_text:
        with st.expander("Prévia do briefing atual"):
            st.text(current_text[:1500] + ("\n...[prévia truncada]" if len(current_text) > 1500 else ""))

    label = "Salvar alterações" if editing else "Criar projeto"
    if not st.button(label, type="primary", width="stretch", disabled=not pode_editar):
        return

    if not nome.strip():
        st.error("O **Nome do Projeto** é obrigatório.")
        return

    briefing_filename, briefing_text = current_filename, current_text
    uploaded_name, uploaded_bytes = "", b""
    if remove_briefing:
        briefing_filename, briefing_text = "", ""
    if briefing_file:
        extracted, error = extract_briefing_text(briefing_file.name, briefing_file.getvalue())
        if error:
            st.error(error)
            return
        briefing_filename = briefing_file.name
        briefing_text = cap_text(extracted)
        uploaded_name, uploaded_bytes = briefing_file.name, briefing_file.getvalue()

    fields = {
        "categoria": categoria,
        "especialidade": especialidade,
        "historico": historico,
        "problemas": problemas,
        "marcas": "\n".join(_lines(marcas)),
        "marca_foco": marca_foco,
        "briefing_filename": briefing_filename,
        "briefing_text": briefing_text,
    }
    try:
        if editing:
            jornada_db.update_project(project["id"], name=nome, **fields)
            saved_id = project["id"]
        else:
            saved_id = jornada_db.create_project(nome, **fields)
    except (auth.AuthorizationError, ValueError) as error:
        st.error(str(error))
        return

    if uploaded_name:
        kb_name = "briefing_jc_{}_{}_{}".format(
            _slugify(nome), datetime.now().strftime("%Y%m%d_%H%M%S"), uploaded_name
        )
        ok, message = _upload_briefing_to_kb(kb_name, uploaded_bytes, saved_id)
        if ok:
            st.toast("Briefing enviado para a base de conhecimento.")
        else:
            st.warning("Projeto salvo, mas o briefing não foi para a base: {}.".format(message))

    st.session_state["jc_project_id"] = saved_id
    if editing:
        st.toast("Projeto atualizado.")
        st.rerun()
    # Num projeto recém-criado o menu desta execução foi montado sem projeto:
    # as páginas dele só existem no rerun seguinte.
    st.session_state["_navigate_to"] = (
        "modules/jornada_compra/uploads.py"
        if auth.can_write(user)
        else "modules/jornada_compra/analise_geral.py"
    )
    st.rerun()


_render_context()
