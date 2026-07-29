"""
Jornada de Compra — Entrevistas Qualitativas.

Gerenciamento e síntese das transcrições de entrevistas do ponto de venda.
"""

import streamlit as st
from utils import auth

user = auth.require_module("jornada_compra")

from utils import jornada_db
from utils.ai_provider import get_openai_client

jornada_db.init_db()

st.title("🗂️ Entrevistas Qualitativas — Jornada de Compra")

project_id = st.session_state.get("jc_project_id")

if not project_id:
    st.warning("⚠️ Nenhum projeto selecionado.")
    if st.button("🎙️ Selecionar Projeto", type="primary"):
        st.switch_page("modules/jornada_compra/projetos.py")
    st.stop()

project = jornada_db.get_project(project_id)
if not project:
    st.error("Projeto não encontrado.")
    st.stop()

st.caption(f"Projeto Ativo: **{project['name']}**")

# ------------------------------------------------------------------
# Adicionar Nova Entrevista
# ------------------------------------------------------------------
with st.expander("➕ Cadastrar Nova Entrevista Qualitativa", expanded=False):
    with st.form("form_nova_entrevista", clear_on_submit=True):
        c1, c2 = st.columns([2, 1])
        with c1:
            tit = st.text_input("Título / Identificação da Entrevista", placeholder="Ex: Entrevista Consumidor 04 - Gôndola Cafe")
        with c2:
            part_id = st.text_input("ID do Participante", placeholder="Ex: P_04")
        txt = st.text_area("Transcrição do Relato / Entrevista", height=150, placeholder="Cole a transcrição da fala do consumidor aqui...")
        
        if st.form_submit_button("💾 Salvar Entrevista", type="primary"):
            if tit.strip() and txt.strip():
                jornada_db.save_interview(project_id, titulo=tit, texto=txt, participante_id=part_id)
                st.success("Entrevista salva com sucesso!")
                st.rerun()
            else:
                st.error("Preencha o título e a transcrição.")

st.divider()

# ------------------------------------------------------------------
# Lista de Entrevistas
# ------------------------------------------------------------------
interviews = jornada_db.get_interviews(project_id)

st.subheader(f"📋 Entrevistas Cadastradas ({len(interviews)})")

if not interviews:
    st.info("Nenhuma entrevista cadastrada para este projeto. Adicione acima ou faça o upload de planilhas na tela de Dados do Projeto.")
else:
    # Botão para Síntese por IA das Entrevistas
    if st.button("🤖 Gerar Resumo Qualitativo de Todas as Entrevistas", type="primary"):
        with st.spinner("Analisando falas dos consumidores com IA..."):
            try:
                client = get_openai_client()
                if client:
                    all_text = []
                    for i in interviews[:10]: # Limitar até 10 para o prompt
                        all_text.append(f"**{i['titulo']} ({i['participante_id']}):**\n{i['texto']}")
                    joined = "\n\n---\n\n".join(all_text)

                    prompt_sys = (
                        "Você é um especialista em pesquisa de neuromarketing e comportamento do consumidor no PDV. "
                        "Sintetize os pontos principais das entrevistas fornecidas, destacando padrões comportamentais, "
                        "drivers de escolha da marca, gatilhos visuais e barreiras de compra citadas."
                    )
                    prompt_usr = f"Sintetize estas entrevistas do ponto de venda:\n\n{joined}"

                    resp = client.responses.create(
                        model="gpt-4.1-mini",
                        instructions=prompt_sys,
                        input=prompt_usr,
                        temperature=0.3,
                        max_output_tokens=1000,
                    )
                    st.session_state[f"jc_interview_summary_{project_id}"] = resp.output_text
                    st.success("Resumo gerado com sucesso!")
                else:
                    st.error("Cliente OpenAI não configurado.")
            except Exception as e:
                st.error(f"Erro ao gerar resumo: {e}")

    summary = st.session_state.get(f"jc_interview_summary_{project_id}")
    if summary:
        with st.container(border=True):
            st.markdown("### 💡 Síntese Qualitativa (IA)")
            st.markdown(summary)

    st.divider()

    for item in interviews:
        with st.expander(f"🗣️ {item['titulo']} — Participante `{item['participante_id'] or 'N/A'}` ({item['created_at']})"):
            st.markdown(item["texto"])
            if st.button("🗑️ Excluir Entrevista", key=f"del_ent_{item['id']}"):
                jornada_db.delete_interview(item["id"])
                st.success("Entrevista excluída.")
                st.rerun()
