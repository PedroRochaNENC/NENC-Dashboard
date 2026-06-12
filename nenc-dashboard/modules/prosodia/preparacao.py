"""
Prosódia — Dados do Projeto.

Formulário de criação/edição de um projeto: nome, contexto e perguntas da entrevista.
As perguntas serão usadas na verificação automática de qualidade de cada áudio.
"""

import streamlit as st

from utils.prosodia_db import (
    init_db,
    create_project,
    get_project,
    update_project,
)

init_db()

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

    submitted = st.form_submit_button(
        "💾 Salvar e ir para Entrevistas",
        type="primary",
        width='stretch',
    )

if submitted:
    if not nome.strip():
        st.error("O **Nome do Projeto** é obrigatório.")
    else:
        if editing:
            update_project(
                project_id,
                name=nome.strip(),
                especialidade=especialidade.strip(),
                historico=historico.strip(),
                problemas=problemas.strip(),
                questions=questions_raw.strip(),
            )
            st.success("Projeto atualizado!")
        else:
            new_id = create_project(
                name=nome.strip(),
                especialidade=especialidade.strip(),
                historico=historico.strip(),
                problemas=problemas.strip(),
                questions=questions_raw.strip(),
            )
            st.session_state["pros_project_id"] = new_id
            st.success("Projeto criado!")

        st.switch_page("modules/prosodia/entrevistas.py")

