"""
NencBoost — índice combinado gravado por áudio.

A lista de Áudios mostra e filtra o índice combinado de cada áudio. Calcular
na hora pediria o Sincronizado de todos os áudios a cada render, então o
índice fica em `audios.indice_combinado` (e as duas metades em
`indice_texto` e `indice_voz`, que a faixa "Divergente" precisa).

A régua da voz é o projeto inteiro (`referencia_valencia`), a mesma da Análise
Geral, do Resumo e da página do áudio. Como ela muda quando entra áudio novo,
o projeto é recalculado inteiro a cada importação, reprocessamento ou
exclusão: `atualizar_indices_do_projeto` depois do lote, uma vez.
"""

from __future__ import annotations

import io
import logging
from typing import Dict, Mapping, Optional, Tuple

import pandas as pd

from utils.prosodia_loader import normalizar_sincronizado
from utils.prosodia_signals import indice_combinado_por_grupo, referencia_valencia

_LOGGER = logging.getLogger(__name__)

LIMIAR = 0.2
FAIXAS = ("Favorável", "Neutro", "Desfavorável", "Divergente")


def calcular_indices(sincronizados: Mapping[str, bytes]) -> Dict[str, Tuple[float, float, float]]:
    """De session_id → Sincronizado (CSV) para session_id → (índice, texto, voz).

    Sincronizado ilegível fica de fora e vai para o log; áudio sem sentimento
    do texto ou sem valência não ganha índice.
    """
    partes = []
    for session_id, blob in sincronizados.items():
        if not blob:
            continue
        try:
            bruto = pd.read_csv(io.BytesIO(blob))
        except Exception:
            _LOGGER.exception("Sincronizado ilegivel no audio %s; fica sem indice combinado.", session_id)
            continue
        if not bruto.empty:
            partes.append(normalizar_sincronizado(bruto, str(session_id)))
    if not partes:
        return {}
    sinc = pd.concat(partes, ignore_index=True)
    tabela = indice_combinado_por_grupo(sinc, referencia_valencia(sinc), "session_id")
    return {
        str(linha["grupo"]): (float(linha["indice"]), float(linha["texto"]), float(linha["voz"]))
        for _, linha in tabela.iterrows()
    }


def faixa(indice: Optional[float], texto: Optional[float], voz: Optional[float]) -> Optional[str]:
    """Faixa do áudio: Divergente quando texto e voz passam de 0,2 em sentidos opostos."""
    if indice is None or pd.isna(indice):
        return None
    if texto is not None and voz is not None and not pd.isna(texto) and not pd.isna(voz):
        if (texto >= LIMIAR and voz <= -LIMIAR) or (texto <= -LIMIAR and voz >= LIMIAR):
            return "Divergente"
    if indice >= LIMIAR:
        return "Favorável"
    if indice <= -LIMIAR:
        return "Desfavorável"
    return "Neutro"


def atualizar_indices_do_projeto(project_id: int) -> int:
    """Recalcula e grava o índice de todos os áudios do projeto. Devolve quantos têm índice."""
    from utils import prosodia_db

    indices = calcular_indices(prosodia_db.get_sincronizados_for_project(project_id))
    return prosodia_db.save_audio_indices(project_id, indices)


def atualizar_sem_derrubar(project_id: int) -> None:
    """Para o fim dos fluxos de importação: o índice desatualizado não pode
    desfazer a importação que acabou de dar certo, mas a falha fica no log."""
    try:
        atualizar_indices_do_projeto(project_id)
    except Exception:
        _LOGGER.exception("Nao foi possivel recalcular o indice combinado do projeto %s.", project_id)
