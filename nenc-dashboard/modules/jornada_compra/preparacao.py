"""
Jornada de Compra — Novo projeto / Dados do Projeto.

Sem projeto aberto, é o formulário de criação; com projeto aberto, reúne o que
define a análise, em abas:

- Contexto: objetivo, perguntas, marcas e briefing (que também vai para a base
  de conhecimento, marcado como material deste projeto);
- Lojas e perfis: nome e canal de cada loja, perfil e tamanho de cada grupo
  dos agregados;
- Catálogo de AOIs: atributos (ex.: tipo = Diurno, Noturno) e correções da
  leitura automática de cada AOI;
- Parâmetros: limiar de "examinou", Hz nominal e limiares de qualidade por
  tipo de tarefa — o papel que o tipo de projeto tem no NencBoost.
"""

import json
from datetime import datetime

import pandas as pd

import streamlit as st
from utils import auth, ui
from utils.icons import page_title

user = auth.require_module("jornada_compra")
pode_editar = auth.can_write(user)

from utils import jornada_db
from utils.ai_provider import (
    add_document_to_vector_store,
    get_openai_client,
    get_vector_store_id,
)
from utils.briefing import BRIEFING_EXTENSIONS, cap_text, extract_briefing_text
from utils.jornada_ingest import TASK_LABELS
from utils.jornada_quality import DEFAULT_THRESHOLDS, default_thresholds
from utils.jornada_taxonomy import KIND_LABELS, format_dimensions, parse_dimensions
from utils.kb_attributes import project_document

jornada_db.init_db()


def _slugify(text: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(text or ""))
    return safe.strip("_")[:80] or "projeto"


def _lines(text: str) -> list:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _upload_briefing_to_kb(filename: str, content: bytes, project_id: int) -> tuple:
    """Manda o briefing para a base da Jornada, marcado como material do projeto."""
    if auth.active_organization_id(user) == 0:
        return False, (
            "em \"Todas as organizações\" a base resolvida seria a da sua "
            "organização, não a do projeto"
        )
    client = get_openai_client()
    vector_store_id = get_vector_store_id()
    if not client:
        return False, "OpenAI não configurado."
    if not vector_store_id:
        return False, "a base de conhecimento da Jornada não está configurada nesta organização"
    try:
        add_document_to_vector_store(
            vector_store_id,
            filename,
            content,
            project_document("jornada_compra", project_id, tipo="briefing"),
            wait=False,
        )
        return True, filename
    except Exception as error:
        return False, str(error)


# ------------------------------------------------------------------
# Modo edição vs criação
# ------------------------------------------------------------------
project_id = st.session_state.get("jc_project_id")
project = jornada_db.get_project(project_id) if project_id else None
if project_id and project is None:
    st.session_state.pop("jc_project_id", None)
    project_id = None
editing = project is not None
project = project or {}

if editing:
    # Ao editar, a autoria entra na conta; ao criar, basta o papel.
    pode_editar = jornada_db.user_can_modify_project(project, user)

ui.inject_theme()
ui.breadcrumb(
    "Jornada de Compra",
    project.get("name", "") if editing else "Projetos",
    "Dados do Projeto" if editing else "Novo",
)
page_title(
    "note-pencil" if editing else "plus",
    "Dados do Projeto" if editing else "Novo Projeto",
    project.get("name") if editing else "Contexto, perguntas e marcas do estudo.",
)

if not pode_editar:
    if editing and auth.can_write(user):
        st.info("Este projeto foi criado por outro administrador da organização.")
    else:
        st.info("Sua conta tem acesso somente de leitura à Jornada de Compra.")


def _render_context() -> None:
    """Contexto do estudo: vira briefing da IA e define as marcas analisadas."""

    nome = st.text_input(
        "Nome do Projeto *",
        value=project.get("name", ""),
        placeholder="Ex: Jornada de compra da categoria — lojas farma e atacarejo",
        disabled=not pode_editar,
    )
    categoria = st.text_input(
        "Categoria",
        value=project.get("categoria") or "",
        placeholder="Ex: Absorventes",
        disabled=not pode_editar,
    )

    col_a, col_b = st.columns(2)
    with col_a:
        especialidade = st.text_area(
            "Contexto / objetivo do estudo",
            value=project.get("especialidade") or "",
            placeholder=(
                "Cliente, categoria, lojas, tarefas (jornada livre, estimulada, "
                "embalagens) e o que a marca quer decidir com o estudo."
            ),
            height=140,
            disabled=not pode_editar,
        )
        historico = st.text_area(
            "Histórico / informações adicionais",
            value=project.get("historico") or "",
            placeholder="Mudanças de embalagem, planograma, promoções no período da coleta...",
            height=140,
            disabled=not pode_editar,
        )
    with col_b:
        problemas = st.text_area(
            "Perguntas centrais (uma por linha)",
            value=project.get("problemas") or "",
            placeholder=(
                "A marca foco é vista e examinada frente às concorrentes?\n"
                "Quais elementos da embalagem são vistos, por perfil?\n"
                "Há diferença entre canais e perfis de shopper?\n"
                "Como o shopper navega e decide (linhas, preço)?"
            ),
            height=140,
            disabled=not pode_editar,
        )
        marcas = st.text_area(
            "Marcas do estudo (uma por linha)",
            value=project.get("marcas") or "",
            placeholder="Marca A\nMarca B\nMarca C",
            height=140,
            help=(
                "Usadas para reconhecer a marca de cada AOI pelo nome. Marcas que não "
                "estiverem aqui ainda são sugeridas a partir dos dados."
            ),
            disabled=not pode_editar,
        )

    marca_options = [""] + _lines(marcas)
    saved_focus = project.get("marca_foco") or ""
    if saved_focus and saved_focus not in marca_options:
        marca_options.append(saved_focus)
    marca_foco = st.selectbox(
        "Marca foco (cliente)",
        marca_options,
        index=marca_options.index(saved_focus) if saved_focus in marca_options else 0,
        format_func=lambda value: value or "— nenhuma —",
        help="Destacada nos gráficos, nos achados e no relatório.",
        disabled=not pode_editar,
    )

    st.divider()
    st.subheader("Briefing do projeto")
    st.markdown(
        "Documento de contexto para a análise de IA. O texto fica no projeto e o "
        "arquivo vai para a base de conhecimento da Jornada, visível só para este projeto."
    )
    current_filename = project.get("briefing_filename") or ""
    current_text = project.get("briefing_text") or ""
    briefing_file = st.file_uploader(
        "Documento de briefing",
        type=list(BRIEFING_EXTENSIONS),
        disabled=not pode_editar,
    )
    remove_briefing = st.checkbox(
        "Remover briefing atual", value=False, disabled=not (pode_editar and current_text)
    )
    if current_filename:
        st.caption("Briefing atual: {}".format(current_filename))
    if current_text:
        with st.expander("Prévia do briefing atual"):
            st.text(current_text[:1500] + ("\n...[prévia truncada]" if len(current_text) > 1500 else ""))

    label = "Salvar alterações" if editing else "Criar projeto"
    if not st.button(label, type="primary", width="stretch", disabled=not pode_editar):
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
        briefing_filename = briefing_file.name
        briefing_text = cap_text(extracted)
        uploaded_name, uploaded_bytes = briefing_file.name, briefing_file.getvalue()

    fields = {
        "categoria": categoria,
        "especialidade": especialidade,
        "historico": historico,
        "problemas": problemas,
        "marcas": "\n".join(_lines(marcas)),
        "marca_foco": marca_foco,
        "briefing_filename": briefing_filename,
        "briefing_text": briefing_text,
    }
    try:
        if editing:
            jornada_db.update_project(project["id"], name=nome, **fields)
            saved_id = project["id"]
        else:
            saved_id = jornada_db.create_project(nome, **fields)
    except (auth.AuthorizationError, ValueError) as error:
        st.error(str(error))
        return

    if uploaded_name:
        kb_name = "briefing_jc_{}_{}_{}".format(
            _slugify(nome), datetime.now().strftime("%Y%m%d_%H%M%S"), uploaded_name
        )
        ok, message = _upload_briefing_to_kb(kb_name, uploaded_bytes, saved_id)
        if ok:
            st.toast("Briefing enviado para a base de conhecimento.")
        else:
            st.warning("Projeto salvo, mas o briefing não foi para a base: {}.".format(message))

    st.session_state["jc_project_id"] = saved_id
    if editing:
        st.toast("Projeto atualizado.")
        st.rerun()
    # Num projeto recém-criado o menu desta execução foi montado sem projeto:
    # as páginas dele só existem no rerun seguinte.
    st.session_state["_navigate_to"] = (
        "modules/jornada_compra/uploads.py"
        if auth.can_write(user)
        else "modules/jornada_compra/analise_geral.py"
    )
    st.rerun()


def _save_settings(changes: dict, **fields) -> None:
    settings = jornada_db.project_settings(project)
    settings.update(changes)
    try:
        jornada_db.update_project(project["id"], settings_json=settings, **fields)
    except (auth.AuthorizationError, ValueError) as error:
        st.error(str(error))
    else:
        st.toast("Configuração salva; a análise foi recalculada.")
        st.rerun()


def _render_stores(model: dict) -> None:
    settings = jornada_db.project_settings(project)
    st.markdown(
        "Nome e canal de cada loja aparecem nos gráficos e no relatório. O canal agrupa as "
        "lojas nas comparações (ex.: FARMA × C&C)."
    )
    stores = model["stores"]
    if stores.empty:
        st.info("As lojas aparecem aqui depois dos uploads.")
    else:
        edited = st.data_editor(
            stores[["store", "label", "channel", "tasks"]],
            hide_index=True,
            width="stretch",
            disabled=True if not pode_editar else ["store", "tasks"],
            column_config={
                "store": st.column_config.TextColumn("Chave"),
                "label": st.column_config.TextColumn("Nome"),
                "channel": st.column_config.TextColumn("Canal"),
                "tasks": st.column_config.TextColumn("Tarefas"),
            },
            key="jc_stores_editor_{}".format(project["id"]),
        )
        if pode_editar and st.button("Salvar lojas", key="jc_save_stores"):
            configured = {
                row["store"]: {
                    "label": str(row["label"] or "").strip(),
                    "channel": str(row["channel"] or "").strip(),
                }
                for _, row in edited.iterrows()
            }
            _save_settings({"stores": configured})

    st.divider()
    st.markdown(
        "**Grupos dos agregados.** Exports agregados (`PERFIL 1`, `TODOS`...) não dizem quem "
        "está no grupo. Informe o perfil de cada um; o tamanho é contado pelos participantes "
        "desse perfil, ou pode ser fixado."
    )
    pooled = model["pooled"]
    groups = (
        sorted(group for group in pooled["group"].unique() if group and group != "TODOS")
        if not pooled.empty
        else []
    )
    if not groups:
        st.caption("Nenhum agregado por grupo neste projeto.")
        return
    configured = settings.get("groups") or {}
    profiles = sorted(profile for profile in model["participants"]["profile"].unique() if profile)
    table = pd.DataFrame(
        [
            {
                "group": group,
                "profile": (configured.get(group) or {}).get("profile") or "",
                "size": (configured.get(group) or {}).get("size"),
                "counted": int(pooled[pooled["group"] == group]["n_group"].max()),
            }
            for group in groups
        ]
    )
    edited_groups = st.data_editor(
        table,
        hide_index=True,
        width="stretch",
        disabled=True if not pode_editar else ["group", "counted"],
        column_config={
            "group": st.column_config.TextColumn("Grupo"),
            "profile": st.column_config.SelectboxColumn("Perfil", options=[""] + profiles),
            "size": st.column_config.NumberColumn("Tamanho fixo", min_value=0, step=1),
            "counted": st.column_config.NumberColumn("Tamanho usado hoje"),
        },
        key="jc_groups_editor_{}".format(project["id"]),
    )
    if pode_editar and st.button("Salvar grupos", key="jc_save_groups"):
        groups_config = {}
        for _, row in edited_groups.iterrows():
            entry = {}
            if row["profile"]:
                entry["profile"] = row["profile"]
            if row["size"] == row["size"] and row["size"]:
                entry["size"] = int(row["size"])
            if entry:
                groups_config[row["group"]] = entry
        _save_settings({"groups": groups_config})


def _render_catalog(model: dict) -> None:
    settings = jornada_db.project_settings(project)
    st.markdown(
        "Cada AOI é lida pelo nome: marca, linha, atributos, parte, preço ou elemento de "
        "embalagem. Os **atributos** são as dimensões que o nome carrega — uma por linha, no "
        "formato `nome: valor1, valor2`."
    )
    dimensions_text = st.text_area(
        "Atributos das AOIs",
        value=format_dimensions(settings.get("dimensions") or {}),
        placeholder="tipo: Diurno, Noturno\ncobertura: Seco, Suave",
        height=90,
        disabled=not pode_editar,
    )
    if pode_editar and st.button("Salvar atributos", key="jc_save_dims"):
        _save_settings({"dimensions": parse_dimensions(dimensions_text)})

    catalog = model["catalog"]
    if catalog.empty:
        st.info("O catálogo aparece depois dos uploads.")
        return
    st.divider()
    filters = st.columns(3)
    store_labels = dict(zip(model["stores"]["store"], model["stores"]["label"]))

    def _store_name(key: str) -> str:
        if key == "Todas":
            return "Todas"
        return store_labels.get(key) or key or "Todas as lojas (agregado)"

    store_choice = filters[0].selectbox(
        "Loja", ["Todas"] + sorted(catalog["store"].unique()), format_func=_store_name,
        key="jc_cat_store",
    )
    kind_choice = filters[1].selectbox(
        "Tipo", ["Todos"] + list(KIND_LABELS), format_func=lambda key: KIND_LABELS.get(key, key),
        key="jc_cat_kind",
    )
    only_manual = filters[2].checkbox("Só ajustadas à mão", key="jc_cat_manual")
    view = catalog
    if store_choice != "Todas":
        view = view[view["store"] == store_choice]
    if kind_choice != "Todos":
        view = view[view["kind"] == kind_choice]
    if only_manual:
        view = view[view["source"] == "manual"]
    attr_columns = [column for column in catalog.columns if column.startswith("attr_")]
    columns = ["store", "aoi", "kind", "brand", "line", "product", "part", "element"] + attr_columns + [
        "shelf_weight", "include", "source",
    ]
    attr_config = {
        column: st.column_config.TextColumn(column.replace("attr_", "").capitalize())
        for column in attr_columns
    }
    edited = st.data_editor(
        view[columns],
        hide_index=True,
        width="stretch",
        disabled=True if not pode_editar else ["store", "aoi", "source"],
        column_config={
            "store": st.column_config.TextColumn("Loja"),
            "aoi": st.column_config.TextColumn("AOI"),
            "kind": st.column_config.SelectboxColumn("Tipo", options=list(KIND_LABELS)),
            "brand": st.column_config.TextColumn("Marca"),
            "line": st.column_config.TextColumn("Linha"),
            "product": st.column_config.TextColumn("Produto"),
            "part": st.column_config.TextColumn("Parte"),
            "element": st.column_config.TextColumn("Elemento"),
            "shelf_weight": st.column_config.NumberColumn(
                "Peso na gôndola", help="Facings ou área, para o índice de presença. Vazio = 1."
            ),
            "include": st.column_config.CheckboxColumn("Entra"),
            "source": st.column_config.TextColumn("Origem"),
            **attr_config,
        },
        key="jc_catalog_editor_{}_{}_{}".format(project["id"], store_choice, kind_choice),
    )
    st.caption("{} AOI(s) · origem “manual” = ajuste salvo por alguém.".format(len(view)))
    if not pode_editar:
        return
    save_col, reset_col = st.columns(2)
    with save_col:
        if st.button("Salvar ajustes do catálogo", key="jc_save_catalog", type="primary"):
            before = view[columns].set_index(["store", "aoi"])
            rows = []
            for _, row in edited.iterrows():
                old = before.loc[(row["store"], row["aoi"])]
                changed = any(
                    str(row[column]) != str(old[column])
                    for column in columns
                    if column not in ("store", "aoi", "source")
                )
                if not changed:
                    continue
                weight = row["shelf_weight"]
                rows.append({
                    "store": row["store"],
                    "aoi": row["aoi"],
                    "kind": row["kind"],
                    "brand": row["brand"],
                    "line": row["line"],
                    "product": row["product"],
                    "part": row["part"],
                    "element": row["element"],
                    "attrs": {column.replace("attr_", ""): row[column] for column in attr_columns if row[column]},
                    "shelf_weight": None if weight is None or weight != weight else weight,
                    "include": bool(row["include"]),
                })
            if not rows:
                st.info("Nada mudou.")
            else:
                try:
                    jornada_db.save_aoi_overrides(project["id"], rows)
                except (auth.AuthorizationError, ValueError) as error:
                    st.error(str(error))
                else:
                    st.toast("{} AOI(s) ajustada(s).".format(len(rows)))
                    st.rerun()
    with reset_col:
        manual = view[view["source"] == "manual"]
        if not manual.empty and st.button(
            "Voltar à leitura automática ({} ajuste(s) nesta visão)".format(len(manual)),
            key="jc_reset_catalog",
        ):
            jornada_db.delete_aoi_overrides(project["id"], list(zip(manual["store"], manual["aoi"])))
            st.rerun()


def _render_parameters() -> None:
    settings = jornada_db.project_settings(project)
    st.markdown("**Métricas**")
    c1, c2 = st.columns(2)
    examined = c1.number_input(
        "Limiar de “examinou” (s)", min_value=0.1, max_value=30.0, step=0.1,
        value=float(settings.get("examined_threshold_s") or 1.0),
        help="Tempo total numa marca a partir do qual ela conta como examinada no funil.",
        disabled=not pode_editar,
    )
    hz_nominal = c2.number_input(
        "Hz nominal do rastreador (0 = não usar)", min_value=0.0, max_value=500.0, step=1.0,
        value=float(settings.get("hz_nominal") or 0.0),
        help="Só converte exports em amostras de gravações sem o arquivo de quadros.",
        disabled=not pode_editar,
    )

    st.divider()
    st.markdown("**Limiares de qualidade**")
    st.caption(
        "Os padrões mudam por tipo de tarefa: a jornada livre inclui a caminhada até a "
        "categoria, então espera gravações mais longas."
    )
    saved = project.get("quality_thresholds")
    use_default = st.checkbox(
        "Usar valores padrão do sistema", value=not saved, disabled=not pode_editar
    )
    try:
        custom = json.loads(saved) if saved else {}
    except (TypeError, ValueError):
        custom = {}
    quality_json = None
    if not use_default:
        common = dict(DEFAULT_THRESHOLDS)
        common.update(custom.get("*") or {})
        q1, q2, q3 = st.columns(3)
        values = {
            "hz_warn": q1.number_input("Alerta de Hz abaixo de", value=float(common["hz_warn"]), step=1.0),
            "hz_fail": q1.number_input("Problema de Hz abaixo de", value=float(common["hz_fail"]), step=1.0),
            "loss_warn_pct": q2.number_input(
                "Alerta de perda acima de (%)", value=float(common["loss_warn_pct"]), step=1.0
            ),
            "loss_fail_pct": q2.number_input(
                "Problema de perda acima de (%)", value=float(common["loss_fail_pct"]), step=1.0
            ),
            "gap_warn_s": q3.number_input(
                "Alerta de falha maior que (s)", value=float(common["gap_warn_s"]), step=0.5
            ),
        }
        per_task = {}
        task_columns = st.columns(len(TASK_LABELS))
        for column, (task, label) in zip(task_columns, TASK_LABELS.items()):
            base = default_thresholds(task)
            base.update(custom.get(task) or {})
            per_task[task] = {
                "min_duration_s": column.number_input(
                    "Duração mínima — {} (s)".format(label),
                    value=float(base["min_duration_s"]),
                    step=5.0,
                    key="jc_min_dur_{}".format(task),
                )
            }
        quality_json = json.dumps(dict({"*": values}, **per_task))

    if pode_editar and st.button("Salvar parâmetros", type="primary", key="jc_save_params"):
        _save_settings(
            {"examined_threshold_s": float(examined), "hz_nominal": float(hz_nominal) or None},
            quality_thresholds=quality_json,
        )


if not editing:
    _render_context()
else:
    from utils.jornada_cache import get_project_model

    tab_context, tab_stores, tab_catalog, tab_params = st.tabs(
        ["Contexto", "Lojas e perfis", "Catálogo de AOIs", "Parâmetros"]
    )
    with tab_context:
        _render_context()
    project_model = get_project_model(project)
    with tab_stores:
        _render_stores(project_model)
    with tab_catalog:
        _render_catalog(project_model)
    with tab_params:
        _render_parameters()
