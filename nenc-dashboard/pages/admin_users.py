"""Administrator-only user and organization management page."""

import streamlit as st

from utils import auth


actor = auth.require_admin()
active_organization_id = auth.active_organization_id(actor)

# O administrador global pode selecionar "Todas as Organizacoes" (id 0); nesse
# caso a listagem de contas cobre o sistema inteiro.
todas_organizacoes = active_organization_id == 0

st.title("Administracao de usuarios")
st.caption("Organizacao ativa: {}".format(
    "Todas as Organizacoes"
    if todas_organizacoes
    else next(
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
    label = "{} ({}, {})".format(user.name, user.email, state)
    if todas_organizacoes:
        return "{} — {}".format(user.organization_name, label)
    return label


tabs = st.tabs(["Usuarios", "Organizacoes"])
users_tab = tabs[0]
organizations_tab = tabs[1]

with users_tab:
    search = st.text_input("Buscar usuario", placeholder="Nome ou e-mail")
    visible_users = auth.list_users(
        actor,
        organization_id=active_organization_id,
        search=search,
        include_inactive=True,
    )
    creator_names = {user.id: user.name for user in visible_users}


    def _creator_label(user: auth.User) -> str:
        if user.created_by_user_id is None:
            return "—"
        # Um autor fora da listagem atual (por exemplo, de outra organizacao)
        # aparece pelo id, para nao se confundir com uma conta sem autor.
        return creator_names.get(
            user.created_by_user_id, "#{}".format(user.created_by_user_id)
        )

    st.dataframe(
        [
            {
                "Nome": user.name,
                **({"Organizacao": user.organization_name} if todas_organizacoes else {}),
                "E-mail": user.email,
                "Telefone": user.phone,
                "Administrador": user.is_admin,
                "Administrador global": user.is_platform_admin,
                "Ativo": user.is_active,
                "Criado por": _creator_label(user),
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
        # A negativa real esta em auth.update_user/auth.delete_user; aqui a tela
        # apenas nao oferece um controle que o servidor vai recusar.
        pode_gerenciar = auth.can_manage_user(actor, selected_user)
        if not pode_gerenciar:
            st.info(
                "Esta conta pertence a outro administrador da organizacao e "
                "nao pode ser editada ou removida por voce."
            )
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
            saved = st.form_submit_button(
                "Salvar alteracoes", type="primary", disabled=not pode_gerenciar
            )
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
                disabled=not (remove_confirmed and pode_gerenciar),
            ):
                try:
                    auth.delete_user(actor, selected_user.id)
                except (auth.AuthorizationError, ValueError) as error:
                    st.error(str(error))
                else:
                    st.success("Usuario removido.")
                    st.rerun()

with organizations_tab:
    organization_list, organization_names = _organization_options(True)

    if actor.is_platform_admin:
        st.subheader("📋 Organizações Cadastradas")
        st.dataframe(
            [
                {
                    "ID": organization.id,
                    "Nome": organization.name,
                    "Ativa": organization.is_active,
                    "Números WhatsApp": organization.whatsapp_numbers,
                }
                for organization in organization_list
            ],
            hide_index=True,
            use_container_width=True,
        )

        with st.expander("Criar Nova Organização", expanded=False):
            with st.form("create-organization"):
                organization_name = st.text_input("Nome da organização")
                initial_wa_numbers = st.text_area(
                    "Números de WhatsApp Destino (um por linha ou separados por vírgula)",
                    placeholder="Ex:\n5511975218007\n5516981360051",
                    height=80,
                )
                created = st.form_submit_button("Criar organização", type="primary")
            if created:
                try:
                    new_org = auth.create_organization(organization_name, actor=actor)
                    if initial_wa_numbers.strip():
                        auth.update_organization(
                            actor,
                            new_org.id,
                            whatsapp_numbers=initial_wa_numbers.strip(),
                        )
                except (auth.AuthorizationError, ValueError) as error:
                    st.error(str(error))
                else:
                    st.success("Organização criada com sucesso.")
                    st.rerun()

    st.subheader("📱 Configuração da Organização e Números de WhatsApp")
    st.markdown(
        "Cadastre os **números de WhatsApp oficiais da organização** que estarão disponíveis "
        "no seletor de destino ao criar QR Codes para campanhas do NencLex."
    )

    if actor.is_platform_admin:
        managed_organization_id = st.selectbox(
            "Selecionar Organização para Editar",
            [organization.id for organization in organization_list],
            format_func=lambda value: organization_names[value],
        )
        managed_organization = next(
            organization
            for organization in organization_list
            if organization.id == managed_organization_id
        )
    else:
        managed_organization_id = active_organization_id
        managed_organization = next(
            (org for org in organization_list if org.id == managed_organization_id),
            auth.Organization(active_organization_id, actor.organization_name, True, "")
        )

    with st.form("edit-organization-settings"):
        if actor.is_platform_admin:
            edited_org_name = st.text_input("Nome da Organização", value=managed_organization.name)
            desired_active = st.checkbox("Organização ativa", value=managed_organization.is_active)
        else:
            edited_org_name = None
            desired_active = None

        edited_wa_numbers = st.text_area(
            "Números de WhatsApp Destino da Organização (um por linha ou separados por vírgula)",
            value=managed_organization.whatsapp_numbers,
            height=110,
            placeholder="Ex:\n5511975218007\n5516981360051",
            help="Estes números alimentam o seletor de WhatsApp Destino na criação de QR Codes do NencLex."
        )

        saved_org = st.form_submit_button("💾 Salvar Configurações da Organização", type="primary")

    if saved_org:
        try:
            auth.update_organization(
                actor,
                managed_organization_id,
                name=edited_org_name,
                whatsapp_numbers=edited_wa_numbers,
                is_active=desired_active,
            )
        except (auth.AuthorizationError, ValueError) as error:
            st.error(str(error))
        else:
            st.success("Configurações da organização salvas com sucesso!")
            st.rerun()