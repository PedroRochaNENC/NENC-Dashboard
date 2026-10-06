"""
Cache do modelo de um projeto do Teste Sensorial.

Ler o PSD de um estudo e calcular os índices de dezenas de milhares de janelas
leva segundos; o Streamlit reexecuta a página a cada clique. O modelo fica em
`st.cache_data` com a chave (projeto, organização, `data_version`): toda
escrita que muda os dados ou o desenho sobe a versão na mesma transação, então
a chave muda sozinha.

A autorização acontece antes: quem chama já obteve o projeto por
`sensorial_db.get_project` (que respeita a organização ativa) e passa o dict
dele. A organização que entra na chave é a DO PROJETO.
"""

import json
from typing import Any, Dict, Optional

import streamlit as st

from utils import sensorial_db
from utils.sensorial_model import build_model

_TTL_SECONDS = 6 * 60 * 60


@st.cache_data(max_entries=4, ttl=_TTL_SECONDS, show_spinner=False)
def _model(project_id: int, organization_id: int, data_version: int) -> Dict[str, Any]:
    return build_model(sensorial_db.load_project_bundle(project_id, organization_id))


@st.cache_data(max_entries=32, ttl=_TTL_SECONDS, show_spinner=False)
def _metrics(project_id: int, organization_id: int, data_version: int, filters_json: str) -> Dict[str, Any]:
    from utils.sensorial_metrics import compute_all

    return compute_all(_model(project_id, organization_id, data_version), json.loads(filters_json))


@st.cache_data(max_entries=64, ttl=_TTL_SECONDS, show_spinner=False)
def _curves(project_id: int, organization_id: int, data_version: int, measure: str, alignment: str,
            filters_json: str) -> Dict[str, Any]:
    from utils.sensorial_metrics import curves

    return curves(_model(project_id, organization_id, data_version), measure, alignment, json.loads(filters_json))


def _filters_json(filters: Optional[Dict[str, Any]]) -> str:
    return json.dumps(filters or {}, sort_keys=True, ensure_ascii=False, default=str)


def _key(project: Dict[str, Any]):
    return int(project["id"]), int(project["organization_id"]), int(project.get("data_version") or 0)


def get_project_model(project: Dict[str, Any]) -> Dict[str, Any]:
    """Modelo do projeto: sessões, janelas com os índices, periféricos, tentativas e avisos."""
    return _model(*_key(project))


def get_project_metrics(project: Dict[str, Any], filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Métricas do projeto para um recorte (dict serializável)."""
    return _metrics(*_key(project), _filters_json(filters))


def get_curves(project: Dict[str, Any], measure: str, alignment: str = "etapa",
               filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Curva média de uma medida por condição (ver `sensorial_metrics.curves`)."""
    return _curves(*_key(project), str(measure), str(alignment), _filters_json(filters))


@st.cache_data(max_entries=128, ttl=_TTL_SECONDS, show_spinner=False)
def _image(project_id: int, organization_id: int, data_version: int, file_id: int, max_px: int) -> bytes:
    from utils.jornada_gallery import downscale

    content = sensorial_db.get_file_content(project_id, file_id)
    return downscale(content, max_px) if content else b""


def get_image(project: Dict[str, Any], file_id: int, max_px: int = 1200) -> bytes:
    """Imagem do projeto (topomapa, estímulo) reduzida para a tela ou a exportação; vazio se faltar."""
    return _image(*_key(project), int(file_id), int(max_px))
