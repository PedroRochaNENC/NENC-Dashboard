"""
Prosódia — Configuração da WhatsApp API.

Permite definir as credenciais de acesso para a API de WhatsApp,
testar a conectividade e visualizar informações de diagnóstico.
"""

import os
from pathlib import Path
import streamlit as st
from utils import auth

auth.require_admin(platform_only=True)

from dotenv import load_dotenv, set_key

from utils.whatsapp_api_client import test_connection, is_configured
from utils.organization_data import migrate_legacy_external_resource

# Use the same dotenv lookup order as the application runtime.
_APPLICATION_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
_WORKSPACE_ENV_PATH = _APPLICATION_ENV_PATH.parent / ".env"
_ENV_PATH = next(
    (path for path in (_APPLICATION_ENV_PATH, _WORKSPACE_ENV_PATH) if path.exists()),
    _APPLICATION_ENV_PATH,
)

# Garantir carregamento das variáveis
load_dotenv(_ENV_PATH)

st.title("⚙️ Configuração da WhatsApp API")
st.markdown(
    "Configure os parâmetros de integração com o serviço **NencProsodiaWhatsapp-API** "
    "para habilitar o envio de campanhas, cadastro de contatos e importação automática de áudios."
)

# Botão Voltar para Projetos
nav_col, _ = st.columns([2, 8])
with nav_col:
    if st.button("← Projetos", width='stretch'):
        st.switch_page("modules/prosodia/projetos.py")

st.divider()

# Ler valores atuais
current_url = os.getenv("WHATSAPP_API_URL", "")
current_key = os.getenv("WHATSAPP_API_KEY", "")

st.subheader("Migração de Recursos Legados")
with st.form("form_whatsapp_legacy_resource", clear_on_submit=True):
    legacy_resource_type = st.selectbox(
        "Tipo de recurso",
        [
            "whatsapp_contact",
            "whatsapp_campaign",
            "whatsapp_api_project",
            "whatsapp_audio",
            "whatsapp_job",
        ],
    )
    legacy_resource_id = st.text_input("ID externo")
    legacy_phone = st.text_input("Telefone do contato", disabled=legacy_resource_type != "whatsapp_contact")
    legacy_name = st.text_input("Nome do contato", disabled=legacy_resource_type != "whatsapp_contact")
    migrate_legacy_resource = st.form_submit_button("Migrar recurso legado")

if migrate_legacy_resource:
    metadata = {}
    if legacy_resource_type == "whatsapp_contact":
        metadata = {
            "phone": "".join(filter(str.isdigit, legacy_phone)),
            "name": legacy_name.strip(),
        }
    try:
        migrate_legacy_external_resource(
            legacy_resource_type,
            legacy_resource_id,
            metadata,
        )
        st.success("Recurso legado migrado para a organização ativa.")
    except Exception as error:
        st.error(f"Não foi possível migrar o recurso legado: {error}")

st.divider()
col_form, col_status = st.columns([6, 4])

with col_form:
    st.subheader("🔑 Credenciais da API")
    with st.form("form_whatsapp_config"):
        api_url = st.text_input(
            "URL da API de WhatsApp",
            value=current_url,
            placeholder="Ex: http://localhost:8000 ou URL ngrok",
            help="Endereço base onde o backend FastAPI está rodando.",
        )
        api_key = st.text_input(
            "Chave de API (X-API-Key)",
            value=current_key,
            type="password",
            placeholder="Chave de autenticação configurada no backend",
            help="Token de segurança exigido pela API para validar as requisições.",
        )
        
        submitted = st.form_submit_button("💾 Salvar Configurações", type="primary", use_container_width=True)

    if submitted:
        url_stripped = api_url.strip().strip("'\"").rstrip("/")
        key_stripped = api_key.strip().strip("'\"")
        
        # Persistir no mesmo .env carregado pelo processo da aplicacao.
        try:
            set_key(str(_ENV_PATH), "WHATSAPP_API_URL", url_stripped, quote_mode="never")
            set_key(str(_ENV_PATH), "WHATSAPP_API_KEY", key_stripped, quote_mode="never")
            
            os.environ["WHATSAPP_API_URL"] = url_stripped
            os.environ["WHATSAPP_API_KEY"] = key_stripped
            
            st.success("✅ Configurações salvas e aplicadas com sucesso!")
            st.rerun()
        except Exception as e:
            st.error(f"Erro ao salvar configurações no arquivo .env: {e}")

with col_status:
    st.subheader("📡 Status da Conexão")
    
    if not is_configured():
        st.warning("⚠️ API não configurada. Preencha os campos ao lado e salve.")
    else:
        # Card de status
        with st.container(border=True):
            st.markdown(f"**URL:** `{current_url}`")
            
            # Botão de teste
            if st.button("🔌 Testar Conexão Agora", type="secondary", use_container_width=True):
                success, msg = test_connection()
                if success:
                    st.success(f"✅ Conectado com sucesso!\n\n**Detalhe:** {msg}")
                else:
                    st.error(f"❌ Falha de conexão!\n\n**Detalhe:** {msg}")
            
            # Estado geral resumido
            success, _ = test_connection()
            if success:
                st.markdown(
                    """
                    <div style="display: flex; align-items: center; gap: 10px; margin-top: 15px; padding: 10px; background-color: rgba(46, 204, 113, 0.15); border-radius: 5px; border-left: 5px solid #2ecc71;">
                        <span style="height: 12px; width: 12px; background-color: #2ecc71; border-radius: 50%; display: inline-block; animation: pulse 1.5s infinite;"></span>
                        <strong style="color: #2ecc71;">API ONLINE & AUTENTICADA</strong>
                    </div>
                    <style>
                    @keyframes pulse {
                        0% { transform: scale(0.9); opacity: 0.7; }
                        50% { transform: scale(1.1); opacity: 1; }
                        100% { transform: scale(0.9); opacity: 0.7; }
                    }
                    </style>
                    """,
                    unsafe_allow_html=True
                )
            else:
                st.markdown(
                    """
                    <div style="display: flex; align-items: center; gap: 10px; margin-top: 15px; padding: 10px; background-color: rgba(231, 76, 60, 0.15); border-radius: 5px; border-left: 5px solid #e74c3c;">
                        <span style="height: 12px; width: 12px; background-color: #e74c3c; border-radius: 50%; display: inline-block;"></span>
                        <strong style="color: #e74c3c;">API INDISPONÍVEL / ERRO</strong>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
