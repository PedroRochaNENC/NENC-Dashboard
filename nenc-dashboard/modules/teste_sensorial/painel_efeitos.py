"""
Teste Sensorial — Painel de efeitos.

Todas as amostras de todos os projetos da organização, para o P&D relacionar
bases e fragrâncias aos efeitos no cérebro. Em cada índice do relatório, o
efeito de cada amostra contra o controle do próprio projeto, em cada etapa de
exposição, com o IC 95% (utils/sensorial_effects.py). Os filtros vêm da ficha
da amostra, preenchida no Desenho de cada projeto: base/fragrância, categoria
e claims-alvo.

Uma organização por vez: em "Todas as organizações" a página pede uma, e os
efeitos de clientes diferentes nunca aparecem juntos.
"""

import pandas as pd

import streamlit as st
from utils import auth, ui
from utils.icons import page_title

user = auth.require_module("teste_sensorial")

from utils import sensorial_db, sensorial_design, sensorial_effects
from utils.sensorial_cache import get_project_effects
from utils.sensorial_charts import effects_forest

sensorial_db.init_db()

ui.inject_theme()
ui.breadcrumb("Teste Sensorial", "Painel de efeitos")
page_title("chart-bar", "Painel de efeitos",
           "O efeito de cada amostra contra o controle, nos índices do relatório, em todos os projetos da "
           "organização.")

projects = sensorial_db.list_projects()
organization_id = auth.active_organization_id(user)
if not organization_id:
    organizations = {int(p["organization_id"]): str(p.get("organization_name") or p["organization_id"])
                     for p in projects}
    if not organizations:
        st.info("Nenhum projeto do Teste Sensorial ainda.")
        st.stop()
    organization_id = st.selectbox(
        "Organização", sorted(organizations, key=lambda o: organizations[o].casefold()),
        format_func=organizations.get, key="ts_panel_org",
        help="O painel compara projetos de uma organização por vez.")

selected = sensorial_effects.selected_projects(projects, organization_id)
if not selected:
    st.info("Nenhum projeto do Teste Sensorial nesta organização.")
    st.stop()

entries, left_out, configs = [], [], {}
with st.spinner("Calculando os efeitos de {} projeto(s)…".format(len(selected))):
    for project in selected:
        try:
            result = get_project_effects(project)
        except Exception as error:
            left_out.append((project["name"], "não foi possível calcular: {}".format(error)))
            continue
        if result["motivo"]:
            left_out.append((project["name"], result["motivo"]))
            continue
        entries.append({"projeto": project, "efeitos": result["efeitos"],
                        "fichas": sensorial_db.sample_sheets(project)})
        configs[project["name"]] = result["config"]

frame = sensorial_effects.panel_frame(entries)


def _left_out() -> None:
    if not left_out:
        return
    with st.expander("Fora do painel ({})".format(len(left_out))):
        for name, reason in left_out:
            st.markdown("- **{}**: {}".format(name, reason))


if frame.empty:
    st.info("Nenhum projeto desta organização tem amostra com EEG pareado com o controle.")
    _left_out()
    st.stop()

options = sensorial_effects.filter_options(frame)
names = sensorial_effects.catalog_names()
c1, c2, c3 = st.columns(3)
bases = c1.multiselect("Base/fragrância", options["bases"], key="ts_panel_bases",
                       placeholder="Todas" if options["bases"] else "Nenhuma ficha com base")
categories = c2.multiselect("Categoria", options["categorias"], key="ts_panel_categories", placeholder="Todas")
claims = c3.multiselect("Claim-alvo", options["claims"], key="ts_panel_claims",
                        placeholder="Todos" if options["claims"] else "Nenhuma ficha com claim")
c4, c5, c6 = st.columns([2, 2, 3])
stages = c4.multiselect("Etapas", options["etapas"], default=options["etapas"], key="ts_panel_stages")
scale = c5.radio("Escala", list(sensorial_effects.SCALES), format_func=sensorial_effects.SCALES.get,
                 key="ts_panel_scale", horizontal=True)
present = [code for code in sensorial_design.INDEX_CODES if code in set(frame["medida"])]
measures = c6.multiselect("Índices", present, default=present, format_func=lambda c: names.get(c, c),
                          key="ts_panel_measures")
st.caption("A ficha de cada amostra (base, categoria e claims-alvo) fica no Desenho do projeto. Sem ficha, a "
           "amostra só aparece sem filtro de base e de claim; sem categoria na ficha, vale a do projeto.")

with st.expander("Como ler o painel"):
    st.markdown(
        "- Cada ponto é o efeito de uma amostra contra o **controle do próprio projeto**, numa etapa: a diferença "
        "média pareada por participante, com o IC 95% no traço.\n"
        "- **dz pareado** divide a diferença pelo desvio das diferenças entre participantes. Não tem unidade, então "
        "dá para comparar estudos e índices de escalas diferentes (≈ 0,2 pequeno, 0,5 médio, 0,8 grande). O IC do "
        "dz é o da diferença dividido pelo mesmo desvio: aproximado, sem a incerteza do desvio.\n"
        "- **Diferença bruta** fica na unidade do índice: só compare projetos com o mesmo equipamento e a mesma "
        "configuração.\n"
        "- Ponto **cheio**: o projeto deu “diferença” (p < alfa dele, sem correção). Ponto vazado: tendência, sem "
        "diferença ou só descritivo (poucos pares).")

shown = sensorial_effects.apply_filters(frame, bases, categories, claims, stages, measures)
if shown.empty:
    st.info("Nenhuma amostra no recorte.")
    _left_out()
    st.stop()

differences = sensorial_effects.comparability({name: config for name, config in configs.items()
                                               if name in set(shown["projeto"])})
if differences:
    st.warning("Os projetos do recorte não têm a mesma configuração; compare com cuidado.\n\n" + "\n".join(
        "- " + note for note in differences))

samples = shown[["projeto_id", "condicao"]].drop_duplicates()
m1, m2, m3 = st.columns(3)
m1.metric("Projetos", int(shown["projeto_id"].nunique()))
m2.metric("Amostras", len(samples))
m3.metric("Com diferença (p < alfa)", int((shown["resultado"] == "diferença").sum()),
          help="Comparações amostra × controle com diferença, entre as mostradas.")

for measure in [code for code in present if code in set(shown["medida"])]:
    rows = shown[shown["medida"] == measure]
    st.markdown("#### {}".format(names.get(measure, measure)))
    st.plotly_chart(effects_forest(rows, names.get(measure, measure), scale), width="stretch",
                    key="ts_panel_chart_{}".format(measure))

st.divider()
st.markdown("**Tabela**")
columns = sensorial_effects.scale_columns(scale)
table = pd.DataFrame({
    "Projeto": shown["projeto"], "Amostra": shown["amostra"], "Base/fragrância": shown["base"],
    "Categoria": shown["categoria"], "Claims-alvo": shown["claims_alvo"].map("; ".join),
    "Etapa": shown["etapa_rotulo"], "Índice": shown["medida"].map(lambda c: names.get(c, c)),
    "Controle": shown["controle"], "n": shown["n"], "Efeito": shown[columns["efeito"]],
    "IC 95% inf.": shown[columns["inf"]], "IC 95% sup.": shown[columns["sup"]], "p": shown["p"],
    "Resultado": shown["resultado"]})
st.dataframe(table, hide_index=True, width="stretch",
             column_config={name: st.column_config.NumberColumn(format="%.3g")
                            for name in ("Efeito", "IC 95% inf.", "IC 95% sup.", "p")})


def _audit_download(project_ids) -> None:
    for project_id in project_ids:
        sensorial_db.audit_export(int(project_id), "painel_efeitos")


export = shown.assign(claims_alvo=shown["claims_alvo"].map("; ".join),
                      indice=shown["medida"].map(lambda c: names.get(c, c)))
st.download_button("Baixar tabela (CSV)", data=export.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
                   file_name="painel_de_efeitos.csv", mime="text/csv", key="ts_panel_csv",
                   on_click=_audit_download, args=(sorted(shown["projeto_id"].unique()),))
_left_out()
