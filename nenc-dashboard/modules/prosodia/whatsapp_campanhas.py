"""
Prosódia — Campanhas do WhatsApp.

Permite listar campanhas, visualizar status de envio e criar novas campanhas disparando templates.
"""

import streamlit as st
from utils import auth

auth.require_module("prosodia")

import pandas as pd
from utils.whatsapp_api_client import (
    get_campaigns,
    get_campaign,
    get_campaign_contacts,
    create_owned_campaign,
    list_owned_contacts,
    is_configured
)
from utils.organization_data import list_external_resources
from utils import ui
from utils.icons import page_title
from utils.prosodia_db import get_projects, get_project, update_project


def _owned_resource_ids(resource_type: str) -> set[str]:
    return {
        resource["id"]
        for resource in list_external_resources(resource_type)
    }


def _contact_id(contact: dict):
    return contact.get("id", contact.get("contact_id"))


def _update_project_campaign(project: dict, campaign_id) -> None:
    update_project(
        project_id=project["id"],
        name=project["name"],
        especialidade=project.get("especialidade", ""),
        historico=project.get("historico", ""),
        problemas=project.get("problemas", ""),
        questions=project.get("questions", ""),
        entities=project.get("entities", ""),
        briefing_filename=project.get("briefing_filename", ""),
        briefing_text=project.get("briefing_text", ""),
        whatsapp_campaign_id=campaign_id,
        quality_thresholds=project.get("quality_thresholds"),
        api_project_id=project.get("api_project_id"),
    )

# A navegacao de volta vive no menu lateral, na seccao "Coleta via WhatsApp".
ui.inject_theme()
ui.breadcrumb("NencBoost", "Coleta via WhatsApp", "Campanhas")
page_title(
    "megaphone",
    "Campanhas",
    "Disparos de convite vinculados ao projeto.",
)

if not is_configured():
    st.warning("API de WhatsApp não está configurada. Configure a URL e a Chave de API primeiro.")
    if st.button("Ir para Configurações", type="primary"):
        st.switch_page("modules/prosodia/whatsapp_config.py")
    st.stop()

projects = get_projects()

# Obter projeto ativo se houver
project_id = st.session_state.get("pros_project_id")
project = get_project(project_id) if project_id else None
if project_id and project is None:
    st.session_state.pop("pros_project_id", None)
    project_id = None
api_project_id = project.get("api_project_id") if project else None
owned_api_project_ids = _owned_resource_ids("whatsapp_api_project")
if api_project_id is not None and str(api_project_id) not in owned_api_project_ids:
    st.warning("O projeto externo vinculado nao foi registrado para a organizacao ativa.")
    api_project_id = None

if api_project_id:
    # Um selo, nao um alerta: e o contexto da pagina, nao um aviso.
    st.markdown(
        ui.status_chip(
            "funnel",
            "Projeto API #{} · {}".format(api_project_id, project["name"]),
            tone="accent",
        ),
        unsafe_allow_html=True,
    )

# Abas para Campanhas
tab_lista, tab_nova = st.tabs(["Campanhas ativas", "Criar campanha"])

# ---------------------------------------------------------------------------
# TAB 1: Campanhas Ativas & Detalhes
# ---------------------------------------------------------------------------
with tab_lista:
    st.subheader("Histórico de Campanhas")
    
    try:
        owned_campaign_ids = _owned_resource_ids("whatsapp_campaign")
        campanhas = [
            campaign
            for campaign in get_campaigns(project_id=api_project_id)
            if str(campaign.get("id")) in owned_campaign_ids
        ]
        
        if not campanhas:
            st.info("Nenhuma campanha foi criada ainda.")
        else:
            # Lista de campanhas em formato DataFrame para visualização geral
            df_c = pd.DataFrame(campanhas)
            
            # Renomear e formatar
            df_c = df_c.rename(columns={
                "id": "ID",
                "name": "Nome",
                "template_name": "Template",
                "language_code": "Idioma",
                "status": "Status",
                "sent_count": "Enviados",
                "failed_count": "Falhas",
                "created_at": "Data de Criação"
            })
            
            if "Data de Criação" in df_c.columns:
                df_c["Data de Criação"] = pd.to_datetime(df_c["Data de Criação"]).dt.strftime("%d/%m/%Y %H:%M")
                
            # Mapear status para emojis
            status_emojis = {
                "pending": "Pendente",
                "running": "Executando",
                "done": "Finalizado",
                "failed": "Falha"
            }
            if "Status" in df_c.columns:
                df_c["Status"] = df_c["Status"].map(lambda x: status_emojis.get(x, x))
            
            # Reordenar e mostrar
            df_c = df_c[["ID", "Nome", "Template", "Status", "Enviados", "Falhas", "Data de Criação"]]
            st.dataframe(df_c, use_container_width=True, hide_index=True)
            
            # Selecionar uma campanha para ver detalhes individuais e vincular
            st.markdown("---")
            st.subheader("Detalhes e Ações da Campanha")
            
            opcao_campanha = st.selectbox(
                "Selecione uma campanha para gerenciar",
                options=campanhas,
                format_func=lambda x: f"ID #{x['id']} — {x['name']} ({x['status'].upper()})"
            )
            
            if opcao_campanha:
                camp_id = opcao_campanha["id"]
                
                # Buscar detalhes frescos
                with st.spinner("Buscando detalhes da campanha..."):
                    camp_details = get_campaign(camp_id)
                    owned_contact_ids = _owned_resource_ids("whatsapp_contact")
                    camp_contacts = [
                        contact
                        for contact in get_campaign_contacts(camp_id)
                        if str(_contact_id(contact)) in owned_contact_ids
                    ]
                
                # Os quatro numeros soltos viram um selo de estado mais uma
                # barra de entrega: enviados em accent, falhas em cinza.
                total_contacts = len(camp_contacts)
                sent = int(camp_details.get("sent_count") or 0)
                failed = int(camp_details.get("failed_count") or 0)
                denominator = total_contacts or 1

                st.markdown(
                    ui.status_chip(
                        "broadcast",
                        str(camp_details["status"]).upper(),
                        tone="accent",
                    ),
                    unsafe_allow_html=True,
                )
                st.markdown(
                    '<div style="border:1px solid var(--nenc-border);'
                    "border-radius:8px;background:var(--nenc-surface);"
                    'padding:.85rem 1rem;margin:.5rem 0 .9rem">'
                    '<div style="display:flex;justify-content:space-between;'
                    'align-items:baseline;margin-bottom:.5rem">'
                    '<span style="font-size:.75rem;color:var(--nenc-muted)">'
                    "Entregues</span>"
                    '<span style="font-size:1.25rem;font-weight:500">{sent}'
                    '<span style="font-size:.75rem;color:var(--nenc-faint)">'
                    " / {total}</span></span></div>"
                    '<div style="height:5px;border-radius:3px;'
                    "background:var(--nenc-border);display:flex;"
                    'overflow:hidden">'
                    '<div style="width:{pct:.1f}%;'
                    'background:var(--nenc-accent)"></div>'
                    '<div style="width:{fail:.1f}%;'
                    'background:var(--nenc-faint)"></div>'
                    "</div></div>".format(
                        sent=sent,
                        total=total_contacts,
                        pct=100 * sent / denominator,
                        fail=100 * failed / denominator,
                    ),
                    unsafe_allow_html=True,
                )
                
                # Área de vinculação ao projeto local do Dashboard
                st.markdown("#### Vinculação com Projeto Local")
                projetos = projects
                
                # Encontrar qual projeto local já está vinculado a esta campanha
                projeto_vinculado = None
                for p in projetos:
                    if p.get("whatsapp_campaign_id") == camp_id:
                        projeto_vinculado = p
                        break
                
                if projeto_vinculado:
                    st.success(f"Esta campanha está vinculada ao projeto: **{projeto_vinculado['name']}**")
                    if st.button("Desvincular Campanha", key="btn_desvincular"):
                        _update_project_campaign(projeto_vinculado, None)
                        st.info("Campanha desvinculada com sucesso!")
                        st.rerun()
                else:
                    st.warning("Esta campanha não está vinculada a nenhum projeto local.")
                    
                    # Selecionar projeto local para vincular
                    col_proj, col_vin_btn = st.columns([3, 1])
                    with col_proj:
                        proj_opcao = st.selectbox(
                            "Selecione um projeto para vincular",
                            options=projetos,
                            format_func=lambda x: x["name"],
                            key="select_proj_vinculo"
                        )
                    with col_vin_btn:
                        st.write("")
                        st.write("")
                        if st.button("Vincular ao Projeto", type="primary", use_container_width=True):
                            if proj_opcao:
                                _update_project_campaign(proj_opcao, camp_id)
                                st.success(f"Campanha vinculada a **{proj_opcao['name']}**!")
                                st.rerun()
                
                # Lista de contatos vinculados e status de entrega
                st.markdown("#### Status de Entrega por Contato")
                if not camp_contacts:
                    st.info("Nenhum contato nesta campanha.")
                else:
                    df_contacts = pd.DataFrame(camp_contacts)
                    df_contacts = df_contacts.rename(columns={
                        "phone": "Telefone",
                        "name": "Nome",
                        "status": "Status de Envio",
                        "sent_at": "Data/Hora",
                        "error_msg": "Mensagem de Erro"
                    })
                    
                    status_c_emojis = {
                        "pending": "Pendente",
                        "sent": "Enviado",
                        "failed": "Falhou"
                    }
                    if "Status de Envio" in df_contacts.columns:
                        df_contacts["Status de Envio"] = df_contacts["Status de Envio"].map(lambda x: status_c_emojis.get(x, x))
                    
                    if "Data/Hora" in df_contacts.columns:
                        df_contacts["Data/Hora"] = pd.to_datetime(df_contacts["Data/Hora"]).dt.strftime("%d/%m/%Y %H:%M")
                    
                    # Reordenar colunas
                    cols_to_show = ["Telefone", "Nome", "Status de Envio", "Data/Hora"]
                    if "Mensagem de Erro" in df_contacts.columns:
                        cols_to_show.append("Mensagem de Erro")
                    
                    st.dataframe(df_contacts[cols_to_show], use_container_width=True, hide_index=True)
                    
    except Exception as e:
        if "403" in str(e):
            st.error("Erro ao buscar campanhas da API: A Chave de API (X-API-Key) foi recusada pelo servidor (HTTP 403 Forbidden). Verifique e atualize a chave na tela de Configurações da WhatsApp API.")
        else:
            st.error(f"Erro ao buscar campanhas da API: {e}")

# ---------------------------------------------------------------------------
# TAB 2: Criar Nova Campanha
# ---------------------------------------------------------------------------
with tab_nova:
    st.subheader("Criar e Disparar Campanha")
    
    try:
        contatos_disponiveis = list_owned_contacts(limit=1000)
        
        if not contatos_disponiveis:
            st.warning("Nenhum contato cadastrado na API. Cadastre contatos primeiro antes de criar uma campanha.")
            if st.button("Ir para Contatos", type="primary"):
                st.switch_page("modules/prosodia/whatsapp_contatos.py")
        else:
            with st.form("form_nova_campanha"):
                nome_campanha = st.text_input(
                    "Nome da Campanha *",
                    placeholder="Ex: Campanha de Coleta - Pesquisa de Satisfação"
                )
                template_name = st.text_input(
                    "Nome do Template no Meta Business *",
                    placeholder="Ex: nenc_welcome_message",
                    help="O nome exato do template aprovado no painel da Meta."
                )
                language_code = st.text_input(
                    "Código do Idioma *",
                    value="pt_BR",
                    help="Código de idioma do template (Ex: pt_BR, en_US)."
                )
                
                st.markdown("### Selecionar Contatos destinatários")
                st.caption("Marque os contatos que receberão as mensagens desta campanha:")
                
                # Montar checklist de contatos
                contatos_selecionados = []
                for c in contatos_disponiveis:
                    label = f"{c.get('name') or 'Sem Nome'} ({c.get('phone')})"
                    checked = st.checkbox(label, key=f"sel_contact_{c['id']}")
                    if checked:
                        contatos_selecionados.append(c["id"])
                
                submitted_campanha = st.form_submit_button("Disparar Campanha", type="primary")
                
            if submitted_campanha:
                if not nome_campanha.strip():
                    st.error("O **Nome da Campanha** é obrigatório.")
                elif not template_name.strip():
                    st.error("O **Nome do Template** é obrigatório.")
                elif not contatos_selecionados:
                    st.error("Selecione pelo menos um contato para disparar a campanha.")
                else:
                    try:
                        with st.spinner("Criando campanha e disparando mensagens na API..."):
                            created_campaign = create_owned_campaign(
                                name=nome_campanha.strip(),
                                template_name=template_name.strip(),
                                language_code=language_code.strip(),
                                contact_ids=contatos_selecionados,
                                project_id=api_project_id
                            )
                            if not isinstance(created_campaign, dict) or created_campaign.get("id") is None:
                                raise RuntimeError("A API nao retornou o identificador da campanha criada.")
                        st.success(f"Campanha '{nome_campanha}' criada e disparada em background com sucesso!")
                        st.balloons()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erro ao disparar campanha: {e}")
                        
    except Exception as e:
        st.error(f"Erro ao listar contatos disponíveis para a campanha: {e}")
