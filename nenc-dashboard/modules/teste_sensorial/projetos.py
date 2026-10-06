"""
Teste Sensorial — Lista de Projetos.

Ponto de entrada do módulo. Cada projeto reúne as saídas do pipeline de um
estudo (EEG, periféricos, teste de associação), os participantes, as decisões
sobre as sessões e as análises — o mesmo desenho da Jornada de Compra.
"""

import streamlit as st
from utils import auth, ui
from utils.icons import page_title

user = auth.require_module("teste_sensorial")
pode_editar = auth.can_write(user)

from utils import sensorial_db

sensorial_db.init_db()

# O que depende do projeto aberto e cai junto quando ele muda.
_CHILD_KEYS = ("ts_session_focus",)


def _open_project(project_id: int, destination: str) -> None:
    st.session_state["ts_project_id"] = project_id
    for key in _CHILD_KEYS:
        st.session_state.pop(key, None)
    # As páginas do projeto só entram no menu no rerun seguinte; o salto passa
    # por `_navigate_to`, que o app.py resolve antes de rodar a página.
    st.session_state["_navigate_to"] = destination
    st.rerun()


ui.inject_theme()
ui.breadcrumb("Teste Sensorial", "Projetos")
page_title(
    "folders",
    "Projetos",
    "Cada projeto reúne EEG, sinais periféricos, teste de associação e análises de um estudo.",
)

_, col_btn = st.columns([4, 1])
with col_btn:
    if pode_editar and st.button("Novo Projeto", type="primary", width="stretch"):
        st.session_state.pop("ts_project_id", None)
        st.switch_page("modules/teste_sensorial/preparacao.py")

if not pode_editar:
    st.info("Sua conta tem acesso somente de leitura ao Teste Sensorial.")

projects = sensorial_db.list_projects()

if not projects:
    st.info("Nenhum projeto criado ainda. Clique em **Novo Projeto** para começar.")
else:
    active_id = st.session_state.get("ts_project_id")
    for proj in projects:
        with st.container(border=True):
            c1, c2, c3 = st.columns([6, 1, 1])
            pode_alterar = sensorial_db.user_can_modify_project(proj, user)

            with c1:
                badges = []
                if proj["id"] == active_id:
                    badges.append(ui.status_chip("check", "aberto", tone="accent"))
                if proj.get("organization_name") and user.is_platform_admin:
                    badges.append(ui.status_chip("buildings", proj["organization_name"]))
                if proj.get("categoria"):
                    badges.append(ui.status_chip("folder-open", proj["categoria"]))
                st.markdown(
                    '<span style="display:inline-flex;align-items:center;gap:.45rem;'
                    'flex-wrap:wrap"><strong>{}</strong>{}</span>'.format(proj["name"], "".join(badges)),
                    unsafe_allow_html=True,
                )
                st.caption(
                    "{} participante(s) · {} arquivo(s) · {} análise(s) · atualizado em {}".format(
                        proj.get("n_participants", 0),
                        proj.get("n_files", 0),
                        proj.get("n_analyses", 0),
                        str(proj.get("updated_at") or "")[:16],
                    )
                )

            with c2:
                st.write("")
                if st.button("Abrir", key="ts_open_{}".format(proj["id"]), width="stretch"):
                    _open_project(proj["id"], "modules/teste_sensorial/analise_geral.py")

            with c3:
                st.write("")
                if pode_alterar and st.button("Excluir", key="ts_del_{}".format(proj["id"]), width="stretch"):
                    st.session_state["ts_confirm_del_{}".format(proj["id"])] = True

            if st.session_state.get("ts_confirm_del_{}".format(proj["id"])):
                st.warning(
                    "Excluir **{}**? Arquivos, tabelas, participantes, decisões, análises e o "
                    "material do projeto na base de conhecimento serão removidos "
                    "permanentemente.".format(proj["name"])
                )
                cc1, cc2 = st.columns(2)
                with cc1:
                    if st.button("Confirmar exclusão", key="ts_del_yes_{}".format(proj["id"]), width="stretch"):
                        try:
                            sensorial_db.delete_project(proj["id"])
                        except auth.AuthorizationError as error:
                            st.error(str(error))
                        else:
                            st.session_state.pop("ts_confirm_del_{}".format(proj["id"]), None)
                            if st.session_state.get("ts_project_id") == proj["id"]:
                                st.session_state.pop("ts_project_id", None)
                                st.session_state["_navigate_to"] = "modules/teste_sensorial/projetos.py"
                            st.rerun()
                with cc2:
                    if st.button("Cancelar", key="ts_del_no_{}".format(proj["id"]), width="stretch"):
                        st.session_state.pop("ts_confirm_del_{}".format(proj["id"]), None)
                        st.rerun()
