"""
Jornada de Compra — Preparação de Dados.

Upload de arquivos de eye-tracking + formulário de contexto e briefing do projeto.
"""

import streamlit as st
from utils import auth

auth.require_module("jornada_compra")

from io import BytesIO
from pptx import Presentation
import pandas as pd

from utils import jornada_db
from utils.jornada_loader import (
    load_jornada_from_upload,
    get_jornada_summary,
    get_jornada_participants,
    get_jornada_aois,
    get_jornada_marcas,
)

jornada_db.init_db()

st.title("📋 Dados do Projeto — Jornada de Compra")

# ------------------------------------------------------------------
# Verificação de Projeto Ativo
# ------------------------------------------------------------------
project_id = st.session_state.get("jc_project_id")

if not project_id:
    st.warning("⚠️ Nenhum projeto está selecionado no momento.")
    c_btn1, c_btn2 = st.columns([1, 2])
    with c_btn1:
        if st.button("🎙️ Ver Lista de Projetos", type="primary", use_container_width=True):
            st.switch_page("modules/jornada_compra/projetos.py")
    st.divider()

    # Form para criar projeto rápido
    with st.expander("➕ Criar Novo Projeto Rápido", expanded=True):
        quick_name = st.text_input("Nome do Projeto", placeholder="Ex: Estudo Gôndola Shampoos 2026")
        quick_cat = st.text_input("Categoria do Produto", placeholder="Ex: Higiene Pessoal / Shampoos")
        if st.button("Criar e Continuar", type="primary"):
            if quick_name.strip():
                new_id = jornada_db.create_project(name=quick_name, categoria=quick_cat)
                st.session_state["jc_project_id"] = new_id
                st.success(f"Projeto '{quick_name}' criado com sucesso!")
                st.rerun()
            else:
                st.error("Digite o nome do projeto.")
    st.stop()

# Carregar projeto do DB
project = jornada_db.get_project(project_id)
if not project:
    st.error("Projeto não encontrado no banco de dados.")
    if st.button("Voltar aos Projetos"):
        st.session_state.pop("jc_project_id", None)
        st.switch_page("modules/jornada_compra/projetos.py")
    st.stop()

st.info(f"📂 **Projeto Ativo:** `{project['name']}` | **Categoria:** `{project.get('categoria') or 'Geral'}`")

# Helper para PPTX
def _extract_pptx_text(file_bytes: bytes) -> str:
    """Extrai todo o texto de um arquivo .pptx."""
    prs = Presentation(BytesIO(file_bytes))
    slides_text = []
    for i, slide in enumerate(prs.slides, 1):
        parts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    text = paragraph.text.strip()
                    if text:
                        parts.append(text)
            if shape.has_table:
                table = shape.table
                for row in table.rows:
                    row_text = " | ".join(cell.text.strip() for cell in row.cells)
                    if row_text.replace(" | ", "").strip():
                        parts.append(row_text)
        if parts:
            slides_text.append(f"--- Slide {i} ---\n" + "\n".join(parts))
    return "\n\n".join(slides_text)

# ------------------------------------------------------------------
# Carregar dados já salvos no DB do Projeto
# ------------------------------------------------------------------
data = st.session_state.get("jc_data")
if not data:
    data = jornada_db.get_dataset(project_id)
    # Tentar carregar entrevistas salvas no DB
    interviews_db = jornada_db.get_interviews(project_id)
    if interviews_db:
        data["entrevistas"] = pd.DataFrame(interviews_db)
    st.session_state["jc_data"] = data

# ==================================================================
# Seção 1: Contexto e Briefing do Projeto
# ==================================================================
with st.expander("📝 Briefing & Contexto do Projeto", expanded=True):
    with st.form("jc_briefing_form"):
        c1, c2 = st.columns(2)
        with c1:
            name_val = st.text_input("Nome do Projeto", value=project["name"])
            categoria_val = st.text_input("Categoria / Segmento", value=project.get("categoria") or "")
            marcas_val = st.text_input("Marcas no Estudo", value=project.get("marcas") or "", placeholder="Ex: Marca A, Marca B, Marca C")
        with c2:
            questions_val = st.text_area("Perguntas de Pesquisa / Objetivos", value=project.get("questions") or "", height=110)

        historico_val = st.text_area(
            "Histórico do Problema / Contexto do PDV",
            value=project.get("historico") or "",
            placeholder="Descreva o contexto do ponto de venda, gôndola ou embalagens sob teste...",
            height=90,
        )
        problemas_val = st.text_area(
            "Problemas Centrais a Responder",
            value=project.get("problemas") or "",
            placeholder="Ex: 1) Qual marca atrai mais atenção? 2) Quais embalagens convertem melhor?",
            height=90,
        )

        relatorio_file = st.file_uploader(
            "📎 Anexar Relatório PPTX (extração automática de briefing)",
            type=["pptx"],
            key="jc_relatorio_pptx",
        )

        briefing_text_val = project.get("briefing_text") or ""
        if relatorio_file is not None:
            briefing_text_val = _extract_pptx_text(relatorio_file.getvalue())
            st.success("Texto extraído com sucesso do relatório PPTX!")

        if st.form_submit_button("💾 Salvar Briefing do Projeto", type="primary"):
            jornada_db.update_project(
                project_id,
                name=name_val,
                categoria=categoria_val,
                historico=historico_val,
                problemas=problemas_val,
                questions=questions_val,
                marcas=marcas_val,
                briefing_text=briefing_text_val,
            )
            st.success("Briefing do projeto atualizado com sucesso!")
            st.rerun()

# ==================================================================
# Seção 2: Upload de Dados de Eye-Tracking
# ==================================================================
st.divider()
st.subheader("📤 Upload de Dados de Eye-Tracking")
st.markdown("Envie ou atualize as planilhas de eye-tracking do projeto.")

col1, col2, col3 = st.columns(3)

with col1:
    tabelas_file = st.file_uploader(
        "Tabelas (per-participante × AOI)",
        type=["csv", "xlsx"],
        key="jc_tabelas",
    )
    por_marca_file = st.file_uploader(
        "Por Marca (agrupado por marca)",
        type=["csv", "xlsx"],
        key="jc_por_marca",
    )

with col2:
    medias_file = st.file_uploader(
        "Médias (médias por AOI)",
        type=["csv", "xlsx"],
        key="jc_medias",
    )
    visual_share_file = st.file_uploader(
        "Visual Share (share por marca)",
        type=["csv", "xlsx"],
        key="jc_visual_share",
    )

with col3:
    anova_file = st.file_uploader(
        "ANOVA (testes estatísticos)",
        type=["csv", "xlsx"],
        key="jc_anova",
    )
    consolidado_file = st.file_uploader(
        "Consolidado (.xlsx)",
        type=["xlsx"],
        key="jc_consolidado",
    )

entrevistas_file = st.file_uploader(
    "🎙️ Entrevistas (transcrições qualitativas do PDV)",
    type=["csv", "xlsx"],
    key="jc_entrevistas",
    help="Planilha com colunas: identificacao/participante e texto",
)

any_file = any([
    tabelas_file, por_marca_file, medias_file,
    visual_share_file, anova_file, consolidado_file,
    entrevistas_file,
])

if any_file:
    uploaded_data = load_jornada_from_upload(
        tabelas_file=tabelas_file,
        por_marca_file=por_marca_file,
        medias_file=medias_file,
        visual_share_file=visual_share_file,
        anova_file=anova_file,
        consolidado_file=consolidado_file,
        entrevistas_file=entrevistas_file,
    )
    
    # Mesclar com dados existentes
    current_data = st.session_state.get("jc_data", {})
    for k, v in uploaded_data.items():
        if k != "_errors":
            current_data[k] = v

    st.session_state["jc_data"] = current_data

    # Persistir no banco de dados SQLite
    jornada_db.save_dataset(
        project_id,
        tabelas=current_data.get("tabelas"),
        por_marca=current_data.get("por_marca"),
        medias=current_data.get("medias"),
        visual_share=current_data.get("visual_share"),
    )

    # Processar entrevistas se houver
    if entrevistas_file is not None and "entrevistas" in uploaded_data:
        ent_df = uploaded_data["entrevistas"]
        id_col = next((c for c in ent_df.columns if c.lower() in ("identificacao", "identificação", "participante", "arquivo")), ent_df.columns[0])
        for idx, row in ent_df.iterrows():
            part_id = str(row.get(id_col, f"P_{idx+1}"))
            txt = str(row.get("texto", str(row.to_dict())))
            jornada_db.save_interview(project_id, titulo=f"Entrevista {part_id}", participante_id=part_id, texto=txt)

    st.success("✅ Arquivos carregados e salvos no banco de dados do projeto com sucesso!")

# ==================================================================
# Seção 3: Resumo e Visualização dos Dados Carregados
# ==================================================================
data = st.session_state.get("jc_data", {})
loaded_keys = [k for k in data if k != "_errors" and isinstance(data[k], pd.DataFrame) and not data[k].empty]

if loaded_keys:
    st.divider()
    st.subheader("✅ Resumo dos Dados do Projeto")

    summary = get_jornada_summary(data)
    n_entrevistas = len(jornada_db.get_interviews(project_id))

    cols = st.columns(5)
    cols[0].metric("Arquivos carregados", len(loaded_keys))
    cols[1].metric("Participantes", summary.get("n_participantes", 0))
    cols[2].metric("AOIs", summary.get("n_aois", 0))
    cols[3].metric("Marcas", summary.get("n_marcas", 0))
    cols[4].metric("Entrevistas PDV", n_entrevistas)

    participants = get_jornada_participants(data)
    marcas = get_jornada_marcas(data)

    if participants:
        with st.expander(f"Participantes ({len(participants)})"):
            st.write(", ".join(participants))

    if marcas:
        with st.expander(f"Marcas ({len(marcas)})"):
            st.write(", ".join(marcas))

    st.subheader("📋 Pré-visualização das Tabelas Salvas")
    labels = {
        "tabelas": "Tabelas (per-participante)",
        "por_marca": "Por Marca",
        "medias": "Médias",
        "visual_share": "Visual Share",
    }
    for key in loaded_keys:
        df = data[key]
        label = labels.get(key, key)
        with st.expander(f"{label} — {len(df)} linhas"):
            st.dataframe(df.head(50), use_container_width=True)
else:
    st.info("Nenhum dado de eye-tracking salvo para este projeto ainda. Envie os arquivos acima.")

# ==================================================================
# Navegação
# ==================================================================
st.divider()
col_nav1, col_nav2 = st.columns(2)

with col_nav1:
    if st.button("⬅️ Voltar aos Projetos", use_container_width=True):
        st.switch_page("modules/jornada_compra/projetos.py")

with col_nav2:
    if st.button("Avançar para Análise ➡️", use_container_width=True, type="primary"):
        st.switch_page("modules/jornada_compra/analise.py")
