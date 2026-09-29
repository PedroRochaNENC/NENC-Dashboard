"""
Jornada de Compra — Lista de Projetos.

Ponto de entrada do módulo. Cada projeto agrupa os arquivos de eye tracking,
os participantes, os vídeos e as análises de um estudo — o mesmo desenho do
NencBoost, que a Jornada teve por alguns dias (0dc853f) e perdeu num refactor.
"""

import streamlit as st
from utils import auth, ui
from utils.icons import page_title

user = auth.require_module("jornada_compra")
pode_editar = auth.can_write(user)

from utils import jornada_db
from utils.organization_data import load_module_state

jornada_db.init_db()

# O que depende do projeto aberto e cai junto quando ele muda.
_CHILD_KEYS = ("jc_media_focus",)


def _open_project(project_id: int, destination: str) -> None:
    st.session_state["jc_project_id"] = project_id
    for key in _CHILD_KEYS:
        st.session_state.pop(key, None)
    # As páginas do projeto só entram no menu no rerun seguinte; o salto passa
    # por `_navigate_to`, que o app.py resolve antes de rodar a página.
    st.session_state["_navigate_to"] = destination
    st.rerun()


def _legacy_context() -> dict:
    """Contexto que a versão anterior guardava por organização (jc_projeto).

    Só o contexto e o texto do relatório sobreviveram: os uploads daquela versão
    nunca chegaram a ser gravados.
    """
    try:
        state = load_module_state("jornada_compra") or {}
    except Exception:
        return {}
    projeto = state.get("jc_projeto")
    if not isinstance(projeto, dict) or not str(projeto.get("nome") or "").strip():
        return {}
    return {
        "nome": str(projeto.get("nome") or "").strip(),
        "especialidade": str(projeto.get("especialidade") or ""),
        "historico": str(projeto.get("historico") or ""),
        "problemas": str(projeto.get("problemas") or ""),
        "pptx_text": str(state.get("jc_pptx_text") or ""),
    }


ui.inject_theme()
ui.breadcrumb("Jornada de Compra", "Projetos")
page_title(
    "folders",
    "Projetos",
    "Cada projeto agrupa dados de eye tracking, participantes, vídeos e análises.",
)

_, col_btn = st.columns([4, 1])
with col_btn:
    if pode_editar and st.button("Novo Projeto", type="primary", width="stretch"):
        st.session_state.pop("jc_project_id", None)
        st.switch_page("modules/jornada_compra/preparacao.py")

if not pode_editar:
    st.info("Sua conta tem acesso somente de leitura à Jornada de Compra.")

legacy = _legacy_context() if pode_editar else {}
if legacy:
    with st.expander("Contexto salvo na versão anterior do módulo"):
        st.markdown("**{}**".format(legacy["nome"]))
        for label, key in (
            ("Área", "especialidade"),
            ("Histórico", "historico"),
            ("Problemas centrais", "problemas"),
        ):
            if legacy[key]:
                st.caption("{}: {}".format(label, legacy[key][:300]))
        st.caption(
            "Os arquivos enviados naquela versão não foram gravados: depois de criar "
            "o projeto, envie os dados em Uploads."
        )
        if st.button("Criar projeto com este contexto", key="jc_legacy_import"):
            try:
                new_id = jornada_db.create_project(
                    legacy["nome"],
                    especialidade=legacy["especialidade"],
                    historico=legacy["historico"],
                    problemas=legacy["problemas"],
                    briefing_text=legacy["pptx_text"][:20000],
                    briefing_filename=(
                        "Relatório PPTX (versão anterior)" if legacy["pptx_text"] else ""
                    ),
                )
            except (auth.AuthorizationError, ValueError) as error:
                st.error(str(error))
            else:
                _open_project(new_id, "modules/jornada_compra/preparacao.py")

projects = jornada_db.list_projects()

if not projects:
    st.info("Nenhum projeto criado ainda. Clique em **Novo Projeto** para começar.")
else:
    active_id = st.session_state.get("jc_project_id")
    for proj in projects:
        with st.container(border=True):
            c1, c2, c3 = st.columns([6, 1, 1])
            pode_alterar = jornada_db.user_can_modify_project(proj, user)

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
                    'flex-wrap:wrap"><strong>{}</strong>{}</span>'.format(
                        proj["name"], "".join(badges)
                    ),
                    unsafe_allow_html=True,
                )
                st.caption(
                    "{} participante(s) · {} arquivo(s) · {} vídeo(s) · {} análise(s) · "
                    "atualizado em {}".format(
                        proj.get("n_participants", 0),
                        proj.get("n_files", 0),
                        proj.get("n_media", 0),
                        proj.get("n_analyses", 0),
                        str(proj.get("updated_at") or "")[:16],
                    )
                )
                if proj.get("marca_foco"):
                    st.caption("Marca foco: {}".format(proj["marca_foco"]))

            with c2:
                st.write("")
                if st.button("Abrir", key="jc_open_{}".format(proj["id"]), width="stretch"):
                    _open_project(proj["id"], "modules/jornada_compra/analise_geral.py")

            with c3:
                st.write("")
                if pode_alterar and st.button(
                    "Excluir", key="jc_del_{}".format(proj["id"]), width="stretch"
                ):
                    st.session_state["jc_confirm_del_{}".format(proj["id"])] = True

            if st.session_state.get("jc_confirm_del_{}".format(proj["id"])):
                st.warning(
                    "Excluir **{}**? Arquivos, participantes, vídeos, análises e o "
                    "material do projeto na base de conhecimento serão removidos "
                    "permanentemente.".format(proj["name"])
                )
                cc1, cc2 = st.columns(2)
                with cc1:
                    if st.button(
                        "Confirmar exclusão",
                        key="jc_del_yes_{}".format(proj["id"]),
                        width="stretch",
                    ):
                        try:
                            jornada_db.delete_project(proj["id"])
                        except auth.AuthorizationError as error:
                            st.error(str(error))
                        else:
                            st.session_state.pop("jc_confirm_del_{}".format(proj["id"]), None)
                            if st.session_state.get("jc_project_id") == proj["id"]:
                                st.session_state.pop("jc_project_id", None)
                                st.session_state["_navigate_to"] = (
                                    "modules/jornada_compra/projetos.py"
                                )
                            st.rerun()
                with cc2:
                    if st.button(
                        "Cancelar", key="jc_del_no_{}".format(proj["id"]), width="stretch"
                    ):
                        st.session_state.pop("jc_confirm_del_{}".format(proj["id"]), None)
                        st.rerun()
