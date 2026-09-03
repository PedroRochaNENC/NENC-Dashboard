"""
Administração — usuários e organizações.

Duas correções em relação à versão anterior: a página passa a usar português
acentuado, como o resto do produto, e as três colunas booleanas
(Administrador, Administrador global, Ativo) viram um papel único mais uma
situação. Editar e remover saem de um selectbox abaixo da tabela e passam a
agir sobre a linha selecionada.
"""

import streamlit as st

from utils import auth, ui
from utils.icons import icon, page_title

actor = auth.require_admin()
active_organization_id = auth.active_organization_id(actor)

# O administrador global pode selecionar "Todas as Organizações" (id 0); nesse
# caso a listagem de contas cobre o sistema inteiro e a organização de cada
# conta vira uma coluna própria.
todas_organizacoes = active_organization_id == 0

ui.inject_theme()
ui.breadcrumb("Administração", "Usuários")

active_organization_name = (
    "Todas as Organizações"
    if todas_organizacoes
    else next(
        (
            organization.name
            for organization in auth.list_organizations(actor, include_inactive=True)
            if organization.id == active_organization_id
        ),
        actor.organization_name,
    )
)


def _organization_options(include_inactive: bool = False):
    organizations = auth.list_organizations(actor, include_inactive=include_inactive)
    return organizations, {
        organization.id: organization.name for organization in organizations
    }


def _role_label(user: auth.User) -> str:
    """Um papel único no lugar das três colunas booleanas."""
    if user.is_platform_admin:
        return "Admin global"
    if user.is_organization_admin:
        return "Admin da organização"
    return "Pesquisador"


users_tab, organizations_tab = st.tabs(["Usuários", "Organizações"])

# ---------------------------------------------------------------------------
# Usuários
# ---------------------------------------------------------------------------
with users_tab:
    page_title(
        "users-three",
        "Usuários",
        "Organização ativa: {}".format(active_organization_name),
    )

    search = st.text_input(
        "Buscar usuário",
        placeholder="Nome ou e-mail",
        label_visibility="collapsed",
    )
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
        # Um autor fora da listagem atual (por exemplo, de outra organização)
        # aparece pelo id, para não se confundir com uma conta sem autor.
        return creator_names.get(
            user.created_by_user_id, "#{}".format(user.created_by_user_id)
        )

    selection = st.dataframe(
        [
            {
                "Nome": user.name,
                **(
                    {"Organização": user.organization_name}
                    if todas_organizacoes
                    else {}
                ),
                "E-mail": user.email,
                "Telefone": user.phone,
                "Papel": _role_label(user),
                "Situação": "Ativa" if user.is_active else "Inativa",
                "Criado por": _creator_label(user),
                "Módulos": ", ".join(
                    auth.MODULE_LABELS[key] for key in user.modules
                ),
            }
            for user in visible_users
        ],
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        key="admin_users_table",
    )

    selected_rows = (
        selection.selection.rows if hasattr(selection, "selection") else []
    )
    selected_user = visible_users[selected_rows[0]] if selected_rows else None

    st.markdown(
        '<div style="display:flex;align-items:center;gap:.5rem;'
        'padding:.55rem .75rem;border-radius:8px;border:1px solid '
        "var(--nenc-border);background:var(--nenc-surface);font-size:.78rem;"
        'color:var(--nenc-muted);margin:.2rem 0 .9rem">{i}{t}</div>'.format(
            i=icon("info", 15),
            t=(
                "Administradores da organização recebem todos os módulos "
                "automaticamente. Contas regulares recebem acesso módulo a módulo."
            ),
        ),
        unsafe_allow_html=True,
    )

    with st.expander("Convidar usuário", expanded=False):
        organizations, organization_names = _organization_options()
        with st.form("create-user"):
            name = st.text_input("Nome")
            email = st.text_input("E-mail")
            phone = st.text_input("Telefone")
            if actor.is_platform_admin:
                organization_id = st.selectbox(
                    "Organização",
                    list(organization_names),
                    format_func=lambda value: organization_names[value],
                )
            else:
                organization_id = actor.organization_id
                st.text_input(
                    "Organização", value=actor.organization_name, disabled=True
                )
            password = st.text_input("Senha inicial", type="password")
            module_keys = st.multiselect(
                "Módulos permitidos",
                list(auth.MODULE_KEYS),
                format_func=lambda key: auth.MODULE_LABELS[key],
            )
            is_organization_admin = st.checkbox("Administrador da organização")
            is_platform_admin = (
                st.checkbox("Administrador global")
                if actor.is_platform_admin
                else False
            )
            submitted = st.form_submit_button("Convidar usuário", type="primary")
        if submitted:
            try:
                auth.create_user(
                    actor=actor,
                    name=name,
                    email=email,
                    phone=phone,
                    password=password,
                    organization_id=organization_id,
                    module_keys=module_keys,
                    is_organization_admin=is_organization_admin,
                    is_platform_admin=is_platform_admin,
                )
            except (auth.AuthorizationError, ValueError) as error:
                st.error(str(error))
            else:
                st.success("Usuário criado.")
                st.rerun()

    if selected_user is None:
        st.caption("Selecione uma linha na tabela para editar ou remover a conta.")
    else:
        # A negativa real está em auth.update_user/auth.delete_user; aqui a tela
        # apenas não oferece um controle que o servidor vai recusar.
        pode_gerenciar = auth.can_manage_user(actor, selected_user)

        st.markdown(
            '<div style="font-size:.68rem;font-weight:600;letter-spacing:.1em;'
            'text-transform:uppercase;color:var(--nenc-faint);'
            'padding:.4rem 0 .3rem">Editando {}</div>'.format(selected_user.name),
            unsafe_allow_html=True,
        )
        if not pode_gerenciar:
            st.info(
                "Esta conta pertence a outro administrador da organização e "
                "não pode ser editada ou removida por você."
            )
        with st.form("edit-user-{}".format(selected_user.id)):
            edited_name = st.text_input("Nome", value=selected_user.name)
            edited_email = st.text_input("E-mail", value=selected_user.email)
            edited_phone = st.text_input("Telefone", value=selected_user.phone)
            if actor.is_platform_admin:
                all_organizations, all_organization_names = _organization_options(True)
                edited_organization_id = st.selectbox(
                    "Organização do usuário",
                    list(all_organization_names),
                    index=list(all_organization_names).index(
                        selected_user.organization_id
                    ),
                    format_func=lambda value: all_organization_names[value],
                )
            else:
                edited_organization_id = None
            reset_password = st.text_input(
                "Nova senha",
                type="password",
                placeholder="Deixe em branco para manter",
            )
            edited_modules = st.multiselect(
                "Módulos permitidos",
                list(auth.MODULE_KEYS),
                default=list(selected_user.modules),
                format_func=lambda key: auth.MODULE_LABELS[key],
            )
            edited_active = st.checkbox("Conta ativa", value=selected_user.is_active)
            edited_organization_admin = st.checkbox(
                "Administrador da organização",
                value=selected_user.is_organization_admin,
            )
            edited_platform_admin = (
                st.checkbox(
                    "Administrador global", value=selected_user.is_platform_admin
                )
                if actor.is_platform_admin
                else None
            )
            saved = st.form_submit_button(
                "Salvar alterações", type="primary", disabled=not pode_gerenciar
            )
        if saved:
            try:
                auth.update_user(
                    actor=actor,
                    user_id=selected_user.id,
                    name=edited_name,
                    email=edited_email,
                    phone=edited_phone,
                    password=reset_password or None,
                    organization_id=edited_organization_id,
                    module_keys=edited_modules,
                    is_active=edited_active,
                    is_organization_admin=edited_organization_admin,
                    is_platform_admin=edited_platform_admin,
                )
            except (auth.AuthorizationError, ValueError) as error:
                st.error(str(error))
            else:
                st.success(
                    "Usuário atualizado. As sessões necessárias foram revogadas."
                )
                st.rerun()

        with st.expander("Remover usuário", expanded=False):
            remove_confirmed = st.checkbox(
                "Confirmo a remoção permanente desta conta.",
                key="remove-confirm-{}".format(selected_user.id),
            )
            if st.button(
                "Remover usuário",
                key="remove-user-{}".format(selected_user.id),
                disabled=not (remove_confirmed and pode_gerenciar),
            ):
                try:
                    auth.delete_user(actor, selected_user.id)
                except (auth.AuthorizationError, ValueError) as error:
                    st.error(str(error))
                else:
                    st.success("Usuário removido.")
                    st.rerun()

# ---------------------------------------------------------------------------
# Organizações
# ---------------------------------------------------------------------------
with organizations_tab:
    page_title("buildings", "Organizações")
    organization_list, organization_names = _organization_options(True)

    if actor.is_platform_admin:
        st.dataframe(
            [
                {
                    "ID": organization.id,
                    "Nome": organization.name,
                    "Situação": "Ativa" if organization.is_active else "Inativa",
                    "Números WhatsApp": organization.whatsapp_numbers,
                }
                for organization in organization_list
            ],
            hide_index=True,
            width="stretch",
        )

        with st.expander("Criar nova organização", expanded=False):
            with st.form("create-organization"):
                organization_name = st.text_input("Nome da organização")
                initial_wa_numbers = st.text_area(
                    "Números de WhatsApp destino "
                    "(um por linha ou separados por vírgula)",
                    placeholder="Ex:\n5511999999999\n5511988888888",
                    height=80,
                )
                created = st.form_submit_button(
                    "Criar organização", type="primary"
                )
            if created:
                try:
                    new_org = auth.create_organization(organization_name, actor=actor)
                    if initial_wa_numbers.strip():
                        auth.update_organization(
                            actor=actor,
                            organization_id=new_org.id,
                            whatsapp_numbers=initial_wa_numbers,
                        )
                except (auth.AuthorizationError, ValueError) as error:
                    st.error(str(error))
                else:
                    st.success("Organização criada com sucesso.")
                    st.rerun()

    st.markdown(
        '<div style="font-size:.68rem;font-weight:600;letter-spacing:.1em;'
        'text-transform:uppercase;color:var(--nenc-faint);'
        'padding:.9rem 0 .3rem">Números de WhatsApp</div>'
        '<p style="font-size:.82rem;line-height:1.55;color:var(--nenc-muted);'
        'margin:0 0 .7rem">Os números oficiais da organização alimentam o '
        "seletor de destino na criação de QR Codes das campanhas do "
        "NencBoost.</p>",
        unsafe_allow_html=True,
    )

    if actor.is_platform_admin:
        managed_organization_id = st.selectbox(
            "Organização a editar",
            [organization.id for organization in organization_list],
            format_func=lambda value: organization_names[value],
        )
        managed_organization = next(
            org for org in organization_list if org.id == managed_organization_id
        )
    else:
        managed_organization_id = active_organization_id
        managed_organization = next(
            (org for org in organization_list if org.id == managed_organization_id),
            auth.Organization(
                active_organization_id, actor.organization_name, True, ""
            ),
        )

    with st.form("edit-organization-settings"):
        if actor.is_platform_admin:
            edited_org_name = st.text_input(
                "Nome da organização", value=managed_organization.name
            )
            desired_active = st.checkbox(
                "Organização ativa", value=managed_organization.is_active
            )
        else:
            edited_org_name = None
            desired_active = None

        edited_wa_numbers = st.text_area(
            "Números de WhatsApp destino (um por linha ou separados por vírgula)",
            value=managed_organization.whatsapp_numbers,
            height=110,
            placeholder="Ex:\n5511999999999\n5511988888888",
            help=(
                "Estes números alimentam o seletor de WhatsApp destino na "
                "criação de QR Codes do NencBoost."
            ),
        )

        saved_org = st.form_submit_button(
            "Salvar configurações da organização", type="primary"
        )

    if saved_org:
        try:
            auth.update_organization(
                actor=actor,
                organization_id=managed_organization_id,
                name=edited_org_name,
                is_active=desired_active,
                whatsapp_numbers=edited_wa_numbers,
            )
        except (auth.AuthorizationError, ValueError) as error:
            st.error(str(error))
        else:
            st.success("Configurações da organização salvas.")
            st.rerun()
