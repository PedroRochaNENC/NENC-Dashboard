"""Administrator-only user and organization management page."""

import streamlit as st

from utils import auth


actor = auth.require_admin()
active_organization_id = auth.active_organization_id(actor)

st.title("Administracao de usuarios")
st.caption("Organizacao ativa: {}".format(
    next(
        (
            organization.name
            for organization in auth.list_organizations(
                actor, include_inactive=True
            )
            if organization.id == active_organization_id
        ),
        actor.organization_name,
    )
))


def _organization_options(include_inactive: bool = False):
    organizations = auth.list_organizations(actor, include_inactive=include_inactive)
    return organizations, {organization.id: organization.name for organization in organizations}


def _user_label(user: auth.User) -> str:
    state = "ativo" if user.is_active else "inativo"
    return "{} ({}, {})".format(user.name, user.email, state)


tabs = st.tabs(["Usuarios", "Organizacao"] if actor.is_admin else ["Usuarios"])
users_tab = tabs[0]
organizations_tab = tabs[1] if actor.is_admin else None

with users_tab:
    search = st.text_input("Buscar usuario", placeholder="Nome ou e-mail")
    visible_users = auth.list_users(
        actor,
        organization_id=active_organization_id,
        search=search,
        include_inactive=True,
    )
    st.dataframe(
        [
            {
                "Nome": user.name,
                "E-mail": user.email,
                "Telefone": user.phone,
                "Administrador": user.is_admin,
                "Administrador global": user.is_platform_admin,
                "Ativo": user.is_active,
                "Modulos": ", ".join(auth.MODULE_LABELS[key] for key in user.modules),
            }
            for user in visible_users
        ],
        hide_index=True,
        use_container_width=True,
    )

    with st.expander("Criar usuario", expanded=False):
        organizations, organization_names = _organization_options()
        with st.form("create-user"):
            name = st.text_input("Nome")
            email = st.text_input("E-mail")
            phone = st.text_input("Telefone")
            if actor.is_platform_admin:
                organization_id = st.selectbox(
                    "Organizacao", list(organization_names),
                    format_func=lambda value: organization_names[value],
                )
            else:
                organization_id = actor.organization_id
                st.text_input("Organizacao", value=actor.organization_name, disabled=True)
            password = st.text_input("Senha inicial", type="password")
            module_keys = st.multiselect(
                "Modulos permitidos",
                list(auth.MODULE_KEYS),
                format_func=lambda key: auth.MODULE_LABELS[key],
            )
            is_organization_admin = st.checkbox("Administrador da organizacao")
            is_platform_admin = (
                st.checkbox("Administrador global") if actor.is_platform_admin else False
            )
            submitted = st.form_submit_button("Criar usuario", type="primary")
        if submitted:
            try:
                auth.create_user(
                    name=name,
                    email=email,
                    phone=phone,
                    organization_id=organization_id,
                    password=password,
                    module_keys=module_keys,
                    is_organization_admin=is_organization_admin,
                    is_platform_admin=is_platform_admin,
                    actor=actor,
                )
            except (auth.AuthorizationError, ValueError) as error:
                st.error(str(error))
            else:
                st.success("Usuario criado.")
                st.rerun()

    if visible_users:
        selected_user_id = st.selectbox(
            "Editar usuario",
            [user.id for user in visible_users],
            format_func=lambda user_id: _user_label(
                next(user for user in visible_users if user.id == user_id)
            ),
        )
        selected_user = next(user for user in visible_users if user.id == selected_user_id)
        with st.form("edit-user-{}".format(selected_user.id)):
            edited_name = st.text_input("Nome", value=selected_user.name)
            edited_email = st.text_input("E-mail", value=selected_user.email)
            edited_phone = st.text_input("Telefone", value=selected_user.phone)
            if actor.is_platform_admin:
                all_organizations, all_organization_names = _organization_options(True)
                edited_organization_id = st.selectbox(
                    "Organizacao do usuario",
                    list(all_organization_names),
                    index=list(all_organization_names).index(selected_user.organization_id),
                    format_func=lambda value: all_organization_names[value],
                )
            else:
                edited_organization_id = None
            reset_password = st.text_input(
                "Nova senha", type="password", placeholder="Deixe em branco para manter"
            )
            edited_modules = st.multiselect(
                "Modulos permitidos",
                list(auth.MODULE_KEYS),
                default=list(selected_user.modules),
                format_func=lambda key: auth.MODULE_LABELS[key],
            )
            edited_active = st.checkbox("Conta ativa", value=selected_user.is_active)
            edited_organization_admin = st.checkbox(
                "Administrador da organizacao", value=selected_user.is_organization_admin
            )
            edited_platform_admin = (
                st.checkbox("Administrador global", value=selected_user.is_platform_admin)
                if actor.is_platform_admin
                else None
            )
            saved = st.form_submit_button("Salvar alteracoes", type="primary")
        if saved:
            try:
                auth.update_user(
                    actor,
                    selected_user.id,
                    name=edited_name,
                    email=edited_email,
                    phone=edited_phone,
                    organization_id=edited_organization_id,
                    password=reset_password or None,
                    is_active=edited_active,
                    is_organization_admin=edited_organization_admin,
                    is_platform_admin=edited_platform_admin,
                    module_keys=edited_modules,
                )
            except (auth.AuthorizationError, ValueError) as error:
                st.error(str(error))
            else:
                st.success("Usuario atualizado. As sessoes necessarias foram revogadas.")
                st.rerun()

        with st.expander("Remover usuario", expanded=False):
            remove_confirmed = st.checkbox(
                "Confirmo a remocao permanente desta conta.",
                key="remove-confirm-{}".format(selected_user.id),
            )
            if st.button(
                "Remover usuario",
                key="remove-user-{}".format(selected_user.id),
                disabled=not remove_confirmed,
            ):
                try:
                    auth.delete_user(actor, selected_user.id)
                except (auth.AuthorizationError, ValueError) as error:
                    st.error(str(error))
                else:
                    st.success("Usuario removido.")
                    st.rerun()

if actor.is_admin and organizations_tab:
    with organizations_tab:
        organization_list, organization_names = _organization_options(True)
        st.dataframe(
            [
                {
                    "Nome": organization.name,
                    "Ativa": organization.is_active,
                    "Telefones WhatsApp": ", ".join(organization.whatsapp_numbers) if organization.whatsapp_numbers else "(Nenhum cadastrado)",
                }
                for organization in organization_list
            ],
            hide_index=True,
            use_container_width=True,
        )

        if actor.is_platform_admin:
            with st.expander("➕ Criar Nova Organizacao", expanded=False):
                with st.form("create-organization"):
                    organization_name = st.text_input("Nome da organizacao *")
                    org_phones_input = st.text_input(
                        "Numeros de WhatsApp da Organizacao (separados por virgula)",
                        placeholder="Ex: 5511975218007, 5516981360051",
                        help="Numeros de atendimento oficiais da organizacao que serao exibidos na selecao de QR Codes.",
                    )
                    created = st.form_submit_button("Criar organizacao", type="primary")
                if created:
                    try:
                        raw_list = [p.strip() for p in org_phones_input.split(",") if p.strip()]
                        auth.create_organization(organization_name, whatsapp_numbers=raw_list, actor=actor)
                    except (auth.AuthorizationError, ValueError) as error:
                        st.error(str(error))
                    else:
                        st.success("Organizacao criada.")
                        st.rerun()

        if organization_list:
            managed_organization_id = st.selectbox(
                "Gerenciar organizacao",
                [organization.id for organization in organization_list],
                format_func=lambda value: organization_names[value],
            )
            managed_organization = next(
                organization
                for organization in organization_list
                if organization.id == managed_organization_id
            )
            with st.form("edit-organization-{}".format(managed_organization.id)):
                edited_org_name = st.text_input("Nome da organizacao", value=managed_organization.name, disabled=not actor.is_platform_admin)
                current_phones_str = ", ".join(managed_organization.whatsapp_numbers)
                edited_org_phones = st.text_input(
                    "Numeros de WhatsApp da Organizacao (separados por virgula)",
                    value=current_phones_str,
                    help="Lista de numeros de atendimento oficiais da organizacao disponiveis para os QR Codes.",
                )
                if actor.is_platform_admin:
                    desired_active = st.checkbox(
                        "Organizacao ativa",
                        value=managed_organization.is_active,
                        key="organization-active-{}".format(managed_organization.id),
                    )
                else:
                    desired_active = managed_organization.is_active

                saved_org = st.form_submit_button("Salvar dados da organizacao", type="primary")

            if saved_org:
                try:
                    raw_list = [p.strip() for p in edited_org_phones.split(",") if p.strip()]
                    auth.update_organization(
                        actor,
                        managed_organization.id,
                        name=edited_org_name if actor.is_platform_admin else None,
                        is_active=desired_active if actor.is_platform_admin else None,
                        whatsapp_numbers=raw_list,
                    )
                except (auth.AuthorizationError, ValueError) as error:
                    st.error(str(error))
                else:
                    st.success("Dados da organizacao atualizados com sucesso.")
                    st.rerun()