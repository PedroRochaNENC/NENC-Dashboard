"""
Teste Sensorial — Base de Conhecimento.

Gerenciamento do vector store OpenAI por projeto: upload de documentos de produto,
listagem, exclusão e busca RAG de laudos e pesquisas sensoriais.
"""

import streamlit as st
from utils import auth, ui
from utils.icons import page_title

auth.require_module("teste_sensorial")

from utils import teste_sensorial_db
from utils.ai_provider import get_openai_client

teste_sensorial_db.init_db()

ui.inject_theme()
ui.breadcrumb("Teste Sensorial", "Base de Conhecimento")
page_title(
    "books",
    "Base de Conhecimento",
    "Documentos usados como contexto pela IA.",
)

project_id = st.session_state.get("ts_project_id")

if not project_id:
    st.warning("Nenhum projeto selecionado. Selecione ou crie um projeto primeiro.")
    if st.button("Ir para Projetos", type="primary"):
        st.switch_page("modules/teste_sensorial/projetos.py")
    st.stop()

project = teste_sensorial_db.get_project(project_id)
if not project:
    st.error("Projeto não encontrado.")
    st.stop()

st.caption(f"Projeto Ativo: **{project['name']}** | Produto: **{project.get('produto_estimulo') or 'N/A'}**")

client = get_openai_client()

if client is None:
    st.error(
        "API OpenAI não configurada. "
        "Defina **OPENAI_API_KEY** no arquivo `.env` e reinicie o app."
    )
    st.stop()

# ==================================================================
# Seção 1: Configuração do Vector Store
# ==================================================================
st.subheader("Vector Store do Projeto")

vs_id = project.get("vector_store_id")

if vs_id:
    st.success(f"Vector Store ativo para o projeto: `{vs_id}`")
else:
    st.warning("Este projeto ainda não possui um Vector Store para busca RAG.")
    if st.button("Criar Vector Store para este Projeto", type="primary"):
        with st.spinner("Criando vector store..."):
            try:
                vs = client.vector_stores.create(name=f"TS - {project['name']}")
                teste_sensorial_db.update_project(project_id, vector_store_id=vs.id)
                st.success(f"Vector Store criado com sucesso: `{vs.id}`")
                st.rerun()
            except Exception as e:
                st.error(f"Erro ao criar vector store: {e}")
    st.stop()

# ==================================================================
# Seção 2: Upload de Documentos
# ==================================================================
st.divider()
st.subheader("Upload de Documentos de Produto / Laudos")

uploaded_files = st.file_uploader(
    "Selecione laudos sensoriais, pesquisas ou fichas técnicas",
    type=["pdf", "pptx", "docx", "txt", "csv", "md"],
    accept_multiple_files=True,
    key="ts_kb_upload",
)

if uploaded_files:
    if st.button("Indexar Documentos na Base do Projeto", type="primary"):
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
                    st.success(f"{f.name} indexado")
                except Exception as e:
                    st.error(f"Erro ao enviar {f.name}: {e}")
            progress.progress((i + 1) / len(uploaded_files))
        st.rerun()

# ==================================================================
# Seção 3: Documentos na Base
# ==================================================================
st.divider()
st.subheader("Documentos Indexados no Projeto")

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
            st.text(f"{filename} ({size_kb:.1f} KB)")

        with col_status:
            status = vf.status
            if status == "completed":
                st.success("Pronto")
            elif status == "in_progress":
                st.warning("Processando...")
            else:
                st.error(status)

        with col_action:
            if st.button("🗑️", key=f"del_ts_vs_{vf.id}", help=f"Remover {filename}"):
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
    st.info("Nenhum documento indexado. Faça upload acima.")

# ==================================================================
# Seção 4: Testar Busca RAG
# ==================================================================
st.divider()
st.subheader("Testar Busca RAG")

test_query = st.text_input(
    "Digite uma pergunta para consultar na base do projeto",
    placeholder="Ex: Qual foi a resposta de valência emocional para a fragrância X?",
    key="ts_kb_test_query",
)

if test_query and st.button("Buscar", key="btn_ts_kb_search"):
    with st.spinner("Consultando base de conhecimento..."):
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
                                    st.caption(f"Fonte: {getattr(ann, 'filename', 'arquivo')}")
        except Exception as e:
            st.error(f"Erro na busca: {e}")

# ==================================================================
# Navegação
# ==================================================================
st.divider()
col_nav1, col_nav2 = st.columns(2)

with col_nav1:
    if st.button("Voltar aos Dados", use_container_width=True):
        st.switch_page("modules/teste_sensorial/preparacao.py")

with col_nav2:
    if st.button("Avançar para Análise", use_container_width=True, type="primary"):
        st.switch_page("modules/teste_sensorial/analise.py")
