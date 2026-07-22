"""
Prosódia — Lista de Projetos.

Ponto de entrada do módulo de prosódia.
Exibe todos os projetos criados e permite criar, abrir ou excluir.
"""

import streamlit as st
from utils import auth

user = auth.require_module("prosodia")

from utils.prosodia_db import init_db, get_projects, delete_project
from utils.organization_data import claim_external_resource

# Garantir que o banco está inicializado
init_db()

st.title("🎙️ NencLex — Projetos")
st.markdown(
    "Organize suas análises do NencLex em **projetos** (campanhas). "
    "Cada projeto agrupa entrevistas com contexto compartilhado e "
    "base de conhecimento unificada."
)

# ------------------------------------------------------------------
# Botão novo projeto
# ------------------------------------------------------------------
col_title, col_btn = st.columns([4, 1])
with col_btn:
    if st.button("➕ Novo Projeto", type="primary", width='stretch'):
        st.session_state.pop("pros_project_id", None)
        st.switch_page("modules/prosodia/preparacao.py")

# ------------------------------------------------------------------
# Configuração da Integração WhatsApp
# ------------------------------------------------------------------
# ------------------------------------------------------------------
# Painel de Controle WhatsApp Integration
# ------------------------------------------------------------------
with st.container(border=True):
    st.markdown("### 📱 Integração com WhatsApp API")
    
    from utils.whatsapp_api_client import is_configured, test_connection
    
    c_status, c_btn1, c_btn2, c_btn3, c_btn4 = st.columns([2, 1.5, 1.5, 1.5, 1.5])
    
    with c_status:
        if is_configured():
            success, _ = test_connection()
            if success:
                st.markdown(
                    """
                    <div style="display: flex; align-items: center; gap: 8px; height: 38px;">
                        <span style="height: 10px; width: 10px; background-color: #2ecc71; border-radius: 50%; display: inline-block; animation: pulse 1.5s infinite;"></span>
                        <strong style="color: #2ecc71; font-size: 14px;">API Conectada</strong>
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
                    <div style="display: flex; align-items: center; gap: 8px; height: 38px;">
                        <span style="height: 10px; width: 10px; background-color: #e74c3c; border-radius: 50%; display: inline-block;"></span>
                        <strong style="color: #e74c3c; font-size: 14px;">API Offline</strong>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
        else:
            st.markdown(
                """
                <div style="display: flex; align-items: center; gap: 8px; height: 38px;">
                    <span style="height: 10px; width: 10px; background-color: #f1c40f; border-radius: 50%; display: inline-block;"></span>
                    <strong style="color: #f1c40f; font-size: 14px;">Não Configurada</strong>
                </div>
                """,
                unsafe_allow_html=True
            )
            
    with c_btn1:
        if st.button("👤 Contatos", use_container_width=True):
            st.switch_page("modules/prosodia/whatsapp_contatos.py")
            
    with c_btn2:
        if st.button("📢 Campanhas", use_container_width=True):
            st.switch_page("modules/prosodia/whatsapp_campanhas.py")
            
    with c_btn3:
        if st.button("📡 Monitor", use_container_width=True):
            st.switch_page("modules/prosodia/whatsapp_monitor.py")
            
    with c_btn4:
        if user.is_platform_admin and st.button("⚙️ Configurar", use_container_width=True):
            st.switch_page("modules/prosodia/whatsapp_config.py")

st.divider()

# ------------------------------------------------------------------
# Lista de projetos
# ------------------------------------------------------------------
projects = get_projects()

if not projects:
    st.info(
        "Nenhum projeto criado ainda. "
        "Clique em **➕ Novo Projeto** para começar."
    )
else:
    for proj in projects:
        with st.container(border=True):
            c1, c2, c3, c4, c5 = st.columns([4, 1, 1, 1, 1])

            api_proj_id = proj.get("api_project_id")

            with c1:
                n = proj.get("n_audios", 0)
                badge_html = f" <span style='background-color: #2ecc71; color: white; padding: 2px 6px; border-radius: 4px; font-size: 10px; margin-left: 8px;'>🔗 API #{api_proj_id}</span>" if api_proj_id else ""
                st.markdown(f"**{proj['name']}**{badge_html}", unsafe_allow_html=True)
                st.caption(
                    f"🗂️ {n} entrevista(s)  •  📅 {proj['created_at'][:10]}"
                    + (f"  •  _{proj['especialidade'][:60]}…_" if proj.get("especialidade") else "")
                )

            with c2:
                st.write("")
                if st.button("📂 Abrir", key=f"open_{proj['id']}", width='stretch'):
                    st.session_state["pros_project_id"] = proj["id"]
                    st.switch_page("modules/prosodia/entrevistas.py")

            with c3:
                st.write("")
                if st.button("✏️ Editar", key=f"edit_{proj['id']}", width='stretch'):
                    st.session_state["pros_project_id"] = proj["id"]
                    st.switch_page("modules/prosodia/preparacao.py")

            with c4:
                st.write("")
                if st.button("📤 Uploads", key=f"uploads_{proj['id']}", width='stretch'):
                    st.session_state["pros_project_id"] = proj["id"]
                    st.switch_page("modules/prosodia/audios.py")

            with c5:
                st.write("")
                if st.button("🗑️ Excluir", key=f"del_{proj['id']}", width='stretch'):
                    st.session_state[f"confirm_del_{proj['id']}"] = True

            # ------------------------------------------------------------------
            # Expander de QR Codes (se vinculado à API)
            # ------------------------------------------------------------------
            if api_proj_id:
                with st.expander(f"📱 Gerenciar QR Codes do Projeto (API #{api_proj_id})"):
                    from utils.whatsapp_api_client import (
                        get_project_qr_codes,
                        create_project_qr_code,
                        delete_project_qr_code,
                        get_project_participations,
                    )
                    import qrcode
                    from io import BytesIO

                    from utils.prosodia_db import update_project_api_id
                    from utils.whatsapp_api_client import create_api_project

                    is_api_missing = False
                    try:
                        qr_codes = get_project_qr_codes(api_proj_id)
                        participations = get_project_participations(api_proj_id)
                    except Exception as e:
                        err_msg = str(e)
                        qr_codes = []
                        participations = []
                        if "404" in err_msg:
                            is_api_missing = True
                            st.warning(
                                f"⚠️ O projeto ID #{api_proj_id} não existe na base de dados da API atual.\n\n"
                                "Isso pode ocorrer se o banco de dados da API foi reiniciado ou resetado."
                            )
                            if st.button("🔄 Recriar / Sincronizar Projeto na API", key=f"resync_api_{proj['id']}", type="primary"):
                                try:
                                    new_api_proj = create_api_project(
                                        name=proj["name"],
                                        organization=proj.get("especialidade") or "General",
                                    )
                                    new_id = new_api_proj.get("id")
                                    update_project_api_id(proj["id"], new_id)
                                    st.success(f"Projeto recriado na API com sucesso! Novo ID: #{new_id}")
                                    st.rerun()
                                except Exception as sync_err:
                                    st.error(f"Erro ao sincronizar com a API: {sync_err}")
                        else:
                            st.error(f"Não foi possível carregar QR Codes da API: {e}")

                    st.markdown(f"**Estatísticas**: {len(qr_codes)} QR Code(s) cadastrado(s) | {len(participations)} participante(s) inscrito(s)")

                    # Formulário de criação de novo QR Code
                    st.markdown("#### ➕ Criar Novo QR Code para Captação")
                    with st.form(key=f"form_new_qr_{proj['id']}"):
                        col_q1, col_q2 = st.columns(2)
                        with col_q1:
                            new_qr_name = st.text_input("Nome do QR Code *", placeholder="Ex: Cartaz Recepção HCFMUSP")
                            new_qr_code = st.text_input("Código de Rastreio (opcional)", placeholder="Ex: HCFMUSP-REC-01")
                        with col_q2:
                            new_qr_desc = st.text_area("Descrição do Local/Canal", placeholder="Ex: Cartaz A3 afixado no balcão de entrada principal")

                        submit_qr = st.form_submit_button("Gerar e Cadastrar QR Code")

                    if submit_qr:
                        if not new_qr_name.strip():
                            st.error("Informe o Nome do QR Code.")
                        else:
                            try:
                                create_project_qr_code(
                                    api_proj_id,
                                    name=new_qr_name.strip(),
                                    description=new_qr_desc.strip() if new_qr_desc else None,
                                    code=new_qr_code.strip() if new_qr_code else None,
                                )
                                st.success("QR Code cadastrado com sucesso!")
                                st.rerun()
                            except Exception as err:
                                err_msg = str(err)
                                if "404" in err_msg:
                                    st.error(
                                        "⚠️ A API retornou 404 (Não Encontrado). "
                                        "Por favor, reinicie o processo do backend FastAPI "
                                        "para que o servidor atualize a tabela de rotas."
                                    )
                                else:
                                    st.error(f"Erro ao criar QR Code: {err}")

                    st.divider()
                    st.markdown("#### 📋 QR Codes Ativos")

                    if not qr_codes:
                        st.info("Nenhum QR Code gerado para este projeto ainda.")
                    else:
                        wa_phone = st.text_input(
                            "Número de WhatsApp Destino (DDI + DDD + Número)",
                            value="5511999999999",
                            key=f"wa_phone_{proj['id']}",
                            help="Número do WhatsApp configurado no Webhook da API para pré-preencher a mensagem do QR Code.",
                        )
                        clean_wa_phone = "".join(filter(str.isdigit, wa_phone))

                        for qr in qr_codes:
                            with st.container(border=True):
                                col_info, col_img = st.columns([3, 2])
                                wa_link = f"https://wa.me/{clean_wa_phone}?text=Projeto%3A{qr['code']}"

                                with col_info:
                                    st.markdown(f"### {qr['name']}")
                                    st.markdown(f"**Código de Rastreio**: `{qr['code']}`")
                                    if qr.get("description"):
                                        st.caption(f"**Descrição**: {qr['description']}")
                                    st.markdown(f"**Link WhatsApp**: [{wa_link}]({wa_link})")
                                    st.caption(f"Status: `{qr['status']}` | Criado em: {qr['created_at'][:10]}")

                                    if st.button("🗑️ Excluir QR Code", key=f"del_qr_{proj['id']}_{qr['id']}"):
                                        try:
                                            delete_project_qr_code(api_proj_id, qr["id"])
                                            st.success("QR Code excluído.")
                                            st.rerun()
                                        except Exception as ex:
                                            st.error(f"Erro ao excluir: {ex}")

                                with col_img:
                                    # Generate QR Code image in memory
                                    qr_img = qrcode.make(wa_link)
                                    buf = BytesIO()
                                    qr_img.save(buf, format="PNG")
                                    img_bytes = buf.getvalue()

                                    st.image(img_bytes, width=180, caption=f"Escaneie: {qr['name']}")
                                    st.download_button(
                                        label="📥 Baixar Imagem (PNG)",
                                        data=img_bytes,
                                        file_name=f"qrcode_{qr['code']}.png",
                                        mime="image/png",
                                        key=f"dl_qr_img_{proj['id']}_{qr['id']}",
                                        use_container_width=True,
                                    )

            # Confirmação de exclusão
            if st.session_state.get(f"confirm_del_{proj['id']}"):
                st.warning(
                    f"Tem certeza que deseja excluir **{proj['name']}**? "
                    "Todas as entrevistas e análises serão removidas permanentemente."
                )

                excluir_na_api = False
                if api_proj_id:
                    excluir_na_api = st.checkbox(
                        "Excluir também na API",
                        value=False,
                        key=f"excluir_api_cb_{proj['id']}"
                    )

                cc1, cc2 = st.columns(2)
                with cc1:
                    if st.button("✅ Confirmar exclusão", key=f"confirm_yes_{proj['id']}", width='stretch'):
                        if api_proj_id and excluir_na_api:
                            try:
                                from utils.whatsapp_api_client import delete_api_project

                                claim_external_resource(
                                    "whatsapp_api_project",
                                    api_proj_id,
                                    {"project_id": proj["id"]},
                                )
                                delete_api_project(api_proj_id)
                            except Exception as e:
                                st.error(f"Erro ao excluir projeto na API: {e}")
                                # Não interrompe para garantir que a exclusão local ocorra
                        delete_project(proj["id"])
                        st.session_state.pop(f"confirm_del_{proj['id']}", None)
                        st.rerun()
                with cc2:
                    if st.button("❌ Cancelar", key=f"confirm_no_{proj['id']}", width='stretch'):
                        st.session_state.pop(f"confirm_del_{proj['id']}", None)
                        st.rerun()
