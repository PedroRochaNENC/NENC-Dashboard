"""
Jornada de Compra — Entrevistas Qualitativas.

Transcrições das conversas com os participantes. Não entram nas métricas de
atenção; vão para o contexto da análise de IA, que as trata como evidência
para triangular com o eye tracking.
"""

import streamlit as st
from utils import auth, ui
from utils.icons import page_title

user = auth.require_module("jornada_compra")
pode_editar = auth.can_write(user)

from utils import jornada_db
from utils.jornada_ui import active_project

jornada_db.init_db()
project = active_project()

ui.inject_theme()
ui.breadcrumb("Jornada de Compra", project["name"], "Entrevistas")
page_title(
    "list-bullets",
    "Entrevistas Qualitativas",
    "Transcrições que entram no contexto da análise de IA.",
)

if pode_editar:
    with st.expander("Cadastrar entrevista", expanded=False):
        with st.form("jc_form_entrevista", clear_on_submit=True):
            c1, c2 = st.columns([2, 1])
            with c1:
                titulo = st.text_input(
                    "Título / identificação", placeholder="Ex: Entrevista pós-jornada — loja 1"
                )
            with c2:
                participante = st.text_input("Participante", placeholder="Ex: Pt04")
            texto = st.text_area(
                "Transcrição", height=160, placeholder="Cole aqui a fala do participante..."
            )
            if st.form_submit_button("Salvar entrevista", type="primary"):
                try:
                    jornada_db.add_interview(project["id"], titulo, texto, participante)
                except (auth.AuthorizationError, ValueError) as error:
                    st.error(str(error))
                else:
                    st.toast("Entrevista salva.")
                    st.rerun()

interviews = jornada_db.list_interviews(project["id"])
st.subheader("Entrevistas cadastradas ({})".format(len(interviews)))

if not interviews:
    st.info("Nenhuma entrevista cadastrada para este projeto.")

confirm_key = "jc_interview_confirm_{}".format(project["id"])
for item in interviews:
    header = "{} — {} ({})".format(
        item["titulo"], item.get("participante_id") or "sem participante", item["created_at"]
    )
    with st.expander(header):
        st.markdown(item["texto"])
        if pode_editar and st.button("Excluir", key="jc_int_del_{}".format(item["id"])):
            st.session_state[confirm_key] = item["id"]
            st.rerun()

pending = st.session_state.get(confirm_key)
if pending and pode_editar:
    st.warning("Excluir a entrevista selecionada? Esta ação não pode ser desfeita.")
    cc1, cc2 = st.columns(2)
    with cc1:
        if st.button("Confirmar exclusão", key="jc_int_del_yes", width="stretch"):
            try:
                jornada_db.delete_interview(project["id"], pending)
            except auth.AuthorizationError as error:
                st.error(str(error))
            st.session_state.pop(confirm_key, None)
            st.rerun()
    with cc2:
        if st.button("Cancelar", key="jc_int_del_no", width="stretch"):
            st.session_state.pop(confirm_key, None)
            st.rerun()
