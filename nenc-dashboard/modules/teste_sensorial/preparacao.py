"""
Teste Sensorial — Novo projeto / Dados do Projeto.

Sem projeto aberto, é o formulário de criação; com projeto aberto, reúne o que
define a análise, em abas:

- Contexto: objetivo, perguntas e briefing (que também vai para a base de
  conhecimento, marcado como material deste projeto);
- Desenho: o que cada sessão é (basal, controle, amostra) e as etapas que
  entram, deduzidos dos dados e corrigíveis aqui;
- Índices: nome de negócio de cada índice e o modo do PPI;
- Limpeza: BASE LIMPA, regra de outliers do SPSS e qualidade dos periféricos;
- Claims: o que confirma cada claim no cérebro e no corpo, e as faixas do
  teste de associação;
- Estatística: mínimo de pares e alfa.

Toda mudança aqui sobe a versão dos dados: a análise é recalculada e as
análises de IA anteriores ficam marcadas como de dados antigos.
"""

from datetime import datetime

import pandas as pd

import streamlit as st
from utils import auth, ui
from utils.icons import page_title

user = auth.require_module("teste_sensorial")
pode_editar = auth.can_write(user)

from utils import sensorial_db, sensorial_design
from utils.ai_provider import add_document_to_vector_store, get_openai_client, get_sensorial_vector_store_id
from utils.briefing import BRIEFING_EXTENSIONS, cap_text, extract_briefing_text
from utils.kb_attributes import project_document

sensorial_db.init_db()

ROLE_LABELS = {"basal": "Basal", "controle": "Controle", "amostra": "Amostra", "ignorar": "Fora da análise"}
STAGE_ROLE_LABELS = {"referencia": "Referência", "exposicao": "Exposição", "pos": "Pós-exposição",
                     "ignorar": "Fora da análise"}
OUT = "(fora da análise)"


def _slugify(text: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(text or ""))
    return safe.strip("_")[:80] or "projeto"


def _codes(text: str) -> list:
    return [part.strip() for part in str(text or "").replace(";", ",").split(",") if part.strip()]


def _upload_briefing_to_kb(filename: str, content: bytes, project_id: int) -> tuple:
    """Manda o briefing para a base do Teste Sensorial, marcado como material do projeto."""
    if auth.active_organization_id(user) == 0:
        return False, ("em \"Todas as organizações\" a base resolvida seria a da sua organização, "
                       "não a do projeto")
    client = get_openai_client()
    vector_store_id = get_sensorial_vector_store_id()
    if not client:
        return False, "OpenAI não configurado."
    if not vector_store_id:
        return False, "a base de conhecimento do Teste Sensorial não está configurada nesta organização"
    try:
        add_document_to_vector_store(vector_store_id, filename, content,
                                     project_document("teste_sensorial", project_id, tipo="briefing"), wait=False)
        return True, filename
    except Exception as error:
        return False, str(error)


# ------------------------------------------------------------------
# Modo edição vs criação
# ------------------------------------------------------------------
project_id = st.session_state.get("ts_project_id")
project = sensorial_db.get_project(project_id) if project_id else None
if project_id and project is None:
    st.session_state.pop("ts_project_id", None)
    project_id = None
editing = project is not None
project = project or {}

if editing:
    # Ao editar, a autoria entra na conta; ao criar, basta o papel.
    pode_editar = sensorial_db.user_can_modify_project(project, user)

ui.inject_theme()
ui.breadcrumb("Teste Sensorial", project.get("name", "") if editing else "Projetos",
              "Dados do Projeto" if editing else "Novo")
page_title("note-pencil" if editing else "plus", "Dados do Projeto" if editing else "Novo Projeto",
           project.get("name") if editing else "Contexto e objetivo do estudo.")

if not pode_editar:
    if editing and auth.can_write(user):
        st.info("Este projeto foi criado por outro administrador da organização.")
    else:
        st.info("Sua conta tem acesso somente de leitura ao Teste Sensorial.")


def _render_context() -> None:
    nome = st.text_input("Nome do Projeto *", value=project.get("name", ""),
                         placeholder="Ex: Teste de fragrâncias — três amostras e controle", disabled=not pode_editar)
    categoria = st.text_input("Categoria", value=project.get("categoria") or "", placeholder="Ex: Perfumaria",
                              disabled=not pode_editar)
    col_a, col_b = st.columns(2)
    with col_a:
        objetivo = st.text_area(
            "Contexto / objetivo do estudo", value=project.get("objetivo") or "", height=140,
            placeholder="Cliente, amostras, o que cada uma pretende comunicar e o que o estudo precisa decidir.",
            disabled=not pode_editar)
        historico = st.text_area("Histórico / informações adicionais", value=project.get("historico") or "",
                                 height=120, disabled=not pode_editar)
    with col_b:
        questions = st.text_area(
            "Perguntas centrais (uma por linha)", value=project.get("questions") or "", height=270,
            placeholder="Qual amostra gera mais aproximação que o controle?\n"
                        "Os claims da marca aparecem no cérebro e no teste explícito?",
            disabled=not pode_editar)

    st.divider()
    st.subheader("Briefing do projeto")
    st.markdown("Documento de contexto para a análise de IA. O texto fica no projeto e o arquivo vai para a base "
                "de conhecimento do Teste Sensorial, visível só para este projeto.")
    current_filename = project.get("briefing_filename") or ""
    current_text = project.get("briefing_text") or ""
    briefing_file = st.file_uploader("Documento de briefing", type=list(BRIEFING_EXTENSIONS),
                                     disabled=not pode_editar)
    remove_briefing = st.checkbox("Remover briefing atual", value=False,
                                  disabled=not (pode_editar and current_text))
    if current_filename:
        st.caption("Briefing atual: {}".format(current_filename))
    if current_text:
        with st.expander("Prévia do briefing atual"):
            st.text(current_text[:1500] + ("\n...[prévia truncada]" if len(current_text) > 1500 else ""))

    if not st.button("Salvar alterações" if editing else "Criar projeto", type="primary", width="stretch",
                     disabled=not pode_editar):
        return
    if not nome.strip():
        st.error("O **Nome do Projeto** é obrigatório.")
        return
    briefing_filename, briefing_text = current_filename, current_text
    uploaded_name, uploaded_bytes = "", b""
    if remove_briefing:
        briefing_filename, briefing_text = "", ""
    if briefing_file:
        extracted, error = extract_briefing_text(briefing_file.name, briefing_file.getvalue())
        if error:
            st.error(error)
            return
        briefing_filename, briefing_text = briefing_file.name, cap_text(extracted)
        uploaded_name, uploaded_bytes = briefing_file.name, briefing_file.getvalue()
    fields = {"categoria": categoria, "objetivo": objetivo, "historico": historico, "questions": questions,
              "briefing_filename": briefing_filename, "briefing_text": briefing_text}
    try:
        if editing:
            sensorial_db.update_project(project["id"], name=nome, **fields)
            saved_id = project["id"]
        else:
            saved_id = sensorial_db.create_project(nome, **fields)
    except (auth.AuthorizationError, ValueError) as error:
        st.error(str(error))
        return
    if uploaded_name:
        kb_name = "briefing_ts_{}_{}_{}".format(_slugify(nome), datetime.now().strftime("%Y%m%d_%H%M%S"),
                                                uploaded_name)
        ok, message = _upload_briefing_to_kb(kb_name, uploaded_bytes, saved_id)
        if ok:
            st.toast("Briefing enviado para a base de conhecimento.")
        else:
            st.warning("Projeto salvo, mas o briefing não foi para a base: {}.".format(message))
    st.session_state["ts_project_id"] = saved_id
    if editing:
        st.toast("Projeto atualizado.")
        st.rerun()
    # Num projeto recém-criado o menu desta execução foi montado sem projeto.
    st.session_state["_navigate_to"] = "modules/teste_sensorial/preparacao.py"
    st.rerun()


def _save_settings(section: str, value) -> None:
    settings = sensorial_db.project_settings(project)
    if value in (None, {}, []):
        settings.pop(section, None)
    else:
        settings[section] = value
    try:
        sensorial_db.update_project(project["id"], settings_json=settings)
    except (auth.AuthorizationError, ValueError) as error:
        st.error(str(error))
    else:
        st.toast("Configuração salva; a análise foi recalculada.")
        st.rerun()


def _render_design(model: dict) -> None:
    design = model["design"]
    sessions = model["sessions"]
    if sessions.empty:
        st.info("O desenho aparece depois que as saídas do pipeline forem gravadas em Uploads.")
        return
    custom = bool(sensorial_db.project_settings(project).get("desenho"))
    st.markdown("Cada sessão vira uma **condição** pela amostra e pelo experimento. O basal é a referência de "
                "cada participante; o controle (ex.: neutro) é a comparação de cada amostra. "
                + ("Este projeto usa um desenho salvo." if custom else "Este desenho foi deduzido dos dados."))

    st.markdown("**Condições**")
    conditions = pd.DataFrame(design.get("condicoes") or [], columns=["codigo", "rotulo", "papel"])
    counts = sessions[sessions["condicao"] != ""].groupby("condicao")["sessao_id"].nunique()
    conditions["sessoes"] = conditions["codigo"].map(counts).fillna(0).astype(int)
    edited_conditions = st.data_editor(
        conditions, hide_index=True, width="stretch", num_rows="dynamic" if pode_editar else "fixed",
        disabled=True if not pode_editar else ["sessoes"],
        column_config={
            "codigo": st.column_config.TextColumn("Código"),
            "rotulo": st.column_config.TextColumn("Nome nos gráficos"),
            "papel": st.column_config.SelectboxColumn("Papel", options=list(ROLE_LABELS)[:3]),
            "sessoes": st.column_config.NumberColumn("Sessões"),
        },
        key="ts_design_conditions_{}".format(project["id"]))

    st.markdown("**De onde vem cada condição** (experimento e amostra das sessões)")
    seen = sessions.assign(amostra=sessions["amostra"].fillna(""), experimento=sessions["experimento"].fillna(""))
    mapping = seen.groupby(["experimento", "amostra"]).agg(sessoes=("sessao_id", "nunique")).reset_index()
    mapping["condicao"] = [
        sensorial_design.condition_for(e, a, design) or OUT for e, a in zip(mapping["experimento"], mapping["amostra"])]
    codes = [c for c in edited_conditions["codigo"].dropna().astype(str) if c.strip()]
    edited_mapping = st.data_editor(
        mapping, hide_index=True, width="stretch", disabled=True if not pode_editar else ["experimento", "amostra",
                                                                                          "sessoes"],
        column_config={
            "experimento": st.column_config.TextColumn("Experimento"),
            "amostra": st.column_config.TextColumn("Amostra"),
            "sessoes": st.column_config.NumberColumn("Sessões"),
            "condicao": st.column_config.SelectboxColumn("Condição", options=codes + [OUT]),
        },
        key="ts_design_mapping_{}".format(project["id"]))

    st.markdown("**Etapas**")
    stages_seen = sorted(set(model["eeg"]["Etapa"].dropna().astype(str))) if not model["eeg"].empty else []
    chosen = {s["codigo"]: s for s in design.get("etapas") or []}
    stages = pd.DataFrame([
        {"codigo": code, "rotulo": (chosen.get(code) or {}).get("rotulo") or sensorial_design.stage_label(code),
         "papel": (chosen.get(code) or {}).get("papel") or "ignorar"}
        for code in list(chosen) + [s for s in stages_seen if s not in chosen]])
    edited_stages = st.data_editor(
        stages, hide_index=True, width="stretch", disabled=True if not pode_editar else ["codigo"],
        column_config={
            "codigo": st.column_config.TextColumn("Etapa nos dados"),
            "rotulo": st.column_config.TextColumn("Nome nos gráficos"),
            "papel": st.column_config.SelectboxColumn("Papel", options=list(STAGE_ROLE_LABELS)),
        },
        key="ts_design_stages_{}".format(project["id"]))

    reference = design.get("referencia") or {}
    analysis = [row["codigo"] for _, row in edited_stages.iterrows() if row["papel"] != "ignorar"]
    c1, c2, c3 = st.columns(3)
    basal_codes = [""] + codes
    ref_condition = c1.selectbox("Condição de referência (basal)", basal_codes,
                                 index=basal_codes.index(reference.get("condicao")) if reference.get(
                                     "condicao") in basal_codes else 0, disabled=not pode_editar)
    stage_options = [""] + analysis
    ref_stage = c2.selectbox("Etapa de referência", stage_options,
                             index=stage_options.index(reference.get("etapa")) if reference.get(
                                 "etapa") in stage_options else 0, disabled=not pode_editar)
    control = c3.selectbox("Controle", basal_codes,
                           index=basal_codes.index(design.get("controle")) if design.get(
                               "controle") in basal_codes else 0, disabled=not pode_editar)
    if not pode_editar:
        return
    save, reset = st.columns(2)
    if save.button("Salvar desenho", type="primary", key="ts_save_design"):
        rows = [{"codigo": str(r["codigo"]).strip(), "rotulo": str(r["rotulo"] or r["codigo"]).strip(),
                 "papel": r["papel"] or "amostra"} for _, r in edited_conditions.iterrows()
                if str(r["codigo"] or "").strip()]
        _save_settings("desenho", {
            "condicoes": rows,
            "mapa": {sensorial_design.condition_key(r["experimento"], r["amostra"]):
                     "" if r["condicao"] == OUT else r["condicao"] for _, r in edited_mapping.iterrows()},
            "etapas": [{"codigo": r["codigo"], "rotulo": r["rotulo"], "papel": r["papel"]}
                       for _, r in edited_stages.iterrows() if r["papel"] != "ignorar"],
            "referencia": {"condicao": ref_condition, "etapa": ref_stage},
            "controle": control,
        })
    if custom and reset.button("Voltar ao desenho deduzido dos dados", key="ts_reset_design"):
        _save_settings("desenho", {})


def _render_indices(model: dict) -> None:
    settings = model["settings"]
    names = settings["indices"].get("nomes") or {}
    st.markdown("Os índices do relatório vêm do PSD por janela, pelas fórmulas da sintaxe do SPSS. O nome de "
                "negócio é o que aparece nos gráficos, nos achados e nas exportações.")
    catalog = pd.DataFrame([
        {"codigo": item["codigo"], "nome": names.get(item["codigo"]) or item["nome"], "descricao": item["descricao"]}
        for item in sensorial_design.INDEX_CATALOG])
    edited = st.data_editor(
        catalog, hide_index=True, width="stretch", disabled=True if not pode_editar else ["codigo", "descricao"],
        column_config={"codigo": st.column_config.TextColumn("Índice"),
                       "nome": st.column_config.TextColumn("Nome de negócio"),
                       "descricao": st.column_config.TextColumn("O que mede", width="large")},
        key="ts_index_names_{}".format(project["id"]))
    st.divider()
    ppi = settings["indices"]["ppi"]
    st.markdown("**PPI** — índice preditivo geral")
    mode = st.radio(
        "Componentes", ["z", "spss"], index=0 if ppi.get("modo", "z") == "z" else 1, horizontal=True,
        format_func=lambda m: "Padronizados (z) no conjunto analisado" if m == "z" else "Como na sintaxe do SPSS",
        help="Na fórmula do SPSS a memória vem em potência absoluta (perto de 1e-12) e quase não pesa.",
        disabled=not pode_editar)
    weights = ppi.get("pesos") or {}
    columns = st.columns(3)
    new_weights = {
        code: columns[i].number_input(code, value=float(weights.get(code, 0.0)), step=0.05,
                                      key="ts_ppi_{}".format(code), disabled=not pode_editar)
        for i, code in enumerate(("FAI", "ATTENTION_IDX_MID", "MEMORY_IDX"))}
    if pode_editar and st.button("Salvar índices", type="primary", key="ts_save_indices"):
        defaults = {item["codigo"]: item["nome"] for item in sensorial_design.INDEX_CATALOG}
        chosen = {row["codigo"]: str(row["nome"]).strip() for _, row in edited.iterrows()
                  if str(row["nome"] or "").strip() and str(row["nome"]).strip() != defaults[row["codigo"]]}
        _save_settings("indices", {"nomes": chosen, "ppi": {"modo": mode, "pesos": new_weights}})


def _render_cleaning(model: dict) -> None:
    cleaning = model["settings"]["limpeza"]
    roles = {item["role"] for item in sensorial_db.list_files(project["id"]) if item["is_active"]}
    sent = [label for role, label in (("base_limpa_eeg", "EEG"), ("base_limpa_perifericos", "periféricos"),
                                      ("base_limpa_associacao", "teste de associação")) if role in roles]
    st.markdown("**BASE LIMPA do SPSS.** Quando enviada, a curadoria do analista decide as janelas e tentativas "
                "de cada camada. " + ("Enviada para: {}.".format(", ".join(sent)) if sent else "Nenhuma enviada."))
    use_base = st.checkbox("Usar a BASE LIMPA onde ela existir", value=bool(cleaning.get("usar_base_limpa", True)),
                           disabled=not pode_editar)
    st.divider()
    st.markdown("**Regra de outliers da sintaxe do SPSS** — ln de cada canal × banda, z no conjunto e "
                "Mahalanobis. Vale só para as camadas sem BASE LIMPA.")
    rule = st.checkbox("Aplicar a regra às janelas de EEG", value=bool(cleaning.get("regra_spss")),
                       disabled=not pode_editar)
    c1, c2, c3 = st.columns(3)
    z = c1.number_input("|z| acima de", value=float(cleaning.get("z", 3.29)), step=0.01, disabled=not pode_editar)
    p_value = c2.number_input("Mahalanobis com p abaixo de", value=float(cleaning.get("p_mahalanobis", 0.001)),
                              step=0.001, format="%.3f", disabled=not pode_editar)
    marks = c3.number_input("Sai com marcas a partir de", value=int(cleaning.get("min_marcas", 5)), step=1,
                            min_value=1, disabled=not pode_editar)
    st.divider()
    quality = st.checkbox(
        "Sem BASE LIMPA dos periféricos, deixar de fora janela com fluxo corrompido ou sem pulso",
        value=bool(model["settings"]["perifericos"].get("exigir_qualidade", True)), disabled=not pode_editar)
    if pode_editar and st.button("Salvar limpeza", type="primary", key="ts_save_cleaning"):
        settings = sensorial_db.project_settings(project)
        settings["limpeza"] = {"usar_base_limpa": use_base, "regra_spss": rule, "z": float(z),
                               "p_mahalanobis": float(p_value), "min_marcas": int(marks)}
        settings["perifericos"] = {"exigir_qualidade": quality}
        try:
            sensorial_db.update_project(project["id"], settings_json=settings)
        except (auth.AuthorizationError, ValueError) as error:
            st.error(str(error))
        else:
            st.toast("Limpeza salva; a análise foi recalculada.")
            st.rerun()


def _render_claims(model: dict) -> None:
    settings = model["settings"]
    known = set(model["indices"]) | set(model["pipeline_indicators"]) | set(sensorial_design.PERIPHERAL_CODES)
    st.markdown("Para cada claim do teste de associação, os índices que deveriam mostrar o efeito no **cérebro** "
                "e no **corpo**. Um `-` antes do código diz que o esperado é o índice cair (ex.: `-AROUSAL_FRONT` "
                "para um claim de relaxamento).")
    st.caption("Códigos: " + ", ".join(sorted(known)))
    claims = pd.DataFrame([{"palavra": c["palavra"], "indicadores": ", ".join(c.get("indicadores") or []),
                            "corpo": ", ".join(c.get("corpo") or [])} for c in model["claims"]],
                          columns=["palavra", "indicadores", "corpo"])
    edited = st.data_editor(
        claims, hide_index=True, width="stretch", num_rows="dynamic" if pode_editar else "fixed",
        disabled=not pode_editar,
        column_config={"palavra": st.column_config.TextColumn("Claim"),
                       "indicadores": st.column_config.TextColumn("Cérebro (EEG)"),
                       "corpo": st.column_config.TextColumn("Corpo (periféricos)")},
        key="ts_claims_{}".format(project["id"]))
    st.divider()
    association = settings["associacao"]
    st.markdown("**Teste de associação** — Score = % de “Sim” × CR médio das respostas “Sim”.")
    groups_text = st.text_area(
        "Somar condições (uma por linha, `rótulo = condições`)",
        value="\n".join("{} = {}".format(k, ", ".join(v)) for k, v in (association.get("agrupar") or {}).items()),
        placeholder="B = B1, B2", height=80, disabled=not pode_editar)
    bands = association["faixas"]
    cuts = association["quadrantes"]
    c1, c2, c3, c4, c5 = st.columns(5)
    very_high = c1.number_input("Muito alta a partir de", value=float(bands["muito_alta"]), step=0.05,
                                disabled=not pode_editar)
    high = c2.number_input("Alta acima de", value=float(bands["alta"]), step=0.05, disabled=not pode_editar)
    low = c3.number_input("Baixa acima de", value=float(bands["baixa"]), step=0.05, disabled=not pode_editar)
    pct = c4.number_input("Quadrante: % de “Sim” a partir de", value=float(cuts["pct_sim"]), step=0.05,
                          min_value=0.0, max_value=1.0, disabled=not pode_editar)
    cr = c5.number_input("Quadrante: CR a partir de", value=float(cuts["cr"]), step=0.05, disabled=not pode_editar)
    if not (pode_editar and st.button("Salvar claims", type="primary", key="ts_save_claims")):
        return
    rows, unknown = [], set()
    for _, row in edited.iterrows():
        word = str(row["palavra"] or "").strip()
        if not word:
            continue
        brain, body = _codes(row["indicadores"]), _codes(row["corpo"])
        unknown |= {code.lstrip("+-") for code in brain + body} - known
        if brain or body:
            rows.append({"palavra": word, "indicadores": brain, "corpo": body})
    if unknown:
        st.error("Códigos desconhecidos: {}.".format(", ".join(sorted(unknown))))
        return
    groups = {}
    for line in str(groups_text or "").splitlines():
        if "=" in line:
            label, members = line.split("=", 1)
            if label.strip() and _codes(members):
                groups[label.strip()] = _codes(members)
    settings_now = sensorial_db.project_settings(project)
    settings_now["claims"] = rows
    settings_now["associacao"] = {"agrupar": groups,
                                  "faixas": {"muito_alta": float(very_high), "alta": float(high), "baixa": float(low)},
                                  "quadrantes": {"pct_sim": float(pct), "cr": float(cr)}}
    try:
        sensorial_db.update_project(project["id"], settings_json=settings_now)
    except (auth.AuthorizationError, ValueError) as error:
        st.error(str(error))
    else:
        st.toast("Claims salvos; a análise foi recalculada.")
        st.rerun()


def _render_statistics(model: dict) -> None:
    stats = model["settings"]["estatistica"]
    st.markdown("As comparações são pareadas por participante (Wilcoxon), com o p de cada família (índice × "
                "etapa) corrigido por Holm. Só o que passa na correção vira “diferença”; o resto com p bruto "
                "abaixo de alfa é “tendência”.")
    c1, c2 = st.columns(2)
    pairs = c1.number_input("Mínimo de participantes pareados para testar", value=int(stats.get("min_pares", 5)),
                            min_value=3, step=1, disabled=not pode_editar)
    alpha = c2.number_input("Alfa", value=float(stats.get("alfa", 0.05)), min_value=0.001, max_value=0.2,
                            step=0.01, format="%.3f", disabled=not pode_editar)
    if pode_editar and st.button("Salvar estatística", type="primary", key="ts_save_stats"):
        _save_settings("estatistica", {"min_pares": int(pairs), "alfa": float(alpha)})


if not editing:
    _render_context()
else:
    from utils.sensorial_cache import get_project_model

    tabs = st.tabs(["Contexto", "Desenho", "Índices", "Limpeza", "Claims", "Estatística"])
    with tabs[0]:
        _render_context()
    project_model = get_project_model(project)
    for tab, render in zip(tabs[1:], (_render_design, _render_indices, _render_cleaning, _render_claims,
                                      _render_statistics)):
        with tab:
            render(project_model)
