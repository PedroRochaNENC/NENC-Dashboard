"""
Prosódia — Base de Conhecimento.

Gerenciamento do vector store OpenAI para o módulo de Prosódia: criação,
upload de documentos, listagem, exclusão e teste de busca.
"""

import streamlit as st
from utils import auth

auth.require_module("prosodia")

from utils.ai_provider import (
    get_openai_client,
    get_prosodia_vector_store_id,
    save_prosodia_vector_store_id,
)

st.title("📚 Base de Conhecimento — NencLex")

client = get_openai_client()

if client is None:
    st.error(
        "⚠️ API OpenAI não configurada. "
        "Defina **OPENAI_API_KEY** no arquivo `.env` e reinicie o app."
    )
    st.stop()

# ==================================================================
# Seção 1: Configuração do Vector Store
# ==================================================================
st.subheader("⚙️ Configuração")

vs_id = get_prosodia_vector_store_id()

if vs_id:
    st.success(f"Vector Store ativo: `{vs_id}`")
else:
    st.warning("Nenhum Vector Store configurado para NencLex.")
    if st.button("➕ Criar novo Vector Store", type="primary"):
        with st.spinner("Criando vector store..."):
            try:
                vs = client.vector_stores.create(name="NENC NencLex KB")
                save_prosodia_vector_store_id(vs.id)
                st.success(f"Vector Store criado: `{vs.id}`")
                st.rerun()
            except Exception as e:
                st.error(f"Erro ao criar vector store: {e}")
    st.stop()

# ==================================================================
# Seção 2: Upload de Documentos
# ==================================================================
st.divider()
st.subheader("📤 Upload de Documentos")
st.markdown(
    "Adicione artigos, relatórios e referências sobre análise de voz, "
    "prosódia, pesquisa qualitativa ou o contexto específico do projeto."
)

uploaded_files = st.file_uploader(
    "Selecione arquivos para adicionar à base",
    type=["pdf", "pptx", "docx", "txt", "csv", "md"],
    accept_multiple_files=True,
    key="pr_kb_upload",
)

if uploaded_files:
    with st.form("pr_upload_form"):
        col1, col2 = st.columns(2)
        with col1:
            tipo = st.selectbox(
                "Tipo de documento",
                ["artigo científico", "relatório", "apresentação", "guia de análise", "outro"],
                key="pr_kb_tipo",
            )
            projeto = st.text_input("Projeto", key="pr_kb_projeto")
        with col2:
            ano = st.number_input("Ano", min_value=2000, max_value=2030, value=2025, key="pr_kb_ano")
            tema = st.text_input("Tema / Área", placeholder="Ex: NencLex, emoção vocal", key="pr_kb_tema")

        submitted = st.form_submit_button("📤 Enviar para a base", type="primary")

        if submitted:
            progress = st.progress(0)
            for i, f in enumerate(uploaded_files):
                with st.spinner(f"Enviando {f.name}..."):
                    try:
                        uploaded = client.files.create(
                            file=(f.name, f.getvalue()),
                            purpose="assistants",
                        )
                        client.vector_stores.files.create(
                            vector_store_id=vs_id,
                            file_id=uploaded.id,
                        )
                        st.success(f"✅ {f.name}")
                    except Exception as e:
                        st.error(f"❌ {f.name}: {e}")
                progress.progress((i + 1) / len(uploaded_files))
            st.rerun()

# ==================================================================
# Seção 3: Documentos na Base
# ==================================================================
st.divider()
st.subheader("📋 Documentos na Base")

try:
    vs_files = client.vector_stores.files.list(vector_store_id=vs_id)
    file_list = list(vs_files)
except Exception as e:
    st.error(f"Erro ao listar arquivos: {e}")
    file_list = []

if file_list:
    st.metric("Total de documentos", len(file_list))

    for vf in file_list:
        col_name, col_status, col_action = st.columns([4, 2, 1])

        try:
            file_info = client.files.retrieve(vf.id)
            filename = file_info.filename
            size_kb = file_info.bytes / 1024 if file_info.bytes else 0
        except Exception:
            filename = vf.id
            size_kb = 0

        with col_name:
            st.text(f"📄 {filename} ({size_kb:.1f} KB)")

        with col_status:
            status = vf.status
            if status == "completed":
                st.success("Pronto")
            elif status == "in_progress":
                st.warning("Processando...")
            else:
                st.error(status)

        with col_action:
            if st.button("🗑️", key=f"pr_del_{vf.id}", help=f"Remover {filename}"):
                try:
                    client.vector_stores.files.delete(
                        vector_store_id=vs_id,
                        file_id=vf.id,
                    )
                    client.files.delete(vf.id)
                    st.rerun()
                except Exception as e:
                    st.error(f"Erro: {e}")
else:
    st.info("Nenhum documento na base. Faça upload acima.")

# ==================================================================
# Seção 4: Testar Busca
# ==================================================================
st.divider()
st.subheader("🔍 Testar Busca")

test_query = st.text_input(
    "Digite uma consulta para testar a busca na base",
    placeholder="Ex: como interpretar variações de pitch em entrevistas qualitativas?",
    key="pr_kb_test_query",
)

if test_query:
    if st.button("Buscar", key="btn_pr_kb_search"):
        with st.spinner("Buscando..."):
            try:
                response = client.responses.create(
                    model="gpt-4.1-mini",
                    input=test_query,
                    tools=[{
                        "type": "file_search",
                        "vector_store_ids": [vs_id],
                    }],
                    max_output_tokens=500,
                )

                for item in response.output:
                    if item.type == "message":
                        for content_block in item.content:
                            if content_block.type == "output_text":
                                st.markdown(content_block.text)
                                for ann in getattr(content_block, "annotations", []):
                                    if ann.type == "file_citation":
                                        st.caption(
                                            f"📎 Fonte: {getattr(ann, 'filename', 'arquivo')}"
                                        )
            except Exception as e:
                st.error(f"Erro na busca: {e}")

# ==================================================================
# Navegação
# ==================================================================
st.divider()
col_nav1, col_nav2 = st.columns(2)

with col_nav1:
    if st.button("⬅️ Voltar para Preparação", width='stretch'):
        st.switch_page("modules/prosodia/preparacao.py")

with col_nav2:
    if st.button("Avançar para Análise ➡️", width='stretch', type="primary"):
        st.switch_page("modules/prosodia/analise.py")
