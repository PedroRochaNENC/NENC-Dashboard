"""
Prosódia — Gerenciamento de Contatos WhatsApp API.

Permite listar, criar, importar em lote (via CSV) e excluir contatos na API.
"""

import streamlit as st
from utils import auth, ui
from utils.icons import page_title

auth.require_module("prosodia")

import pandas as pd
from utils.whatsapp_api_client import (
    list_owned_contacts,
    create_owned_contact,
    delete_owned_contact,
    import_owned_contacts_csv,
    is_configured
)


def _normalize_phone(value) -> str:
    return "".join(filter(str.isdigit, str(value or "")))


ui.inject_theme()
ui.breadcrumb("NencBoost", "Coleta via WhatsApp", "Contatos")
page_title(
    "address-book",
    "Contatos",
    "Quem pode receber convites de entrevista.",
)
st.markdown(
    "Gerencie a lista de contatos autorizados a receber mensagens das suas campanhas. "
    "Os contatos importados aqui poderão ser selecionados no momento da criação de uma nova campanha."
)

if not is_configured():
    st.warning("API de WhatsApp não está configurada. Configure a URL e a Chave de API primeiro.")
    if st.button("Ir para Configurações", type="primary"):
        st.switch_page("modules/prosodia/whatsapp_config.py")
    st.stop()

# Abas para separar as funcionalidades
tab_lista, tab_novo, tab_import = st.tabs([
    "Lista de Contatos",
    "Novo Contato",
    "Importar em Lote (CSV)"
])

# ---------------------------------------------------------------------------
# TAB 1: Lista de Contatos
# ---------------------------------------------------------------------------
with tab_lista:
    st.subheader("Contatos Cadastrados")
    
    # Campo de busca
    busca = st.text_input("Buscar contato", placeholder="Digite o nome ou telefone...")
    
    try:
        # Carrega contatos
        contatos = list_owned_contacts(
            search=busca if busca else None,
            limit=1000,
        )
        
        if not contatos:
            st.info("Nenhum contato encontrado.")
        else:
            # Exibir métrica de total
            st.metric("Total de Contatos", len(contatos))
            
            # Converter para DataFrame para exibição bonita
            df = pd.DataFrame(contatos)
            df = df.rename(columns={
                "id": "ID",
                "phone": "Telefone",
                "name": "Nome",
                "created_at": "Data de Cadastro"
            })
            
            # Formatar a data
            if "Data de Cadastro" in df.columns:
                df["Data de Cadastro"] = pd.to_datetime(df["Data de Cadastro"]).dt.strftime("%d/%m/%Y %H:%M")
            
            # Reordenar colunas
            df = df[["ID", "Telefone", "Nome", "Data de Cadastro"]]
            
            # Tabela interativa
            st.dataframe(df, use_container_width=True, hide_index=True)
            
            # Exclusão de contato
            st.markdown("### Excluir Contato")
            col_del_id, col_del_btn = st.columns([3, 1])
            with col_del_id:
                # Selecionar o contato para exclusão
                opcao_del = st.selectbox(
                    "Selecione o contato para excluir",
                    options=contatos,
                    format_func=lambda x: f"{x.get('name') or 'Sem Nome'} ({x.get('phone')})",
                    key="del_contact_select"
                )
            with col_del_btn:
                st.write("")
                st.write("")
                if st.button("Excluir", type="primary", use_container_width=True):
                    if opcao_del:
                        try:
                            delete_owned_contact(opcao_del["id"])
                            st.success(f"Contato {opcao_del.get('phone')} excluído com sucesso!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Erro ao excluir contato: {e}")
                            
    except Exception as e:
        st.error(f"Não foi possível listar os contatos da API: {e}")

# ---------------------------------------------------------------------------
# TAB 2: Novo Contato
# ---------------------------------------------------------------------------
with tab_novo:
    st.subheader("Cadastrar Contato Individual")
    
    with st.form("form_novo_contato", clear_on_submit=True):
        telefone = st.text_input(
            "Telefone (formato internacional) *",
            placeholder="Ex: 5511999999999",
            help="Insira o código do país, DDD e número de telefone (apenas números)."
        )
        nome = st.text_input(
            "Nome do Contato (Opcional)",
            placeholder="Ex: João da Silva"
        )
        
        submitted_contato = st.form_submit_button("Salvar Contato", type="primary")
        
    if submitted_contato:
        # Validação simples
        tel_clean = _normalize_phone(telefone)
        if not tel_clean:
            st.error("Por favor, insira um número de telefone válido.")
        else:
            try:
                create_owned_contact(
                    phone=tel_clean,
                    name=nome.strip() if nome.strip() else None,
                )
                st.success(f"Contato {tel_clean} cadastrado com sucesso!")
                st.rerun()
            except Exception as e:
                st.error(f"Erro ao cadastrar contato: {e}")

# ---------------------------------------------------------------------------
# TAB 3: Importar em Lote (CSV)
# ---------------------------------------------------------------------------
with tab_import:
    st.subheader("Importar de Arquivo CSV")
    st.markdown(
        "Carregue um arquivo CSV contendo os contatos. O arquivo deve ter uma linha de cabeçalho "
        "com pelo menos a coluna `phone` (telefone do contato no formato DDI + DDD + Número) e "
        "opcionalmente a coluna `name` (nome do contato)."
    )
    
    # Exemplo de formato
    with st.expander("Ver formato de exemplo do CSV"):
        st.code(
            "phone,name\n"
            "5511999999999,João Silva\n"
            "5511988888888,Maria Santos\n"
            "5521977777777,Carlos Souza"
        )
        
    csv_file = st.file_uploader("Selecione o arquivo CSV", type=["csv"])
    
    if csv_file:
        try:
            # Ler o CSV para prévia
            df_preview = pd.read_csv(csv_file)
            
            if "phone" not in df_preview.columns:
                st.error("Erro: O arquivo CSV deve conter uma coluna chamada 'phone'.")
            else:
                st.markdown("### Prévia dos dados a serem importados")
                st.dataframe(df_preview.head(10), use_container_width=True)
                
                # Voltar o cursor para ler novamente
                csv_file.seek(0)
                
                if st.button("Confirmar Importação em Lote", type="primary"):
                    with st.spinner("Enviando e processando lista na API..."):
                        try:
                            resultado, claimed_count, unavailable_count = (
                                import_owned_contacts_csv(
                                    csv_file.read(),
                                    df_preview["phone"].tolist(),
                                )
                            )
                            
                            st.success(
                                f"Importação finalizada!\n\n"
                                f"- **Contatos Criados:** {resultado.get('created', 0)}\n"
                                f"- **Ignorados (já existiam):** {resultado.get('skipped', 0)}\n"
                                f"- **Associados à organização:** {claimed_count}"
                            )
                            if unavailable_count:
                                st.warning(
                                    "Alguns contatos existentes pertencem a outra organizacao e nao foram associados."
                                )
                            st.rerun()
                        except Exception as e:
                            st.error(f"Erro ao importar arquivo: {e}")
        except Exception as e:
            st.error(f"Erro ao ler arquivo CSV: {e}")
