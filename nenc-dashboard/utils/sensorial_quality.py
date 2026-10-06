"""
Qualidade das sessões do Teste Sensorial, por camada.

Cada sessão recebe um status em cada camada — "pass" (OK), "warn" (atenção),
"fail" (problema) ou "na" (a camada não se aplica) — com o motivo em texto:

- **EEG**: janelas nas etapas da análise (cada uma tem 0,25 s), a observação
  do pipeline (sessão só com marcadores) e os canais que o registro de campo
  marcou com problema para o participante;
- **frequência cardíaca**: fração das janelas com o pulso "ok" para o
  pipeline, pulso ausente no PPG e PPG invertido;
- **condutância da pele**: fração das janelas com o GSR "ok";
- **teste de associação**: tentativas com palavra.

Os limiares vêm em camadas: os padrões daqui e, por cima, o
`quality_thresholds` do projeto.
"""

import json
from typing import Any, Dict, List, Optional

import pandas as pd

from utils.prosodia_quality import compute_overall_status, status_badge

DEFAULT_THRESHOLDS: Dict[str, float] = {
    # 100 janelas de 0,25 s = 25 s de sinal nas etapas da análise.
    "eeg_janelas_alerta": 100,
    "eeg_janelas_problema": 20,
    # Canais com problema no registro de campo, de 16.
    "canais_alerta": 1,
    "canais_problema": 3,
    # Fração das janelas com qualidade "ok" no pipeline.
    "fc_ok_alerta": 0.8,
    "fc_ok_problema": 0.5,
    "gsr_ok_alerta": 0.8,
    "gsr_ok_problema": 0.5,
    # Tentativas com palavra numa sessão do teste de associação.
    "tentativas_alerta": 5,
}
LAYERS = ("eeg", "fc", "gsr", "associacao")
STATUS_ICONS = {"pass": "✓", "warn": "!", "fail": "✗", "na": "—"}


def thresholds_for_project(project: Optional[Dict[str, Any]]) -> Dict[str, float]:
    merged = dict(DEFAULT_THRESHOLDS)
    raw = (project or {}).get("quality_thresholds")
    if raw:
        try:
            custom = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (TypeError, ValueError):
            custom = {}
        merged.update({key: float(value) for key, value in custom.items() if key in DEFAULT_THRESHOLDS})
    return merged


def _share_ok(windows: pd.DataFrame, column: str) -> pd.Series:
    if windows.empty or column not in windows:
        return pd.Series(dtype=float)
    ok = windows[column].astype(str).str.lower().eq("ok")
    return ok.groupby(windows["sessao_id"]).mean()


def _level(value: float, warn: float, fail: float, higher_is_better: bool = True) -> str:
    if value != value:
        return "na"
    if higher_is_better:
        return "fail" if value < fail else ("warn" if value < warn else "pass")
    return "fail" if value >= fail else ("warn" if value >= warn else "pass")


def session_quality(model: Dict[str, Any], thresholds: Optional[Dict[str, float]] = None) -> pd.DataFrame:
    """Uma linha por sessão: `qualidade_<camada>` e `detalhe_<camada>`, e a `qualidade` geral."""
    limits = dict(DEFAULT_THRESHOLDS, **(thresholds or {}))
    sessions = model["sessions"]
    columns = ["sessao_id"] + ["{}_{}".format(part, layer) for layer in LAYERS for part in ("qualidade", "detalhe")] \
        + ["qualidade"]
    if sessions.empty:
        return pd.DataFrame(columns=columns)
    quality = model.get("quality") or {}
    eeg_dqa = quality.get("eeg")
    observations = (eeg_dqa.set_index("sessao_id")["observacao"].dropna()
                    if eeg_dqa is not None and "observacao" in eeg_dqa else pd.Series(dtype=object))
    peri_dqa = quality.get("perifericos")
    pulse = peri_dqa.drop_duplicates("sessao_id").set_index("sessao_id") if peri_dqa is not None else pd.DataFrame()
    field = quality.get("campo")
    channels = (field.drop_duplicates("participant_code", keep="last").set_index("participant_code")
                if field is not None and not field.empty else pd.DataFrame())
    peripherals = model["peripherals"]
    analysis = peripherals[peripherals["Etapa"].isin({s["codigo"] for s in model["design"].get("etapas") or []})] \
        if not peripherals.empty and "Etapa" in peripherals else peripherals
    fc_share = _share_ok(analysis, "qualidade_fc")
    gsr_share = _share_ok(analysis, "qualidade_gsr")

    rows: List[Dict[str, Any]] = []
    for session in sessions.itertuples():
        row: Dict[str, Any] = {"sessao_id": session.sessao_id}
        # EEG
        windows = int(session.janelas_eeg_analise)
        status = _level(windows, limits["eeg_janelas_alerta"], limits["eeg_janelas_problema"])
        reasons = ["{} janela(s) nas etapas da análise".format(windows)]
        if windows == 0:
            status = "fail" if session.janelas_eeg == 0 else "warn"
            reasons = ["sem janela nas etapas da análise"]
        note = observations.get(session.sessao_id)
        if isinstance(note, str) and note.strip():
            reasons.append(note.strip())
        code = session.participant_code
        if isinstance(code, str) and code in channels.index and "n_canais_problema" in channels:
            bad = int(channels.at[code, "n_canais_problema"] or 0)
            if bad:
                channel_status = _level(bad, limits["canais_alerta"], limits["canais_problema"], False)
                status = compute_overall_status([{"status": status}, {"status": channel_status}])
                reasons.append("canal(is) {} com problema no campo".format(channels.at[code, "canais_problema"]))
        row["qualidade_eeg"], row["detalhe_eeg"] = status, "; ".join(reasons)
        # Frequência cardíaca e GSR
        for layer, shares, prefix in (("fc", fc_share, "pulso"), ("gsr", gsr_share, "GSR")):
            share = shares.get(session.sessao_id, float("nan"))
            status = _level(share, limits[layer + "_ok_alerta"], limits[layer + "_ok_problema"])
            reasons = [] if share != share else ["{} ok em {:.0%} das janelas".format(prefix, share)]
            if layer == "fc" and session.sessao_id in pulse.index:
                info = pulse.loc[session.sessao_id]
                if str(info.get("pulso_ok")).lower() == "false":
                    status, reasons = "fail", reasons + ["sem pulso no PPG"]
                if str(info.get("ppg_invertido")).lower() == "true":
                    status = compute_overall_status([{"status": status}, {"status": "warn"}])
                    reasons.append("PPG invertido")
            row["qualidade_" + layer], row["detalhe_" + layer] = status, "; ".join(reasons) or "sem janela"
        # Teste de associação
        trials = int(session.tentativas)
        if trials:
            row["qualidade_associacao"] = "warn" if trials < limits["tentativas_alerta"] else "pass"
            row["detalhe_associacao"] = "{} tentativa(s)".format(trials)
        else:
            row["qualidade_associacao"], row["detalhe_associacao"] = "na", "sem teste nesta sessão"
        row["qualidade"] = compute_overall_status(
            [{"status": row["qualidade_" + layer]} for layer in LAYERS if row["qualidade_" + layer] != "na"])
        rows.append(row)
    return pd.DataFrame(rows, columns=columns)


def badge(status: str) -> str:
    return "{} {}".format(STATUS_ICONS.get(status, "—"), status_badge(status) if status != "na" else "não se aplica")
