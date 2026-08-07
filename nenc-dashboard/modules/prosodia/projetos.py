"""
Prosódia — Lista de Projetos.

Ponto de entrada do módulo de prosódia.
Exibe todos os projetos criados e permite criar, abrir ou excluir.
"""

import streamlit as st
from utils import auth

user = auth.require_module("prosodia")

# A negativa de verdade esta em prosodia_db/whatsapp_api_client; aqui so
# evitamos oferecer ao leitor controles que o servidor vai recusar.
pode_editar = auth.can_write(user)

from utils.prosodia_db import (
    init_db,
    get_projects,
    delete_project,
    user_can_modify_project,
)
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
    if pode_editar and st.button("➕ Novo Projeto", type="primary", width='stretch'):
        st.session_state.pop("pros_project_id", None)
        st.switch_page("modules/prosodia/preparacao.py")

if not pode_editar:
    st.info("Sua conta tem acesso somente de leitura ao NencLex.")

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
            # Editar e excluir dependem da autoria; abrir e enviar entrevistas
            # continuam liberados para qualquer administrador da organizacao.
            pode_alterar = user_can_modify_project(proj, user)

            with c1:
                n = proj.get("n_audios", 0)
                org_name = proj.get("organization_name")
                org_badge = f" <span style='background-color: #3498db; color: white; padding: 2px 6px; border-radius: 4px; font-size: 10px; margin-left: 8px;'>🏢 {org_name}</span>" if org_name and user.is_platform_admin else ""
                api_badge = f" <span style='background-color: #2ecc71; color: white; padding: 2px 6px; border-radius: 4px; font-size: 10px; margin-left: 8px;'>🔗 API #{api_proj_id}</span>" if api_proj_id else ""
                st.markdown(f"**{proj['name']}**{org_badge}{api_badge}", unsafe_allow_html=True)
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
                if pode_alterar and st.button("✏️ Editar", key=f"edit_{proj['id']}", width='stretch'):
                    st.session_state["pros_project_id"] = proj["id"]
                    st.switch_page("modules/prosodia/preparacao.py")

            with c4:
                st.write("")
                if pode_editar and st.button("📤 Uploads", key=f"uploads_{proj['id']}", width='stretch'):
                    st.session_state["pros_project_id"] = proj["id"]
                    st.switch_page("modules/prosodia/audios.py")

            with c5:
                st.write("")
                if pode_alterar and st.button("🗑️ Excluir", key=f"del_{proj['id']}", width='stretch'):
                    st.session_state[f"confirm_del_{proj['id']}"] = True

            # ------------------------------------------------------------------
            # Gerenciar QR Codes do Projeto
            # ------------------------------------------------------------------
            expander_title = (
                f"📱 Gerenciar QR Codes do Projeto (API #{api_proj_id})"
                if api_proj_id
                else "📱 Gerenciar QR Codes do Projeto"
            )
            with st.expander(expander_title, expanded=False):
                from utils.whatsapp_api_client import (
                    is_configured,
                    get_project_qr_codes,
                    get_project_participations,
                    create_project_qr_code,
                    delete_project_qr_code,
                    generate_qr_code_bytes,
                    build_whatsapp_deeplink,
                    create_api_project,
                )
                from utils.organization_data import list_external_resources
                from utils.prosodia_db import DEFAULT_QR_VERIFICATION_TEXT, update_project

                target_org_id = proj.get("organization_id") or auth.active_organization_id(user)
                org_phones = auth.get_organization_whatsapp_numbers(target_org_id)

                # Complementar com contatos externos salvos se necessário
                if not org_phones:
                    org_contacts = list_external_resources("whatsapp_contact")
                    for c in org_contacts:
                        meta = c.get("metadata") or {}
                        p = meta.get("phone") or c.get("external_id")
                        if p:
                            clean_p = "".join(filter(str.isdigit, str(p)))
                            if clean_p and clean_p not in org_phones:
                                org_phones.append(clean_p)

                if not org_phones:
                    org_phones = ["5511975218007", "5516981360051"]

                current_verification_text = proj.get("qr_verification_text") or DEFAULT_QR_VERIFICATION_TEXT

                if is_configured() and not api_proj_id:
                    st.info("🔗 Este projeto local ainda não está vinculado à API de WhatsApp.")
                    if pode_alterar and st.button("🚀 Vincular Projeto à API de WhatsApp agora", key=f"btn_link_api_{proj['id']}"):
                        try:
                            resp = create_api_project(proj["name"], user.organization_name)
                            new_api_id = resp.get("id")
                            update_project(
                                proj["id"],
                                name=proj["name"],
                                especialidade=proj.get("especialidade", ""),
                                historico=proj.get("historico", ""),
                                problemas=proj.get("problemas", ""),
                                questions=proj.get("questions", ""),
                                entities=proj.get("entities", ""),
                                briefing_filename=proj.get("briefing_filename", ""),
                                briefing_text=proj.get("briefing_text", ""),
                                whatsapp_campaign_id=proj.get("whatsapp_campaign_id"),
                                quality_thresholds=proj.get("quality_thresholds"),
                                api_project_id=new_api_id,
                                qr_verification_text=proj.get("qr_verification_text"),
                            )
                            st.success("Projeto vinculado à API de WhatsApp com sucesso!")
                            st.rerun()
                        except Exception as link_err:
                            st.error(f"Erro ao vincular à API: {link_err}")

                if api_proj_id and is_configured():
                    qr_codes = []
                    participations = []
                    try:
                        qr_codes = get_project_qr_codes(api_proj_id)
                        participations = get_project_participations(api_proj_id)
                    except Exception as e:
                        st.error(f"Não foi possível carregar QR Codes da API: {e}")

                    st.markdown(
                        f"**Estatísticas**: {len(qr_codes)} QR Code(s) cadastrado(s) | "
                        f"{len(participations)} participante(s) inscrito(s)"
                    )

                    default_welcome_text = (
                        "Quero agradecer muito a sua participação. Grave um áudio com o conteúdo que achar importante "
                        "(sugestão, reclamação, saudação, agradecimento... ) que possa colaborar com a melhoria do nosso trabalho. "
                        "Críticas, sugestões e também elogios serão muito bem vindos."
                    )

                    with st.container(border=True):
                        st.markdown("#### 📝 Texto de Verificação do WhatsApp para o Projeto")
                        st.caption("Texto pré-preenchido no WhatsApp ao escanear o QR Code de verificação.")
                        with st.form(key=f"form_global_verif_{proj['id']}"):
                            global_verif_text = st.text_area(
                                "Texto de Verificação (WhatsApp)",
                                value=current_verification_text,
                                height=85,
                                help="Texto pré-preenchido no WhatsApp do participante.",
                                key=f"global_verif_text_{proj['id']}"
                            )
                            submit_global_verif = st.form_submit_button(
                                "💾 Salvar Texto de Verificação", disabled=not pode_alterar
                            )
                        if submit_global_verif:
                            update_project(
                                proj["id"],
                                name=proj["name"],
                                especialidade=proj.get("especialidade", ""),
                                historico=proj.get("historico", ""),
                                problemas=proj.get("problemas", ""),
                                questions=proj.get("questions", ""),
                                entities=proj.get("entities", ""),
                                briefing_filename=proj.get("briefing_filename", ""),
                                briefing_text=proj.get("briefing_text", ""),
                                whatsapp_campaign_id=proj.get("whatsapp_campaign_id"),
                                quality_thresholds=proj.get("quality_thresholds"),
                                api_project_id=proj.get("api_project_id"),
                                qr_verification_text=global_verif_text.strip(),
                            )
                            st.success("Texto de verificação atualizado!")
                            st.rerun()

                    st.markdown("#### ➕ Criar Novo QR Code para Captação")
                    with st.form(key=f"form_new_qr_{proj['id']}"):
                        col_q1, col_q2 = st.columns(2)
                        with col_q1:
                            new_qr_name = st.text_input(
                                "Nome do QR Code *",
                                placeholder="Ex: Cartaz Recepção HCFMUSP",
                                key=f"new_qr_name_{proj['id']}"
                            )
                            suggested_code = f"{api_proj_id:02d}-{len(qr_codes)+1:02d}"
                            new_qr_code = st.text_input(
                                "Código de Rastreio (opcional)",
                                value=suggested_code,
                                placeholder="Ex: 01-01",
                                key=f"new_qr_code_{proj['id']}"
                            )
                            new_target_phone = st.selectbox(
                                "Número WhatsApp Destino da Organização *",
                                options=org_phones,
                                help="Selecione o número de WhatsApp cadastrado na organização que receberá as mensagens deste QR Code.",
                                key=f"new_target_phone_{proj['id']}"
                            )
                        with col_q2:
                            new_qr_desc = st.text_area(
                                "Descrição do Local/Canal",
                                placeholder="Ex: Cartaz A3 afixado no balcão de entrada principal",
                                height=110,
                                key=f"new_qr_desc_{proj['id']}"
                            )
                            new_welcome_msg = st.text_area(
                                "Resposta Automática de Boas-Vindas (WhatsApp)",
                                value=default_welcome_text,
                                height=110,
                                help="Mensagem de texto enviada pelo WhatsApp assim que o participante escanear o QR Code.",
                                key=f"new_welcome_msg_{proj['id']}"
                            )

                        new_verification_text = st.text_area(
                            "Texto de Verificação (WhatsApp)",
                            value=current_verification_text,
                            height=85,
                            help="Texto pré-preenchido no WhatsApp ao escanear o QR Code de verificação.",
                            key=f"new_verification_text_{proj['id']}"
                        )

                        submit_qr = st.form_submit_button(
                            "Gerar e Cadastrar QR Code", disabled=not pode_alterar
                        )

                    if submit_qr:
                        if not new_qr_name.strip():
                            st.error("Informe o Nome do QR Code.")
                        else:
                            try:
                                if new_verification_text.strip() and new_verification_text.strip() != current_verification_text:
                                    update_project(
                                        proj["id"],
                                        name=proj["name"],
                                        especialidade=proj.get("especialidade", ""),
                                        historico=proj.get("historico", ""),
                                        problemas=proj.get("problemas", ""),
                                        questions=proj.get("questions", ""),
                                        entities=proj.get("entities", ""),
                                        briefing_filename=proj.get("briefing_filename", ""),
                                        briefing_text=proj.get("briefing_text", ""),
                                        whatsapp_campaign_id=proj.get("whatsapp_campaign_id"),
                                        quality_thresholds=proj.get("quality_thresholds"),
                                        api_project_id=proj.get("api_project_id"),
                                        qr_verification_text=new_verification_text.strip(),
                                    )
                                create_project_qr_code(
                                    api_proj_id,
                                    name=new_qr_name.strip(),
                                    description=new_qr_desc.strip() if new_qr_desc else None,
                                    code=new_qr_code.strip() if new_qr_code else None,
                                    target_phone=new_target_phone.strip() if new_target_phone else None,
                                    welcome_message=new_welcome_msg.strip() if new_welcome_msg else None,
                                    verification_text=new_verification_text.strip() if new_verification_text else None,
                                )
                                st.success("QR Code cadastrado com sucesso!")
                                st.rerun()
                            except Exception as err:
                                st.error(f"Erro ao criar QR Code: {err}")

                    st.divider()
                    st.markdown("#### 📋 QR Codes Ativos")

                    if not qr_codes:
                        st.info("Nenhum QR Code gerado para este projeto ainda.")
                    else:
                        project_verification_text = proj.get("qr_verification_text") or DEFAULT_QR_VERIFICATION_TEXT

                        for qr in qr_codes:
                            with st.container(border=True):
                                col_info, col_img = st.columns([3, 2])
                                target_ph = "".join(filter(str.isdigit, qr.get("target_phone") or org_phones[0]))
                                wa_link = build_whatsapp_deeplink(target_ph, project_verification_text, qr.get("code", ""))

                                with col_info:
                                    st.markdown(f"### {qr['name']}")
                                    st.markdown(f"**Código de Rastreio**: `{qr['code']}` | **WhatsApp Destino**: `+{target_ph}`")
                                    if qr.get("description"):
                                        st.caption(f"**Descrição**: {qr['description']}")
                                    if qr.get("welcome_message"):
                                        st.caption(f"💬 **Resposta Automática**: *\"{qr['welcome_message']}\"*")
                                    st.markdown(f"**Link WhatsApp**: [{wa_link}]({wa_link})")
                                    st.caption(f"Status: `{qr['status']}` | Criado em: {qr.get('created_at', '')[:10]}")

                                    if pode_alterar and st.button("🗑️ Excluir QR Code", key=f"del_qr_{proj['id']}_{qr['id']}"):
                                        try:
                                            delete_project_qr_code(api_proj_id, qr["id"])
                                            st.success("QR Code excluído.")
                                            st.rerun()
                                        except Exception as ex:
                                            st.error(f"Erro ao excluir: {ex}")

                                with col_img:
                                    try:
                                        img_bytes = generate_qr_code_bytes(wa_link)
                                        st.image(img_bytes, width=180, caption=f"Escaneie: {qr['name']}")
                                        st.download_button(
                                            label="📥 Baixar Imagem (PNG)",
                                            data=img_bytes,
                                            file_name=f"qrcode_{qr['code']}.png",
                                            mime="image/png",
                                            key=f"dl_qr_img_{proj['id']}_{qr['id']}",
                                            use_container_width=True,
                                        )
                                    except Exception as img_err:
                                        st.error(f"Erro ao renderizar imagem do QR Code: {img_err}")
                else:
                    st.markdown("#### 📱 Gerar / Baixar QR Code do Projeto")
                    with st.form(key=f"form_local_qr_{proj['id']}"):
                        col_l1, col_l2 = st.columns(2)
                        with col_l1:
                            target_phone_local = st.selectbox(
                                "Número WhatsApp Destino *",
                                options=org_phones,
                                key=f"local_phone_{proj['id']}"
                            )
                            local_code = st.text_input(
                                "Código de Rastreio (opcional)",
                                value=f"{proj['id']:02d}-01",
                                key=f"local_code_{proj['id']}"
                            )
                        with col_l2:
                            local_verif_text = st.text_area(
                                "Texto de Verificação (WhatsApp)",
                                value=current_verification_text,
                                height=85,
                                help="Texto pré-preenchido no WhatsApp ao escanear o QR Code.",
                                key=f"local_verif_text_{proj['id']}"
                            )
                        btn_gen_local = st.form_submit_button("Gerar e Visualizar QR Code")

                    if btn_gen_local or st.session_state.get(f"show_local_qr_{proj['id']}"):
                        st.session_state[f"show_local_qr_{proj['id']}"] = True
                        if pode_alterar and local_verif_text.strip() and local_verif_text.strip() != current_verification_text:
                            update_project(
                                proj["id"],
                                name=proj["name"],
                                especialidade=proj.get("especialidade", ""),
                                historico=proj.get("historico", ""),
                                problemas=proj.get("problemas", ""),
                                questions=proj.get("questions", ""),
                                entities=proj.get("entities", ""),
                                briefing_filename=proj.get("briefing_filename", ""),
                                briefing_text=proj.get("briefing_text", ""),
                                whatsapp_campaign_id=proj.get("whatsapp_campaign_id"),
                                quality_thresholds=proj.get("quality_thresholds"),
                                api_project_id=proj.get("api_project_id"),
                                qr_verification_text=local_verif_text.strip(),
                            )
                            current_verification_text = local_verif_text.strip()

                        clean_target_local = "".join(filter(str.isdigit, target_phone_local))
                        wa_link_local = build_whatsapp_deeplink(clean_target_local, current_verification_text, local_code)

                        col_l_info, col_l_img = st.columns([3, 2])
                        with col_l_info:
                            st.markdown(f"**Link do WhatsApp**: [{wa_link_local}]({wa_link_local})")
                            st.markdown(f"**WhatsApp Destino**: `+{clean_target_local}` | **Código**: `{local_code}`")
                            st.caption(f"**Texto de Verificação**: *\"{current_verification_text}\"*")

                        with col_l_img:
                            try:
                                img_bytes_local = generate_qr_code_bytes(wa_link_local)
                                st.image(img_bytes_local, width=180, caption=f"QR Code: {local_code}")
                                st.download_button(
                                    label="📥 Baixar Imagem (PNG)",
                                    data=img_bytes_local,
                                    file_name=f"qrcode_{proj['id']}_{local_code}.png",
                                    mime="image/png",
                                    key=f"dl_local_qr_{proj['id']}",
                                    use_container_width=True,
                                )
                            except Exception as img_err_loc:
                                st.error(f"Erro ao renderizar QR Code: {img_err_loc}")

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
