"""
Teste Sensorial — Participantes.

Quem participou, de quais sessões, com que qualidade e o que entra na análise:

- matriz participante × condição: a qualidade da sessão em cada condição e as
  repetições;
- sessões: código, condição, janelas e tentativas, a situação em cada camada
  (e o motivo de ficar de fora) e a qualidade;
- decisão sobre uma sessão: incluir ou excluir numa camada (com motivo),
  corrigir o código do participante ou a condição;
- janelas que ficaram de fora por etapa;
- perfil de cada participante (sexo, grupo...), para recortar a análise.

Só o código do participante aparece aqui; o nome nunca chega ao app.
"""

import pandas as pd
import streamlit as st
from utils import auth, ui
from utils.icons import page_title

user = auth.require_module("teste_sensorial")
pode_editar = auth.can_write(user)

from utils import sensorial_db, sensorial_design, sensorial_quality
from utils.project_ui import active_project
from utils.sensorial_cache import get_project_model
from utils.sensorial_model import LAYER_LABELS

sensorial_db.init_db()
project = active_project(sensorial_db, "ts_project_id", "modules/teste_sensorial/projetos.py")
project_id = project["id"]
DECISION_LAYERS = {"todas": "Todas as camadas", **LAYER_LABELS}


def _text(value) -> str:
    """Célula da grade como texto: vazio, None e NaN viram ""."""
    if value is None or (isinstance(value, float) and value != value):
        return ""
    return str(value).strip()

STATUS_LABELS = {"auto": "Automático", "incluida": "Incluir", "excluida": "Excluir"}

ui.inject_theme()
ui.breadcrumb("Teste Sensorial", project["name"], "Participantes")
page_title("users-three", "Participantes", "Sessões, qualidade e o que entra na análise, por camada.")

model = get_project_model(project)
sessions = model["sessions"]
if sessions.empty:
    st.info("Os participantes aparecem depois que as saídas do pipeline forem gravadas em Uploads.")
    st.stop()

labels = sensorial_design.condition_labels(model["design"])
quality = sensorial_quality.session_quality(model, sensorial_quality.thresholds_for_project(project))
table = sessions.merge(quality, on="sessao_id", how="left")

coded = table[table["participant_code"].notna()]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Participantes", int(coded["participant_code"].nunique()))
c2.metric("Sessões", len(table))
c3.metric("Sessões no EEG", int(table["incluida_eeg"].sum()))
c4.metric("Sem código", int(table["participant_code"].isna().sum()))

# ------------------------------------------------------------------
# Matriz participante × condição
# ------------------------------------------------------------------
st.subheader("Participante × condição")
matrix_layer = st.radio("Camada", list(LAYER_LABELS), format_func=LAYER_LABELS.get, horizontal=True,
                        key="ts_part_matrix_layer")
st.caption("Qualidade da sessão na camada: ✓ OK, ! atenção, ✗ problema, — não se aplica. “×2” = mais de uma "
           "sessão na condição. Entre parênteses, sessão que está fora da análise nessa camada.")
cells = {}
for (code, condition), group in coded[coded["condicao"] != ""].groupby(["participant_code", "condicao"]):
    statuses = [s for s in group["qualidade_" + matrix_layer].fillna("na") if s != "na"]
    worst = sensorial_quality.compute_overall_status([{"status": s} for s in statuses]) if statuses else "na"
    text = sensorial_quality.STATUS_ICONS.get(worst, "—")
    if len(group) > 1:
        text += " ×{}".format(len(group))
    if not group["incluida_" + matrix_layer].any():
        text = "({})".format(text)
    cells[(code, condition)] = text
conditions = [c["codigo"] for c in model["design"].get("condicoes") or []]
matrix = pd.DataFrame([{"participante": code, **{labels.get(c, c): cells.get((code, c), "") for c in conditions}}
                       for code in sorted(coded["participant_code"].unique())])
st.dataframe(matrix, hide_index=True, width="stretch", height=min(38 + 35 * len(matrix), 520))

# ------------------------------------------------------------------
# Sessões
# ------------------------------------------------------------------
st.subheader("Sessões")
f1, f2 = st.columns([2, 3])
condition_filter = f1.multiselect("Condição", conditions, format_func=lambda c: labels.get(c, c),
                                  placeholder="Todas", key="ts_part_conditions")
flags = f2.multiselect("Mostrar só", ["com problema de qualidade", "repetidas", "fora do EEG", "sem código"],
                       placeholder="Todas as sessões", key="ts_part_flags")
view = table
if condition_filter:
    view = view[view["condicao"].isin(condition_filter)]
if "com problema de qualidade" in flags:
    view = view[view["qualidade"] == "fail"]
if "repetidas" in flags:
    view = view[view["repetida"]]
if "fora do EEG" in flags:
    view = view[~view["incluida_eeg"]]
if "sem código" in flags:
    view = view[view["participant_code"].isna()]


def _layer(row, layer: str) -> str:
    if row["incluida_" + layer]:
        return "entra"
    return "fora: {}".format(row["motivo_" + layer] or "—")


shown = pd.DataFrame({
    "sessão": view["sessao_id"],
    "participante": view["participant_code"].fillna("—"),
    "condição": view["condicao"].map(lambda c: labels.get(c, c) if c else "fora do desenho"),
    "experimento": view["experimento"],
    "data": view["data"],
    "janelas EEG": view["janelas_eeg_analise"],
    "janelas periféricos": view["janelas_perifericos_analise"],
    "tentativas": view["tentativas"],
    "repetida": view["repetida"].map({True: "sim", False: ""}),
    "EEG": [_layer(row, "eeg") for _, row in view.iterrows()],
    "FC": [_layer(row, "fc") for _, row in view.iterrows()],
    "GSR": [_layer(row, "gsr") for _, row in view.iterrows()],
    "associação": [_layer(row, "associacao") for _, row in view.iterrows()],
    "qualidade": view["qualidade"].map(sensorial_quality.badge),
    "qualidade EEG": view["detalhe_eeg"],
    "qualidade FC": view["detalhe_fc"],
})
st.dataframe(shown, hide_index=True, width="stretch")
st.caption("{} de {} sessões.".format(len(view), len(table)))

# ------------------------------------------------------------------
# Decisão sobre uma sessão
# ------------------------------------------------------------------
if pode_editar:
    st.subheader("Decidir sobre uma sessão")
    st.caption("A decisão vale por cima das regras automáticas e da BASE LIMPA. Excluir pede um motivo, que aparece "
               "nas limitações da análise e nas exportações.")

    def _session_label(sessao_id: str) -> str:
        row = table[table["sessao_id"] == sessao_id].iloc[0]
        return "{} · {} · {} · {}".format(row["participant_code"] if isinstance(row["participant_code"], str) else
                                           "sem código", labels.get(row["condicao"], row["condicao"] or "fora"),
                                           row["data"] or "", sessao_id)

    chosen = st.selectbox("Sessão", [None] + list(view["sessao_id"]),
                          format_func=lambda s: _session_label(s) if s else "— escolha —", key="ts_part_session")
    if chosen:
        current = {row["layer"]: row for row in sensorial_db.list_session_overrides(project_id)
                   if row["sessao_id"] == chosen}
        d1, d2 = st.columns(2)
        layer = d1.selectbox("Camada", list(DECISION_LAYERS), format_func=DECISION_LAYERS.get, key="ts_part_layer")
        existing = current.get(layer) or {}
        status = d2.selectbox("Decisão", list(STATUS_LABELS), format_func=STATUS_LABELS.get,
                              index=list(STATUS_LABELS).index(existing.get("status") or "auto"), key="ts_part_status")
        reason = st.text_input("Motivo", value=existing.get("reason") or "", key="ts_part_reason",
                               placeholder="Ex.: eletrodo solto na pós-olfação")
        general = current.get("todas") or {}
        o1, o2 = st.columns(2)
        code_override = o1.text_input("Corrigir o código do participante", key="ts_part_code",
                                      value=general.get("participant_code_override") or "",
                                      help="Vale para todas as camadas. Vazio = o código lido dos dados.")
        options = [""] + conditions
        condition_override = o2.selectbox(
            "Corrigir a condição", options, format_func=lambda c: labels.get(c, c) if c else "— a dos dados —",
            index=options.index(general.get("condition_override")) if general.get("condition_override") in options
            else 0, key="ts_part_condition")
        if st.button("Salvar decisão", type="primary", key="ts_part_save"):
            try:
                if layer != "todas" and (code_override or condition_override):
                    sensorial_db.set_session_status(
                        project_id, chosen, layer="todas", status=general.get("status") or "auto",
                        reason=general.get("reason") or "", participant_code_override=code_override,
                        condition_override=condition_override)
                sensorial_db.set_session_status(
                    project_id, chosen, layer=layer, status=status, reason=reason,
                    participant_code_override=code_override if layer == "todas" else existing.get(
                        "participant_code_override"),
                    condition_override=condition_override if layer == "todas" else existing.get("condition_override"))
            except (auth.AuthorizationError, ValueError) as error:
                st.error(str(error))
            else:
                st.toast("Decisão salva; a análise foi recalculada.")
                st.rerun()

# ------------------------------------------------------------------
# Janelas que ficaram de fora
# ------------------------------------------------------------------
eeg = model["eeg"]
if not eeg.empty:
    st.subheader("Janelas de EEG por etapa")
    stages = sensorial_design.analysis_stages(model["design"])
    windows = eeg[eeg["Etapa"].isin(stages) & eeg["participant_code"].notna() & (eeg["condicao"] != "")]
    summary = windows.groupby(["participant_code", "condicao", "Etapa"]).agg(
        janelas=("incluida", "size"), incluidas=("incluida", "sum")).reset_index()
    summary["de fora"] = summary["janelas"] - summary["incluidas"]
    reasons = windows[~windows["incluida"]].groupby(["participant_code", "condicao", "Etapa"])["motivo"].agg(
        lambda values: "; ".join("{} ({})".format(k, v) for k, v in values.value_counts().items()))
    summary["motivos"] = [reasons.get((r.participant_code, r.condicao, r.Etapa), "")
                          for r in summary.itertuples()]
    only_out = st.checkbox("Só onde alguma janela ficou de fora", value=True, key="ts_part_only_out")
    if only_out:
        summary = summary[summary["de fora"] > 0]
    stage_labels = {s["codigo"]: s["rotulo"] for s in model["design"].get("etapas") or []}
    st.dataframe(summary.assign(condicao=summary["condicao"].map(lambda c: labels.get(c, c)),
                                Etapa=summary["Etapa"].map(lambda s: stage_labels.get(s, s)))
                 .rename(columns={"participant_code": "participante", "condicao": "condição", "Etapa": "etapa"}),
                 hide_index=True, width="stretch")

# ------------------------------------------------------------------
# Perfil
# ------------------------------------------------------------------
st.subheader("Perfil")
st.caption("Atributos para recortar e comparar a análise (sexo, grupo, usuária...). Uma planilha de perfil enviada em "
           "Uploads preenche o que está vazio; o que for editado aqui não é sobrescrito.")
participants = sensorial_db.list_participants(project_id)
fields = sorted({key for item in participants for key in (item.get("profile") or {})})
new_field = st.text_input("Novo campo de perfil", key="ts_part_new_field", disabled=not pode_editar,
                          placeholder="Ex.: grupo") if pode_editar else ""
if new_field and new_field.strip() and new_field.strip() not in fields:
    fields.append(new_field.strip())
profile_table = pd.DataFrame([{"codigo": item["code"], **{f: (item.get("profile") or {}).get(f, "") for f in fields},
                               "notas": item.get("notes") or ""} for item in participants])
if profile_table.empty:
    st.info("Nenhum participante registrado ainda.")
else:
    edited = st.data_editor(profile_table, hide_index=True, width="stretch",
                            disabled=True if not pode_editar else ["codigo"],
                            column_config={"codigo": st.column_config.TextColumn("Participante")},
                            key="ts_part_profile_{}".format(len(fields)))
    if pode_editar and st.button("Salvar perfil", key="ts_part_profile_save"):
        before = profile_table.set_index("codigo")
        rows = []
        for _, row in edited.iterrows():
            old = before.loc[row["codigo"]]
            if any(_text(row[column]) != _text(old[column]) for column in fields + ["notas"]):
                rows.append({"code": row["codigo"], "profile": {f: _text(row[f]) for f in fields},
                             "notes": _text(row["notas"])})
        if not rows:
            st.info("Nada mudou.")
        else:
            try:
                sensorial_db.upsert_participants(project_id, rows, source="manual")
            except (auth.AuthorizationError, ValueError) as error:
                st.error(str(error))
            else:
                st.toast("{} participante(s) atualizado(s).".format(len(rows)))
                st.rerun()
