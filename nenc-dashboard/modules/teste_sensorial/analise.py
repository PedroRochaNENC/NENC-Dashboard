"""
Teste Sensorial — Análise de IA.

Diagnóstico inteligente neurocientífico dos dados EEG (atenção, valência, assimetria)
e Periféricos (GSR, HR) com salvamento em banco e histórico de relatórios.
"""

import streamlit as st
from utils import auth

auth.require_module("teste_sensorial")

import pandas as pd
from utils import teste_sensorial_db
from utils.ai_provider import get_openai_client, create_analysis

teste_sensorial_db.init_db()

NEURO_SENSORIAL_SYSTEM_PROMPT = """Você é um neurocientista sênior especialista em pesquisas sensoriais, experiência do consumidor e neuromarketing.
Sua tarefa é analisar os dados fisiológicos de testes sensoriais (EEG, assimetria frontal AWI, atenção, memória, resposta galvânica da pele/GSR e frequência cardíaca).

Ao responder:
1. Forneça uma interpretação fisiológica rigorosa de cada indicador.
2. Identifique os momentos de pico emocional, sobrecarga cognitiva ou queda de atenção por etapa/estímulo.
3. Conecte as métricas ao briefing e aos objetivos de pesquisa do produto.
4. Apresente conclusões estratégicas e recomendações acionáveis de produto/design sensorial.
5. Escreva em português do Brasil de forma clara, executiva e altamente fundamentada.
"""

st.title("🧠 Análise Inteligente — Teste Sensorial")

project_id = st.session_state.get("ts_project_id")

if not project_id:
    st.warning("⚠️ Nenhum projeto selecionado.")
    if st.button("🧪 Ir para Projetos", type="primary"):
        st.switch_page("modules/teste_sensorial/projetos.py")
    st.stop()

project = teste_sensorial_db.get_project(project_id)
if not project:
    st.error("Projeto não encontrado.")
    st.stop()

st.caption(f"Projeto Ativo: **{project['name']}** | Produto: **{project.get('produto_estimulo') or 'N/A'}**")

# Carregar dados
data = st.session_state.get("ts_data")
if not data or not any(k in data for k in ("indicadores", "perifericos", "psd_results")):
    data = teste_sensorial_db.get_dataset(project_id)
    st.session_state["ts_data"] = data

# Sidebar de Controles
with st.sidebar:
    st.header("⚙️ Configurações da IA")
    client = get_openai_client()
    if client:
        st.success("OpenAI Conectado ✅")
    else:
        st.error("Configure OPENAI_API_KEY no .env ❌")

    ai_model = st.selectbox("Modelo", ["gpt-4.1-mini", "gpt-4.1-nano", "gpt-4.1"], index=0, key="ts_ai_model")

    vs_id = project.get("vector_store_id")
    use_kb = False
    if vs_id:
        use_kb = st.toggle("📚 Consultar Base do Produto", value=True, key="ts_use_kb")

tab_ai, tab_history = st.tabs(["🤖 Gerador de Análise Sensorial", "📜 Histórico Salvo no Banco"])

with tab_ai:
    st.subheader("🤖 Diagnóstico Fisiológico Automatizado")

    # Montar texto das métricas
    metrics_summary_text = ""

    if "indicadores" in data and not data["indicadores"].empty:
        ind_df = data["indicadores"]
        m_cols = [c for c in ind_df.columns if c not in ("participante", "tempo", "etapa") and pd.api.types.is_numeric_dtype(ind_df[c])]
        if "etapa" in ind_df.columns and m_cols:
            grouped = ind_df.groupby("etapa")[m_cols].mean().round(4)
            metrics_summary_text += "### Médias dos Indicadores EEG por Etapa\n" + grouped.to_string() + "\n\n"

    if "perifericos" in data and not data["perifericos"].empty:
        per_df = data["perifericos"]
        p_cols = [c for c in per_df.columns if c not in ("participante", "tempo", "etapa") and pd.api.types.is_numeric_dtype(per_df[c])]
        if "etapa" in per_df.columns and p_cols:
            grouped_p = per_df.groupby("etapa")[p_cols].mean().round(4)
            metrics_summary_text += "### Médias de Dados Periféricos por Etapa\n" + grouped_p.to_string() + "\n\n"

    if not metrics_summary_text:
        st.info("Nenhum dado numérico de indicadores ou periféricos encontrado para análise.")
    elif not client:
        st.info("Configure a chave OPENAI_API_KEY no arquivo .env para gerar diagnósticos.")
    else:
        if st.button("🔍 Gerar e Salvar Diagnóstico Neurocientífico", type="primary", key="btn_run_ts_ai"):
            with st.spinner("Analisando padrões neurofisiológicos e salvando relatório no banco..."):
                try:
                    user_prompt = f"""
## Briefing do Projeto Sensorial
- **Nome do Projeto:** {project['name']}
- **Produto/Estímulo:** {project.get('produto_estimulo', 'N/A')}
- **Histórico/Contexto:** {project.get('historico', 'N/A')}
- **Objetivos de Pesquisa:** {project.get('problemas', 'N/A')}
- **Hipóteses/Perguntas:** {project.get('questions', 'N/A')}

## Dados Neurofisiológicos Agrupados
{metrics_summary_text}

Por favor, elabore um diagnóstico neurocientífico completo sobre a experiência sensorial e os resultados fisiológicos obtidos.
"""
                    active_vs = vs_id if use_kb else None

                    res = create_analysis(
                        system_prompt=NEURO_SENSORIAL_SYSTEM_PROMPT,
                        user_prompt=user_prompt,
                        model=ai_model,
                        vector_store_id=active_vs,
                        temperature=0.4,
                        max_tokens=3500,
                    )
                    full_text = res["text"]
                    citations = [c.get("filename", "") for c in res.get("citations", [])]

                    # Salvar no Banco SQLite
                    teste_sensorial_db.save_analysis(project_id, analysis_text=full_text, model=ai_model, citations=citations)
                    st.session_state["ts_last_ai_text"] = full_text
                    st.success("Análise sensorial salva no banco de dados com sucesso!")
                    st.markdown(full_text)
                except Exception as e:
                    st.error(f"Erro ao gerar análise: {e}")

        if st.session_state.get("ts_last_ai_text"):
            st.divider()
            st.markdown("### 📄 Diagnóstico Recente")
            st.markdown(st.session_state["ts_last_ai_text"])

with tab_history:
    st.subheader("📜 Histórico de Análises Salvas no Banco")
    history = teste_sensorial_db.get_analyses(project_id)
    if not history:
        st.info("Nenhuma análise salva para este projeto ainda.")
    else:
        for item in history:
            with st.expander(f"🧠 Análise Sensorial de {item['created_at']} (Modelo: {item['model']})"):
                st.markdown(item["analysis_text"])
                if item.get("citations"):
                    st.caption(f"📎 Fontes: {', '.join(item['citations'])}")

# Navegação
st.divider()
col_nav1, col_nav2 = st.columns(2)
with col_nav1:
    if st.button("⬅️ Voltar para Timeline", use_container_width=True):
        st.switch_page("modules/teste_sensorial/timeline.py")
with col_nav2:
    if st.button("⬅️ Voltar aos Projetos", use_container_width=True):
        st.switch_page("modules/teste_sensorial/projetos.py")
