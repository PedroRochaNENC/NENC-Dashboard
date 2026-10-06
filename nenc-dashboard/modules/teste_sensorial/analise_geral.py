"""
Teste Sensorial — Análise Geral.

A leitura consolidada do estudo, no molde da Análise Geral da Jornada:

- Resumo: por amostra e claim, o que cérebro, corpo e teste explícito
  confirmam; os achados (só é "diferença" o que passa na correção de Holm) e
  as limitações;
- EEG: cada índice por condição e etapa, as comparações pareadas (× basal,
  × controle, × amostra), a matriz de resultados e os topomapas;
- Periféricos: o mesmo para frequência cardíaca, variabilidade, condutância,
  índice emocional e conforto;
- Associação: Score, faixas e quadrantes de cada claim;
- Amostra e qualidade: quantos participantes em cada condição e camada, e o
  que ficou de fora e por quê;
- IA: o relatório gerado a partir dessas métricas (rápido ou aprofundado),
  com a base de conhecimento do Teste Sensorial da organização, o histórico e
  um chat sobre cada análise.

O recorte por perfil vale para todas as seções. Cada gráfico tem a tabela
equivalente logo abaixo.
"""

import pandas as pd
import streamlit as st
from utils import auth, ui
from utils.icons import page_title

user = auth.require_module("teste_sensorial")
pode_editar = auth.can_write(user)

from utils import sensorial_charts as charts
from utils import sensorial_db, sensorial_design
from utils.ai_provider import get_openai_client, get_sensorial_vector_store_id
from utils.project_ui import active_project
from utils.sensorial_ai import AI_MODELS, MODE_LABELS, chat_answer, filters_text, generate_analysis, send_analysis_to_kb
from utils.sensorial_cache import get_image, get_project_metrics, get_project_model
from utils.sensorial_model import LAYER_LABELS

sensorial_db.init_db()
project = active_project(sensorial_db, "ts_project_id", "modules/teste_sensorial/projetos.py")

SECTIONS = ["Resumo", "EEG", "Periféricos", "Associação", "Amostra e qualidade", "IA"]
CONCLUSIONS = {"validado": "✓ validado", "parcial": "◐ parcial", "não validado": "✗ não validado"}
COMPARISON_LABELS = {"vs_basal": "× basal", "vs_controle": "× controle", "entre_amostras": "× amostra"}

ui.inject_theme()
ui.breadcrumb("Teste Sensorial", project["name"], "Análise Geral")
page_title("chart-bar", "Análise Geral", project["name"])

model = get_project_model(project)
if model["sessions"].empty:
    st.info("Ainda não há dados neste projeto. Envie as saídas do pipeline em **Uploads**.")
    st.stop()

design = model["design"]
names = model["index_names"]
labels = sensorial_design.condition_labels(design)
stage_labels = {s["codigo"]: s["rotulo"] for s in design.get("etapas") or []}

# ------------------------------------------------------------------
# Recorte por perfil: uma linha acima de tudo que ele recorta
# ------------------------------------------------------------------
participants = model["participants"]
profile_fields = sorted(c[len("perfil_"):] for c in participants.columns if c.startswith("perfil_"))
f1, f2, f3 = st.columns([2, 3, 2])
field = f1.selectbox("Recortar por perfil", [""] + profile_fields, format_func=lambda f: f or "— todos —",
                     key="ts_ag_field", disabled=not profile_fields,
                     help=None if profile_fields else "Cadastre o perfil em Participantes ou envie a planilha.")
values = []
if field:
    options = sorted(v for v in participants["perfil_" + field].dropna().astype(str).unique() if v)
    values = f2.multiselect("Valores", options, key="ts_ag_values", placeholder="Escolha os valores")
compare = f3.selectbox("Comparar grupos de", [""] + profile_fields, format_func=lambda f: f or "— nenhum —",
                       key="ts_ag_compare", disabled=not profile_fields)
filters = {}
if field and values:
    filters["perfil"] = {field: values}
if compare:
    filters["comparar_por"] = compare
metrics = get_project_metrics(project, filters)
if filters.get("perfil"):
    st.caption("Recorte: {} = {} · {} participante(s).".format(field, ", ".join(values), metrics["participantes"]))

section = st.segmented_control("Seção", SECTIONS, default="Resumo", key="ts_ag_section",
                               label_visibility="collapsed") or "Resumo"


def _comparison_table(table: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "comparação": table["tipo"].map(COMPARISON_LABELS),
        "condição": table["condicao_a"].map(lambda c: labels.get(c, c)),
        "contra": [labels.get(b, b) + ("" if e == eb else " ({})".format(stage_labels.get(eb, eb)))
                   for b, e, eb in zip(table["condicao_b"], table["etapa"], table["etapa_b"])],
        "etapa": table["etapa"].map(lambda e: stage_labels.get(e, e)),
        "n": table["n"],
        "mediana": table["mediana_a"],
        "mediana contra": table["mediana_b"],
        "diferença mediana": table["diferenca_mediana"],
        "r": table["r"],
        "p": table["p"],
        "p Holm": table["p_holm"],
        "resultado": table["resultado"],
    })


NUMBER = st.column_config.NumberColumn(format="%.4g")
P_VALUE = st.column_config.NumberColumn(format="%.4f")


def _measure_section(block: dict, measures: list, key: str) -> str:
    """Gráfico por condição e etapa, comparações e matriz de uma família de medidas. Devolve a escolhida."""
    if not measures:
        st.info("Sem medidas desta camada no projeto.")
        return ""
    measure = st.selectbox("Medida", measures, format_func=lambda m: "{} ({})".format(names.get(m, m), m)
                           if names.get(m, m) != m else m, key=key)
    st.plotly_chart(charts.condition_stage_bars(block["resumo"], measure, design, names.get(measure, measure)),
                    width="stretch", key=key + "_bars")
    summary = block["resumo"][block["resumo"]["medida"] == measure]
    if not summary.empty:
        st.dataframe(summary.assign(condicao=summary["condicao"].map(lambda c: labels.get(c, c)),
                                    etapa=summary["etapa"].map(lambda e: stage_labels.get(e, e)))
                     .drop(columns=["medida"]).rename(columns={"condicao": "condição", "media": "média",
                                                               "dp": "desvio", "ep": "erro padrão"}),
                     hide_index=True, width="stretch",
                     column_config={c: NUMBER for c in ("média", "desvio", "erro padrão", "mediana")})
    st.markdown("**Comparações pareadas por participante**")
    st.caption("Wilcoxon de postos sinalizados; p de Holm por família (medida × etapa). “diferença” só com o p de "
               "Holm abaixo de alfa; “tendência” com o p bruto; abaixo do mínimo de pares, só descritivo.")
    table = block["comparacoes"][block["comparacoes"]["medida"] == measure]
    st.plotly_chart(charts.comparison_chart(table, labels, stage_labels), width="stretch", key=key + "_cmp")
    if not table.empty:
        st.dataframe(_comparison_table(table), hide_index=True, width="stretch",
                     column_config={"mediana": NUMBER, "mediana contra": NUMBER, "diferença mediana": NUMBER,
                                    "r": st.column_config.NumberColumn(format="%.2f"), "p": P_VALUE,
                                    "p Holm": P_VALUE})
    with st.expander("Todas as medidas desta camada"):
        st.caption("▲/▼ diferença (Holm) para cima ou para baixo; △/▽ tendência; · sem diferença.")
        matrix = charts.results_matrix(block["comparacoes"], names, labels, stage_labels, measures)
        if matrix.empty:
            st.info("Sem comparações.")
        else:
            st.dataframe(matrix, hide_index=True, width="stretch")
    return measure


# ------------------------------------------------------------------
# Resumo
# ------------------------------------------------------------------
if section == "Resumo":
    findings = metrics["achados"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Participantes", metrics["participantes"])
    c2.metric("Condições", len(design.get("condicoes") or []))
    c3.metric("Diferenças", sum(1 for f in findings if f["tipo"] == "diferença"))
    c4.metric("Tendências", sum(1 for f in findings if f["tipo"] == "tendência"))

    st.subheader("Síntese por amostra e claim")
    synthesis = metrics["sintese"]
    if synthesis.empty:
        st.info("Sem amostras ou claims para sintetizar.")
    else:
        if (synthesis["cerebro"] == "sem indicador").all() and (synthesis["corpo"] == "sem indicador").all():
            st.info("Ligue cada claim aos índices que deveriam mostrar o efeito em **Dados do Projeto → Claims**; "
                    "por enquanto a síntese só tem o teste explícito.")
        pivot = synthesis.assign(condicao=synthesis["condicao"].map(lambda c: labels.get(c, c)),
                                 conclusao=synthesis["conclusao"].map(CONCLUSIONS)) \
            .pivot_table(index="palavra", columns="condicao", values="conclusao", aggfunc="first", sort=False)
        st.dataframe(pivot.reset_index().rename(columns={"palavra": "claim"}), hide_index=True, width="stretch")
        st.caption("Validado: o teste explícito confirma (faixa alta ou muito alta) e o cérebro ou o corpo mostram "
                   "diferença no sentido esperado contra o controle (ou o basal). Parcial: só uma das camadas.")
        with st.expander("Detalhe por camada"):
            st.dataframe(synthesis.assign(condicao=synthesis["condicao"].map(lambda c: labels.get(c, c)))
                         .rename(columns={"condicao": "amostra", "palavra": "claim", "cerebro": "cérebro",
                                          "explicito": "explícito", "conclusao": "conclusão"}),
                         hide_index=True, width="stretch")

    st.subheader("Achados")
    differences = [f for f in findings if f["tipo"] == "diferença"]
    if differences:
        for item in differences:
            st.markdown("- {}".format(item["texto"]))
    else:
        st.info("Nenhuma comparação passou na correção de Holm.")
    trends = [f for f in findings if f["tipo"] == "tendência"]
    if trends:
        with st.expander("Tendências ({}): p bruto abaixo de alfa, sem passar no Holm".format(len(trends))):
            for item in trends:
                st.markdown("- {}".format(item["texto"]))

    st.subheader("Limitações")
    for note in metrics["limitacoes"]:
        st.markdown("- {}".format(note))

# ------------------------------------------------------------------
# EEG
# ------------------------------------------------------------------
elif section == "EEG":
    families = {"Índices do relatório": list(model["indices"]),
                "Indicadores do pipeline": list(model["pipeline_indicators"])}
    family = st.radio("Medidas", [k for k, v in families.items() if v] or ["Índices do relatório"],
                      horizontal=True, key="ts_ag_family")
    _measure_section(metrics["eeg"], families.get(family) or [], "ts_ag_eeg")
    if model["topomaps"]:
        with st.expander("Topomapas do pipeline ({})".format(len(model["topomaps"]))):
            experiments = sorted({t["experimento"] or "" for t in model["topomaps"]})
            chosen = st.selectbox("Experimento", experiments, key="ts_ag_topo_exp")
            items = [t for t in model["topomaps"] if (t["experimento"] or "") == chosen]
            columns = st.columns(4)
            for index, item in enumerate(items):
                content = get_image(project, item["file_id"], 600)
                if content:
                    columns[index % 4].image(content, caption=(item["etapa_arquivo"] or "").replace("_", " "))
    if not metrics["perfil"].empty:
        st.subheader("Comparação por {}".format(filters.get("comparar_por")))
        st.caption("Entre grupos de participantes (não pareada): permutação, com o δ de Cliff como efeito.")
        st.dataframe(metrics["perfil"].assign(medida=metrics["perfil"]["medida"].map(lambda m: names.get(m, m))),
                     hide_index=True, width="stretch")

# ------------------------------------------------------------------
# Periféricos
# ------------------------------------------------------------------
elif section == "Periféricos":
    measures = [m for m in sensorial_design.PERIPHERAL_CODES if m in set(metrics["perifericos"]["medias"]["medida"])]
    quality_notes = [i["message"] for i in model["issues"] if i["code"] == "perifericos_qualidade"]
    for note in quality_notes:
        st.warning(note)
    if not measures and not model["peripherals"].empty:
        st.info("Nenhuma janela dos periféricos passou na qualidade do pipeline (fluxo corrompido ou sem pulso). "
                "Veja a qualidade por sessão em Participantes.")
    else:
        _measure_section(metrics["perifericos"], measures, "ts_ag_peri")

# ------------------------------------------------------------------
# Associação
# ------------------------------------------------------------------
elif section == "Associação":
    association = metrics["associacao"]
    settings = model["settings"]["associacao"]
    table = association["por_condicao"]
    if association.get("agrupado") is not None and st.toggle(
            "Somar as condições agrupadas ({})".format(", ".join(
                "{} = {}".format(k, " + ".join(v)) for k, v in settings["agrupar"].items())), key="ts_ag_pool"):
        table = association["agrupado"]
    if table.empty:
        st.info("O projeto não tem o teste de associação.")
    else:
        st.caption("Score = % de “Sim” × CR médio das respostas “Sim” (o quanto a pessoa respondeu mais rápido que a "
                   "média dela). Faixas: muito alta a partir de {muito_alta}, alta acima de {alta}, baixa acima de "
                   "{baixa}.".format(**settings["faixas"]))
        st.plotly_chart(charts.score_bars(table, design, settings["faixas"]), width="stretch", key="ts_ag_scores")
        st.dataframe(pd.DataFrame({
            "condição": table["condicao"].map(lambda c: labels.get(c, c)), "claim": table["palavra"],
            "tentativas": table["tentativas"], "participantes": table["participantes"],
            "% Sim": table["pct_sim"], "CR do Sim": table["cr_sim"], "Score": table["score"],
            "faixa": table["faixa"], "quadrante": table["quadrante"]}),
            hide_index=True, width="stretch",
            column_config={"% Sim": st.column_config.NumberColumn(format="percent"),
                           "CR do Sim": st.column_config.NumberColumn(format="%.3f"),
                           "Score": st.column_config.NumberColumn(format="%.3f")})
        st.markdown("**Quadrantes**")
        st.plotly_chart(charts.quadrant_chart(table, design, settings["quadrantes"]), width="stretch",
                        key="ts_ag_quadrants")

# ------------------------------------------------------------------
# Amostra e qualidade
# ------------------------------------------------------------------
elif section == "Amostra e qualidade":
    counts = metrics["n_por_condicao"]
    st.subheader("Participantes por condição")
    st.dataframe(counts.assign(condicao=counts["condicao"].map(lambda c: labels.get(c, c)))
                 .rename(columns={"condicao": "condição", "eeg": "EEG", "perifericos": "periféricos",
                                  "associacao": "teste de associação"}), hide_index=True, width="stretch")
    st.subheader("Sessões por camada")
    sessions = model["sessions"]
    rows = []
    for layer, label in LAYER_LABELS.items():
        included = int(sessions["incluida_" + layer].sum())
        reasons = sessions.loc[~sessions["incluida_" + layer], "motivo_" + layer].value_counts()
        rows.append({"camada": label, "incluídas": included, "de fora": len(sessions) - included,
                     "motivos": "; ".join("{} ({})".format(k, v) for k, v in reasons.items())})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    eeg = model["eeg"]
    if not eeg.empty:
        st.subheader("Janelas de EEG")
        stages = sensorial_design.analysis_stages(design)
        in_stage = eeg[eeg["Etapa"].isin(stages)]
        reasons = in_stage.loc[~in_stage["incluida"], "motivo"].value_counts()
        st.caption("{} de {} janelas das etapas da análise entram ({}).".format(
            int(in_stage["incluida"].sum()), len(in_stage),
            "; ".join("{}: {}".format(k, v) for k, v in reasons.items()) or "nenhuma de fora"))
    st.subheader("Avisos do modelo")
    for issue in model["issues"]:
        (st.warning if issue["level"] == "warn" else st.info)(issue["message"])
    st.page_link("modules/teste_sensorial/participantes.py", label="Ver as sessões e decidir em Participantes",
                 icon=":material/group:")

# ------------------------------------------------------------------
# IA
# ------------------------------------------------------------------
else:
    project_id = project["id"]
    analyses = sensorial_db.list_analyses(project_id)
    # Em "Todas as organizações" a base resolvida seria a de quem está logado,
    # não a do projeto: a busca fica desligada.
    kb_store = get_sensorial_vector_store_id() if auth.active_organization_id(user) else None
    recorte = filters_text(filters)
    st.subheader("Análise por IA")
    st.caption("A IA recebe o contexto do projeto e as métricas do recorte — médias, comparações com o p de Holm, o "
               "teste de associação e a síntese —, nunca os dados brutos nem o nome de ninguém. As contas são do "
               "app; a leitura é da IA.")
    use_kb = False
    if pode_editar:
        with st.container(border=True):
            c1, c2, c3 = st.columns([1.4, 1.2, 1.6], vertical_alignment="bottom")
            mode_label = c1.segmented_control(
                "Modo", ["Rápida", "Aprofundada"], default="Rápida", key="ts_ai_mode",
                help="Rápida: uma chamada. Aprofundada: leitura estatística das tabelas e, depois, interpretação "
                     "estratégica (duas chamadas, mais demorada).") or "Rápida"
            ai_model = c2.selectbox("Modelo", AI_MODELS, key="ts_ai_model")
            use_kb = c3.toggle("Usar a base de conhecimento", value=bool(kb_store), disabled=not kb_store,
                               key="ts_ai_kb", help="Busca na literatura da organização e no material deste "
                                                    "projeto.") and bool(kb_store)
            if not auth.active_organization_id(user):
                st.caption("Em “Todas as organizações” a base fica desligada: a busca iria à base da sua "
                           "organização, não à do projeto.")
            elif not kb_store:
                st.caption("A base de conhecimento do Teste Sensorial não está configurada nesta organização.")
            st.caption("Recorte enviado: {}".format(recorte))
            if st.button("Gerar análise", type="primary", key="ts_ai_generate"):
                if get_openai_client() is None:
                    st.error("A OpenAI não está configurada: defina OPENAI_API_KEY no .env e reinicie o app.")
                else:
                    mode = "rapida" if mode_label == "Rápida" else "aprofundada"
                    try:
                        with st.spinner("Gerando a análise {}…".format(mode_label.lower())):
                            result = generate_analysis(project, model, metrics, mode=mode, ai_model=ai_model,
                                                       recorte=recorte, vector_store_id=kb_store if use_kb else None)
                        if not result["text"].strip():
                            st.error("A IA não devolveu texto. Tente de novo.")
                        else:
                            new_id = sensorial_db.save_analysis(
                                project_id, model=ai_model, mode=mode, analysis_text=result["text"],
                                citations=result["citations"], search=result["search"], filters=filters,
                                data_version=project.get("data_version"))
                            st.session_state["ts_ai_pick"] = new_id
                            st.rerun()
                    except Exception as error:
                        st.error("Não foi possível gerar a análise: {}".format(error))

    if not analyses:
        st.info("Nenhuma análise gerada ainda." + (" Escolha o modo e clique em **Gerar análise**." if pode_editar
                                                   else ""))
    else:
        analyses_by_id = {a["id"]: a for a in analyses}
        if st.session_state.get("ts_ai_pick") not in analyses_by_id:
            st.session_state["ts_ai_pick"] = analyses[0]["id"]
        chosen_id = st.selectbox(
            "Análise", list(analyses_by_id), key="ts_ai_pick",
            format_func=lambda aid: "{} · {} · {}{}".format(
                str(analyses_by_id[aid].get("created_at") or "")[:16],
                MODE_LABELS.get(analyses_by_id[aid].get("mode"), analyses_by_id[aid].get("mode") or ""),
                analyses_by_id[aid].get("model") or "", " · na base" if analyses_by_id[aid].get("kb_file_id") else ""))
        analysis = analyses_by_id[chosen_id]
        analysis_recorte = filters_text(analysis.get("filters"))
        if analysis.get("data_version") is not None and analysis["data_version"] != project.get("data_version"):
            st.warning("Os dados do projeto mudaram depois desta análise (arquivos, decisões sobre sessões ou "
                       "configuração): os números dela podem não bater com as seções atuais. {}".format(
                           "Gere uma nova para atualizar." if pode_editar
                           else "Peça uma análise nova a quem edita o projeto."))
        st.caption("Recorte da análise: {}".format(analysis_recorte))
        with st.container(border=True):
            st.markdown(analysis.get("analysis_text") or "")
        with st.expander("Referências da base de conhecimento"):
            ui.knowledge_base_references({"citations": analysis.get("citations"), "search": analysis.get("search")})

        if pode_editar:
            a1, a2, _ = st.columns([1.4, 1.1, 2.5])
            if analysis.get("kb_file_id"):
                a1.caption("Esta análise está na base de conhecimento.")
            elif a1.button("Enviar para a base", key="ts_ai_to_kb", disabled=not kb_store, width="stretch",
                           help="Guarda esta análise na base da organização, marcada como análise deste projeto."):
                try:
                    file_id = send_analysis_to_kb(project, analysis, analysis_recorte, kb_store)
                    sensorial_db.set_analysis_kb_file(project_id, chosen_id, file_id)
                    st.toast("Análise enviada para a base de conhecimento.")
                    st.rerun()
                except Exception as error:
                    st.error("Não foi possível enviar para a base: {}".format(error))
            if a2.button("Excluir", key="ts_ai_delete", width="stretch"):
                st.session_state["ts_ai_confirm"] = chosen_id
                st.rerun()
            if st.session_state.get("ts_ai_confirm") == chosen_id:
                st.warning("Excluir a análise de {}? {}Não dá para desfazer.".format(
                    str(analysis.get("created_at") or "")[:16],
                    "A cópia na base de conhecimento também sai. " if analysis.get("kb_file_id") else ""))
                y, n, _ = st.columns([1.4, 1.1, 2.5])
                if y.button("Confirmar exclusão", type="primary", key="ts_ai_delete_yes", width="stretch"):
                    sensorial_db.delete_analyses(project_id, [chosen_id])
                    for state_key in ("ts_ai_confirm", "ts_ai_pick", "ts_ai_chat_{}".format(chosen_id)):
                        st.session_state.pop(state_key, None)
                    st.rerun()
                if n.button("Cancelar", key="ts_ai_delete_no", width="stretch"):
                    st.session_state.pop("ts_ai_confirm", None)
                    st.rerun()

            st.divider()
            st.markdown("**Perguntas sobre esta análise**")
            st.caption("A conversa usa o relatório e os achados do recorte da análise e fica só nesta sessão.")
            chat_key = "ts_ai_chat_{}".format(chosen_id)
            history = st.session_state.setdefault(chat_key, [])
            for message in history:
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])
            if history and st.button("Limpar conversa", key="ts_ai_chat_clear"):
                st.session_state[chat_key] = []
                st.rerun()
            question = st.chat_input("Pergunte sobre esta análise…", key="ts_ai_chat_input")
            if question:
                if get_openai_client() is None:
                    st.error("A OpenAI não está configurada: defina OPENAI_API_KEY no .env e reinicie o app.")
                else:
                    history.append({"role": "user", "content": question})
                    try:
                        with st.spinner("Pensando…"):
                            answer = chat_answer(analysis.get("analysis_text") or "",
                                                 get_project_metrics(project, analysis.get("filters") or {}),
                                                 history, ai_model=analysis.get("model") or AI_MODELS[0],
                                                 project_id=project_id, vector_store_id=kb_store if use_kb else None)
                        history.append({"role": "assistant", "content": answer})
                        st.rerun()
                    except Exception as error:
                        history.pop()
                        st.error("Não foi possível responder: {}".format(error))
