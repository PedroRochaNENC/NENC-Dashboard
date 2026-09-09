"""
Teste Sensorial — Base de Conhecimento.

Gerenciamento do vector store OpenAI por projeto: upload de documentos de produto,
listagem, exclusão e busca RAG de laudos e pesquisas sensoriais.
"""

import streamlit as st
from utils import auth, ui
from utils.icons import page_title

user = auth.require_module("teste_sensorial")

from utils import teste_sensorial_db
from utils.ai_provider import (
    add_document_to_vector_store,
    get_openai_client,
    list_vector_store_documents,
)

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

# Acesso ao modulo e leitura; alterar a base e escrita, e so administrador da
# organizacao escreve. Cada acao repete a guarda no servidor: esconder o botao
# organiza a tela, nao autoriza nada.
pode_editar = auth.can_write(user)
if not pode_editar:
    st.info("Sua conta consulta a base de conhecimento, mas não pode alterá-la.")

# ==================================================================
# Seção 1: Configuração do Vector Store
# ==================================================================
st.subheader("Vector Store do Projeto")

vs_id = project.get("vector_store_id")

if vs_id:
    st.success(f"Vector Store ativo para o projeto: `{vs_id}`")
else:
    st.warning("Este projeto ainda não possui um Vector Store para busca RAG.")
    if not pode_editar:
        st.caption("Peça a um administrador da organização para criar a base.")
    elif st.button("Criar Vector Store para este Projeto", type="primary"):
        with st.spinner("Criando vector store..."):
            try:
                # Antes de criar na OpenAI: negar depois deixaria um vector
                # store orfao, pago e sem dono no projeto.
                auth.assert_module_write("teste_sensorial")
                vs = client.vector_stores.create(name=f"TS - {project['name']}")
                teste_sensorial_db.update_project(project_id, vector_store_id=vs.id)
                st.success(f"Vector Store criado com sucesso: `{vs.id}`")
                st.rerun()
            except auth.AuthorizationError as error:
                st.error(str(error))
            except Exception as e:
                st.error(f"Erro ao criar vector store: {e}")
    st.stop()

# ==================================================================
# Seção 2: Upload de Documentos
# ==================================================================
st.divider()
st.subheader("Upload de Documentos de Produto / Laudos")

if pode_editar:
    uploaded_files = st.file_uploader(
        "Selecione laudos sensoriais, pesquisas ou fichas técnicas",
        type=["pdf", "pptx", "docx", "txt", "csv", "md"],
        accept_multiple_files=True,
        key="ts_kb_upload",
    )
else:
    uploaded_files = None
    st.caption("Somente administradores da organização enviam documentos.")

if uploaded_files:
    if st.button("Indexar Documentos na Base do Projeto", type="primary"):
        try:
            auth.assert_module_write("teste_sensorial")
        except auth.AuthorizationError as error:
            st.error(str(error))
            st.stop()

        progress = st.progress(0)
        for i, f in enumerate(uploaded_files):
            with st.spinner(f"Enviando {f.name}..."):
                try:
                    documento = add_document_to_vector_store(
                        vs_id,
                        f.name,
                        f.getvalue(),
                        {
                            "escopo": "referencia",
                            "modulo": "teste_sensorial",
                            "projeto": project["name"],
                            "project_id": project_id,
                        },
                    )
                    if documento.status == "completed":
                        st.success(f"{f.name} indexado")
                    else:
                        st.warning(f"{f.name}: {documento.status}")
                except Exception as e:
                    st.error(f"Erro ao enviar {f.name}: {e}")
            progress.progress((i + 1) / len(uploaded_files))
        list_vector_store_documents.clear()
        st.rerun()

# ==================================================================
# Seção 3: Documentos na Base
# ==================================================================
st.divider()
st.subheader("Documentos Indexados no Projeto")

try:
    file_list = list_vector_store_documents(vs_id)
except Exception as e:
    st.error(f"Erro ao listar arquivos: {e}")
    file_list = []

if file_list:
    col_total, col_refresh = st.columns([3, 1])
    with col_total:
        st.metric("Total de documentos", len(file_list))
    with col_refresh:
        # A listagem fica em cache por um minuto: quem acabou de indexar um
        # documento precisa de um jeito de acompanhar a mudanca de status.
        if st.button("Atualizar lista", key="ts_kb_refresh"):
            list_vector_store_documents.clear()
            st.rerun()

    for document in file_list:
        col_name, col_status, col_action = st.columns([4, 2, 1])

        filename = document["filename"]

        with col_name:
            st.text(f"{filename} ({document['size_kb']:.1f} KB)")
            # Os metadados do envio existem para filtrar a busca; mostra-los e a
            # unica forma de perceber um documento que subiu sem eles.
            etiquetas = [
                f"{chave}: {valor}"
                for chave, valor in sorted(document.get("attributes", {}).items())
                if chave not in ("escopo", "modulo")
            ]
            if etiquetas:
                st.caption(" · ".join(etiquetas))

        with col_status:
            status = document["status"]
            if status == "completed":
                st.success("Pronto")
            elif status == "in_progress":
                st.warning("Processando...")
            else:
                st.error(status)

        with col_action:
            if pode_editar and st.button(
                "🗑️", key=f"del_ts_vs_{document['id']}", help=f"Remover {filename}"
            ):
                try:
                    auth.assert_module_write("teste_sensorial")
                    client.vector_stores.files.delete(
                        vector_store_id=vs_id,
                        file_id=document["id"],
                    )
                    client.files.delete(document["id"])
                    list_vector_store_documents.clear()
                    st.rerun()
                except auth.AuthorizationError as error:
                    st.error(str(error))
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
            # A busca crua, sem gerar texto: mostra os mesmos trechos que o
            # file_search entregaria ao modelo, com o score de cada um. Era aqui
            # que uma resposta bem escrita escondia uma base que nao devolvia nada.
            found = list(
                client.vector_stores.search(
                    vector_store_id=vs_id,
                    query=test_query,
                    max_num_results=10,
                )
            )

            if not found:
                st.info("Nenhum trecho encontrado para essa consulta.")
            for result in found:
                st.markdown(f"**{result.filename}** · relevância {result.score:.2f}")
                for part in result.content:
                    if part.type == "text":
                        st.caption(part.text[:500])
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
