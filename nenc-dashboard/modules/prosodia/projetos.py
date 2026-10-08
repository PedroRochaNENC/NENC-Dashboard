"""
NencBoost — Todos os projetos.

Uma linha por projeto, ordenada pela última entrada. "Abrir" leva ao Resumo
do projeto; Dados do Projeto, QR codes e Excluir ficam no menu "Mais" da
linha. O gerenciamento de QR codes, que vivia num expander dentro de cada
card, passou para a página QR codes do projeto (`qr_codes.py`).
"""

import html

import streamlit as st
from utils import auth, ui
from utils.icons import page_title

user = auth.require_module("prosodia")

# A negativa de verdade esta em prosodia_db/whatsapp_api_client; aqui so
# evitamos oferecer ao leitor controles que o servidor vai recusar.
pode_editar = auth.can_write(user)

from utils import prosodia_summary as summary
from utils.organization_data import claim_external_resource
from utils.prosodia_db import (
    delete_project,
    get_projects,
    init_db,
    user_can_modify_project,
)
from utils.prosodia_project_types import (
    ENTREVISTA_QUALITATIVA,
    PESQUISA_OPINIAO,
    normalize_project_type,
)

init_db()

_TYPE_FILTER_LABELS = {
    "todos": "Todos",
    ENTREVISTA_QUALITATIVA: "Entrevistas Qualitativas",
    PESQUISA_OPINIAO: "Pesquisas de Opinião",
}
# Selo da linha: ícone, rótulo curto e tom.
_TYPE_CHIPS = {
    ENTREVISTA_QUALITATIVA: ("users-three", "Entrevista Qualitativa", "muted"),
    PESQUISA_OPINIAO: ("file-audio", "Pesquisa de Opinião", "accent"),
}
_GREEN = "#7bc0a8"


def _open(project_id: int, page: str = "modules/prosodia/resumo.py") -> None:
    """Abre o projeto numa página dele.

    As páginas do projeto só entram no menu no rerun seguinte, então o salto
    passa por `_navigate_to`, que `app.py` resolve antes de rodar a página.
    """
    st.session_state["pros_project_id"] = project_id
    st.session_state.pop("pros_audio_id", None)
    st.session_state["_navigate_to"] = page


def _new_project() -> None:
    st.session_state.pop("pros_project_id", None)
    st.session_state["_navigate_to"] = "modules/prosodia/preparacao.py"


def _stat(value: str, label: str, color: str = "var(--nenc-text)", size: str = ".95rem") -> str:
    return (
        '<div style="display:flex;flex-direction:column;gap:.1rem">'
        '<span style="font-size:{s};color:{c};font-variant-numeric:tabular-nums">{v}</span>'
        '<span style="font-size:.58rem;letter-spacing:.08em;text-transform:uppercase;'
        'color:var(--nenc-faint)">{l}</span></div>'.format(v=value, l=label, c=color, s=size)
    )


ui.inject_theme()
ui.breadcrumb("NencBoost", "Projetos")

col_title, col_actions = st.columns([3, 2], vertical_alignment="center")
with col_title:
    page_title("folders", "Projetos", "Cada projeto agrupa áudios e análises.")
with col_actions:
    from utils.whatsapp_api_client import is_configured, test_connection

    if is_configured():
        api_online, _ = test_connection()
        api_status = (
            ui.status_chip("plug", "API conectada", tone="accent")
            if api_online
            else ui.status_chip("plug", "API offline")
        )
    else:
        api_status = ui.status_chip("plug", "API não configurada")
    c_chip, c_new = st.columns([1.2, 1], vertical_alignment="center")
    c_chip.markdown(
        '<div style="display:flex;justify-content:flex-end">{}</div>'.format(api_status),
        unsafe_allow_html=True,
    )
    if pode_editar:
        c_new.button("Novo projeto", type="primary", width="stretch", on_click=_new_project)

if not pode_editar:
    st.info("Sua conta tem acesso somente de leitura ao NencBoost.")

projects = get_projects()

if not projects:
    st.info("Nenhum projeto criado ainda. Clique em **Novo projeto** para começar.")
    st.stop()

activity = summary.project_activity()

c_filter, c_count = st.columns([3, 1.4], vertical_alignment="center")
with c_filter:
    type_filter = st.segmented_control(
        "Tipo de projeto",
        list(_TYPE_FILTER_LABELS),
        format_func=_TYPE_FILTER_LABELS.get,
        default="todos",
        key="pros_type_filter",
        label_visibility="collapsed",
    ) or "todos"
if type_filter != "todos":
    projects = [p for p in projects if normalize_project_type(p.get("tipo_projeto")) == type_filter]


def _last_entry(project: dict):
    stamp = activity.get(project["id"], {}).get("ultima")
    return stamp if stamp is not None and stamp == stamp else None  # NaT != NaT


projects.sort(key=lambda p: (_last_entry(p) is not None, _last_entry(p) or 0, p.get("created_at") or ""),
              reverse=True)
c_count.markdown(
    '<div style="text-align:right;font-size:.75rem;color:var(--nenc-faint)">'
    "{} projeto(s) · pela última entrada</div>".format(len(projects)),
    unsafe_allow_html=True,
)

if not projects:
    st.info("Nenhum projeto deste tipo.")
    st.stop()

for proj in projects:
    pid = proj["id"]
    api_proj_id = proj.get("api_project_id")
    # Editar e excluir dependem da autoria; abrir continua liberado para
    # qualquer administrador da organizacao.
    pode_alterar = user_can_modify_project(proj, user)
    act = activity.get(pid, {})
    n_audios = int(proj.get("n_audios") or 0)
    n_qr = act.get("n_qr", 0)
    last = summary.relative_time(_last_entry(proj))

    with st.container(border=True):
        c_info, c_audios, c_qr, c_last, c_open, c_more = st.columns(
            [5, 0.9, 0.9, 1.4, 1.1, 1.1], vertical_alignment="center"
        )

        with c_info:
            type_icon, type_label, type_tone = _TYPE_CHIPS[normalize_project_type(proj.get("tipo_projeto"))]
            org_name = proj.get("organization_name")
            chips = [
                ui.status_chip(type_icon, type_label, tone=type_tone),
                ui.status_chip("buildings", org_name) if org_name and user.is_platform_admin else "",
                ui.status_chip("plug", "API #{}".format(api_proj_id), tone="accent") if api_proj_id
                else ui.status_chip("plug", "sem API"),
            ]
            detail = "Criado em {}".format(str(proj.get("created_at") or "")[:10])
            if proj.get("especialidade"):
                detail += " · " + html.escape(str(proj["especialidade"])[:70])
            st.markdown(
                '<div style="display:flex;flex-direction:column;gap:.35rem;min-width:0">'
                '<div style="display:flex;align-items:center;gap:.45rem;flex-wrap:wrap">'
                '<strong style="font-size:.92rem">{n}</strong>{c}</div>'
                '<div style="font-size:.7rem;color:var(--nenc-faint);white-space:nowrap;overflow:hidden;'
                'text-overflow:ellipsis">{d}</div></div>'.format(
                    n=html.escape(proj["name"]), c="".join(chips), d=detail
                ),
                unsafe_allow_html=True,
            )

        c_audios.markdown(_stat(summary.number_text(n_audios), "áudios"), unsafe_allow_html=True)
        c_qr.markdown(_stat(str(n_qr) if api_proj_id else "—", "QR codes"), unsafe_allow_html=True)
        if last:
            fresh = last == "agora" or last.endswith("min")
            c_last.markdown(_stat(last, "última entrada", _GREEN if fresh else "var(--nenc-muted)", ".78rem"),
                            unsafe_allow_html=True)
        else:
            c_last.markdown(_stat("sem entradas", "última entrada", "var(--nenc-faint)", ".78rem"),
                            unsafe_allow_html=True)

        c_open.button("Abrir", key="open_{}".format(pid), type="primary", width="stretch",
                      on_click=_open, args=(pid,))

        with c_more:
            with st.popover("Mais", width="stretch"):
                st.button("Dados do Projeto", key="data_{}".format(pid), width="stretch",
                          on_click=_open, args=(pid, "modules/prosodia/preparacao.py"))
                st.button("QR codes", key="qr_{}".format(pid), width="stretch",
                          on_click=_open, args=(pid, "modules/prosodia/qr_codes.py"))
                if pode_alterar:
                    st.button("Excluir projeto", key="del_{}".format(pid), width="stretch",
                              on_click=lambda key="confirm_del_{}".format(pid): st.session_state.__setitem__(key, True))

        # Confirmação de exclusão
        if st.session_state.get("confirm_del_{}".format(pid)):
            st.warning(
                f"Tem certeza que deseja excluir **{proj['name']}**? "
                "Todos os áudios e análises serão removidos permanentemente."
            )

            excluir_na_api = False
            if api_proj_id:
                excluir_na_api = st.checkbox(
                    "Excluir também na API",
                    value=False,
                    key=f"excluir_api_cb_{pid}",
                )

            cc1, cc2 = st.columns(2)
            with cc1:
                if st.button("Confirmar exclusão", key=f"confirm_yes_{pid}", width="stretch"):
                    if api_proj_id and excluir_na_api:
                        try:
                            from utils.whatsapp_api_client import delete_api_project

                            claim_external_resource(
                                "whatsapp_api_project",
                                api_proj_id,
                                {"project_id": pid},
                            )
                            delete_api_project(api_proj_id)
                        except Exception as e:
                            st.error(f"Erro ao excluir projeto na API: {e}")
                            # Não interrompe para garantir que a exclusão local ocorra
                    delete_project(pid)
                    st.session_state.pop(f"confirm_del_{pid}", None)
                    st.rerun()
            with cc2:
                if st.button("Cancelar", key=f"confirm_no_{pid}", width="stretch"):
                    st.session_state.pop(f"confirm_del_{pid}", None)
                    st.rerun()
