"""
Cache do modelo e das métricas de um projeto da Jornada de Compra.

O Streamlit reexecuta a página a cada clique; reler e reparsear os arquivos
do projeto em cada execução é o que deixa a Análise Geral do NencBoost lenta.
Aqui o modelo fica em `st.cache_data` com a chave (projeto, organização,
`data_version`): toda escrita que muda os dados sobe a versão na mesma
transação, então a chave muda sozinha e nunca é preciso limpar o cache de
todo mundo.

A autorização acontece antes: quem chama já obteve o projeto por
`jornada_db.get_project` (que respeita a organização ativa) e passa o dict
dele. A organização que entra na chave é a DO PROJETO.
"""

import json
from typing import Any, Dict, Optional

import streamlit as st

from utils import jornada_db
from utils.jornada_model import build_model

_TTL_SECONDS = 6 * 60 * 60


@st.cache_data(max_entries=8, ttl=_TTL_SECONDS, show_spinner=False)
def _model(project_id: int, organization_id: int, data_version: int) -> Dict[str, Any]:
    return build_model(jornada_db.load_project_bundle(project_id, organization_id))


@st.cache_data(max_entries=64, ttl=_TTL_SECONDS, show_spinner=False)
def _metrics(
    project_id: int, organization_id: int, data_version: int, filters_json: str
) -> Dict[str, Any]:
    from utils.jornada_metrics import compute_all

    model = _model(project_id, organization_id, data_version)
    return compute_all(model, json.loads(filters_json))


def _key(project: Dict[str, Any]):
    return int(project["id"]), int(project["organization_id"]), int(project.get("data_version") or 0)


def get_project_model(project: Dict[str, Any]) -> Dict[str, Any]:
    """Modelo do projeto (tabelas de gravações, olhar, agregados, catálogo...)."""

    return _model(*_key(project))


def get_project_metrics(project: Dict[str, Any], filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Métricas do projeto para um conjunto de filtros (dict serializável)."""

    filters_json = json.dumps(filters or {}, sort_keys=True, ensure_ascii=False, default=str)
    return _metrics(*_key(project), filters_json)
