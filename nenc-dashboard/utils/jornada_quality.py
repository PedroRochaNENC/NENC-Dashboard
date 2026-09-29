"""
Qualidade das gravações da Jornada de Compra.

O equivalente do `prosodia_quality` do NencBoost: checagens objetivas por
gravação, com limiares padrão que mudam conforme o TIPO DE TAREFA (o papel que
o tipo de projeto tem no NencBoost) e que o projeto pode sobrescrever.

Nada aqui é gravado: as checagens saem do modelo, que já traz taxa de
amostragem, perdas, duração, unidade e status de cada gravação, e são
recalculadas quando os dados mudam.
"""

import json
import math
from typing import Dict, List, Optional

import pandas as pd

from utils.prosodia_quality import compute_overall_status, status_badge

__all__ = [
    "DEFAULT_THRESHOLDS",
    "DEFAULT_THRESHOLDS_BY_TASK",
    "check_recording",
    "compute_overall_status",
    "default_thresholds",
    "run_quality",
    "status_badge",
    "thresholds_for_project",
]

DEFAULT_THRESHOLDS = {
    # Abaixo de 20 Hz as fixações curtas somem; abaixo de 10 Hz o tempo por AOI
    # vira poucos pontos. O rastreador do estudo que originou o módulo roda ~23 Hz.
    "hz_warn": 20.0,
    "hz_fail": 10.0,
    # Tempo perdido em falhas de mais de 250 ms, em % da gravação.
    "loss_warn_pct": 5.0,
    "loss_fail_pct": 15.0,
    # Uma falha isolada longa esconde exatamente o momento de decisão.
    "gap_warn_s": 2.0,
    "min_duration_s": 15.0,
}
# O que muda por tarefa: uma jornada livre inclui a caminhada até a categoria;
# a estimulada e a de embalagens começam diante do estímulo.
DEFAULT_THRESHOLDS_BY_TASK = {
    "livre": {"min_duration_s": 60.0},
    "estimulada": {"min_duration_s": 15.0},
    "embalagens": {"min_duration_s": 30.0},
    "outra": {},
}


def default_thresholds(task: Optional[str] = None) -> Dict[str, float]:
    merged = dict(DEFAULT_THRESHOLDS)
    merged.update(DEFAULT_THRESHOLDS_BY_TASK.get(task or "", {}))
    return merged


def _custom(project: Optional[Dict]) -> Dict:
    raw = (project or {}).get("quality_thresholds")
    if not raw:
        return {}
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def thresholds_for_project(project: Optional[Dict], task: Optional[str] = None) -> Dict[str, float]:
    """Limiares efetivos: padrão da tarefa, depois o do projeto para todas as
    tarefas (`"*"`), depois o do projeto para a tarefa.

    Como no NencBoost, "usar valores padrão" grava NULL, e o padrão só se
    resolve aqui.
    """
    merged = default_thresholds(task)
    custom = _custom(project)
    for scope in ("*", task or ""):
        for key, value in (custom.get(scope) or {}).items():
            if key in merged:
                try:
                    merged[key] = float(value)
                except (TypeError, ValueError):
                    pass
    return merged


def _check(check_id: str, label: str, status: str, detail: str, value: object = None) -> Dict:
    return {"id": check_id, "label": label, "status": status, "detail": detail, "value": value}


def _number(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    return number


def check_recording(record: Dict, thresholds: Dict[str, float], profile: str = "") -> List[Dict]:
    """Checagens de uma gravação (uma linha de `model["recordings"]`)."""

    checks: List[Dict] = []
    hz = _number(record.get("hz"))
    if not record.get("has_frames"):
        checks.append(_check(
            "taxa", "Taxa de amostragem", "warn",
            "Sem o arquivo de quadros: taxa e perdas não verificadas.",
        ))
    elif hz < thresholds["hz_fail"]:
        checks.append(_check("taxa", "Taxa de amostragem", "fail",
                             "{:.1f} Hz, abaixo de {:.0f} Hz.".format(hz, thresholds["hz_fail"]), hz))
    elif hz < thresholds["hz_warn"]:
        checks.append(_check("taxa", "Taxa de amostragem", "warn",
                             "{:.1f} Hz, abaixo de {:.0f} Hz: fixações curtas se perdem.".format(
                                 hz, thresholds["hz_warn"]), hz))
    else:
        checks.append(_check("taxa", "Taxa de amostragem", "pass", "{:.1f} Hz.".format(hz), hz))

    if record.get("has_frames"):
        loss = _number(record.get("loss_pct"))
        if loss == loss and loss >= thresholds["loss_fail_pct"]:
            status = "fail"
        elif loss == loss and loss >= thresholds["loss_warn_pct"]:
            status = "warn"
        else:
            status = "pass"
        checks.append(_check("perda", "Perda de rastreio", status,
                             "{:.1f}% do tempo em falhas de rastreio.".format(loss if loss == loss else 0), loss))
        gap = _number(record.get("max_gap_s"))
        checks.append(_check(
            "falha", "Maior falha", "warn" if gap == gap and gap >= thresholds["gap_warn_s"] else "pass",
            "Maior intervalo sem olhar: {:.1f} s.".format(gap if gap == gap else 0), gap,
        ))

    duration = _number(record.get("duration_s"))
    if duration == duration:
        short = duration < thresholds["min_duration_s"]
        checks.append(_check(
            "duracao", "Duração", "warn" if short else "pass",
            "{:.0f} s{}".format(
                duration,
                " — menos que {:.0f} s esperados para a tarefa.".format(thresholds["min_duration_s"])
                if short else ".",
            ),
            duration,
        ))

    status = record.get("status")
    coding = {
        "incluida": ("pass", "AOIs codificadas; entra na análise."),
        "agregado": ("pass", "Só existe o agregado do grupo; entra nas análises agregadas."),
        "nao_codificada": ("fail", "Todas as AOIs zeradas: ninguém codificou esta gravação."),
        "sem_aoi": ("warn", "Gravação sem AOIs codificadas; fica fora da análise."),
        "excluida": ("warn", "Excluída da análise: {}.".format(record.get("status_reason") or "sem motivo")),
    }.get(status, ("warn", "Situação desconhecida."))
    checks.append(_check("codificacao", "Codificação", coding[0], coding[1]))

    if status == "incluida":
        conversion = record.get("conversion") or ""
        evidence = record.get("unit_evidence") or ""
        if not record.get("unit"):
            unit_check = ("warn", "Unidade não identificada.")
        elif conversion == "sem conversão":
            unit_check = ("warn", "Export em amostras sem quadros nem Hz nominal: sem tempos em segundos.")
        elif evidence.startswith("ambigua"):
            unit_check = ("warn", "Unidade incerta ({}).".format(evidence))
        elif conversion == "Hz nominal do projeto":
            unit_check = ("warn", "Convertido pelo Hz nominal, sem os quadros da gravação.")
        else:
            unit_check = ("pass", "{} ({}).".format(record.get("unit"), conversion or evidence))
        checks.append(_check("unidade", "Unidade de tempo", unit_check[0], unit_check[1]))

    checks.append(_check(
        "perfil", "Perfil do participante", "pass" if profile else "warn",
        profile or "Participante sem perfil: fica fora das comparações por perfil.",
    ))
    return checks


def run_quality(model: Dict, project: Optional[Dict] = None) -> Dict[str, pd.DataFrame]:
    """Checagens de todas as gravações: tabela longa e status por gravação."""

    recordings = model.get("recordings")
    if recordings is None or recordings.empty:
        empty = pd.DataFrame(columns=["recording_key", "id", "label", "status", "detail", "value"])
        return {"checks": empty, "summary": pd.DataFrame(columns=["recording_key", "quality", "quality_label"])}

    profiles = {}
    participants = model.get("participants")
    if participants is not None and not participants.empty:
        profiles = dict(zip(participants["participant"], participants["profile"]))

    rows = []
    summary = []
    for record in recordings.to_dict("records"):
        thresholds = thresholds_for_project(project, record.get("task"))
        checks = check_recording(record, thresholds, profiles.get(record["participant"], ""))
        for check in checks:
            rows.append(dict(check, recording_key=record["recording_key"]))
        overall = compute_overall_status(checks)
        summary.append({
            "recording_key": record["recording_key"],
            "quality": overall,
            "quality_label": status_badge(overall),
            "alerts": "; ".join(c["label"] for c in checks if c["status"] != "pass"),
        })
    return {"checks": pd.DataFrame(rows), "summary": pd.DataFrame(summary)}
