"""
Modelo de um projeto do Teste Sensorial.

Junta as tabelas gravadas (`sensorial_db.load_project_bundle`) num retrato da
análise: as sessões com código, condição e situação em cada camada; as
janelas de EEG com os índices do relatório e os indicadores do pipeline; as
janelas dos periféricos com o índice emocional e o conforto; as tentativas do
teste de associação com o CR. Cada janela, tentativa e sessão diz se entra e,
quando não entra, por quê — a tela de Participantes e as exportações mostram
isso.

Ordem das regras de uma sessão, numa camada:
1. automáticas: sem código de participante ou com condição fora do desenho
   ficam fora; repetida (mesmo participante e condição) fica marcada;
2. a BASE LIMPA da camada, quando enviada, decide as sessões e as janelas;
3. a decisão do usuário (incluir ou excluir, com motivo) vale por último.

Funções puras: nenhuma chama o banco nem a sessão do Streamlit.
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from utils import sensorial_cleaning, sensorial_db, sensorial_design, sensorial_indices
from utils.jornada_taxonomy import fold

LAYERS = ("eeg", "fc", "gsr", "associacao")
LAYER_LABELS = {"eeg": "EEG", "fc": "Frequência cardíaca", "gsr": "Condutância da pele",
                "associacao": "Teste de associação"}
_BASE_LIMPA_ROLE = {"eeg": "base_limpa_eeg", "fc": "base_limpa_perifericos", "gsr": "base_limpa_perifericos",
                    "associacao": "base_limpa_associacao"}
_SESSION_COLUMNS = ("sessao_id", "participant_code", "experimento", "amostra", "data", "hora", "avisos_sessao")
_SESSION_SOURCES = ("eeg_qualidade", "perifericos_qualidade", "eeg_psd", "eeg_indicadores", "perifericos_metricas",
                    "associacao_tentativas", "eeg_psd_medio")
_WINDOW_COLUMNS = ("sessao_id", "Etapa", "Etapa_variante", "Etapa_original", "Bloco", "Tempo", "primeira_olfacao_s")
_EPSILON = 1e-6


def _issue(issues: List[Dict], level: str, code: str, message: str) -> None:
    issues.append({"level": level, "code": code, "message": message})


def _tables(bundle: Dict) -> Dict[str, pd.DataFrame]:
    """A tabela ativa de cada papel com tabela (a mais nova, se houver mais de uma)."""
    tables: Dict[str, pd.DataFrame] = {}
    for item in sorted(bundle.get("files") or [], key=lambda f: f["id"]):
        if item.get("table_path"):
            tables[item["role"]] = sensorial_db.read_file_table(item)
    return tables


# ---------------------------------------------------------------------------
# Sessões
# ---------------------------------------------------------------------------

def _session_catalog(tables: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    parts = []
    for role in _SESSION_SOURCES:
        table = tables.get(role)
        if table is None or "sessao_id" not in table:
            continue
        columns = [column for column in _SESSION_COLUMNS if column in table]
        parts.append(table[columns].drop_duplicates("sessao_id"))
    if not parts:
        return pd.DataFrame(columns=list(_SESSION_COLUMNS))
    merged = pd.concat(parts, ignore_index=True)
    catalog = merged.groupby("sessao_id", sort=True).first().reset_index()
    for column in _SESSION_COLUMNS:
        if column not in catalog:
            catalog[column] = None
    return catalog[list(_SESSION_COLUMNS)]


def _decisions(bundle: Dict) -> Dict[str, Dict[str, Dict]]:
    """Decisões do usuário: sessão -> camada -> linha."""
    result: Dict[str, Dict[str, Dict]] = {}
    for row in bundle.get("sessions") or []:
        result.setdefault(str(row["sessao_id"]), {})[row["layer"]] = row
    return result


def _apply_overrides(sessions: pd.DataFrame, decisions: Dict[str, Dict[str, Dict]]) -> pd.DataFrame:
    sessions = sessions.copy()
    sessions["codigo_corrigido"] = False
    sessions["condicao_corrigida"] = ""
    for index, sessao_id in sessions["sessao_id"].items():
        general = decisions.get(str(sessao_id), {}).get("todas") or {}
        if general.get("participant_code_override"):
            sessions.at[index, "participant_code"] = general["participant_code_override"]
            sessions.at[index, "codigo_corrigido"] = True
        if general.get("condition_override"):
            sessions.at[index, "condicao_corrigida"] = general["condition_override"]
    return sessions


def _layer_counts(sessions: pd.DataFrame, tables: Dict[str, pd.DataFrame], stages: List[str]) -> pd.DataFrame:
    """Janelas e tentativas de cada sessão; `_analise` conta só as das etapas da análise."""
    for role, column in (("eeg_psd", "janelas_eeg"), ("perifericos_metricas", "janelas_perifericos"),
                         ("associacao_tentativas", "tentativas")):
        table = tables.get(role)
        if role == "eeg_psd" and table is None:
            table = tables.get("eeg_indicadores")
        if table is None or "sessao_id" not in table:
            table = pd.DataFrame(columns=["sessao_id"])
        sessions[column] = sessions["sessao_id"].map(table["sessao_id"].value_counts()).fillna(0).astype(int)
        if "Etapa" in table:
            in_stage = table.loc[table["Etapa"].isin(stages), "sessao_id"].value_counts()
            sessions[column + "_analise"] = sessions["sessao_id"].map(in_stage).fillna(0).astype(int)
        else:
            sessions[column + "_analise"] = sessions[column]
    return sessions


def _session_status(sessions: pd.DataFrame, tables: Dict[str, pd.DataFrame], decisions: Dict,
                    settings: Dict) -> pd.DataFrame:
    """`incluida_<camada>` e `motivo_<camada>` de cada sessão, na ordem das regras."""
    use_base = bool(settings["limpeza"].get("usar_base_limpa", True))
    for layer in LAYERS:
        included = pd.Series(True, index=sessions.index)
        reason = pd.Series("", index=sessions.index, dtype=object)
        no_code = sessions["participant_code"].isna() | (sessions["participant_code"].astype(str).str.strip() == "")
        no_condition = sessions["condicao"] == ""
        included[no_condition] = False
        reason[no_condition] = "condição fora do desenho"
        included[no_code] = False
        reason[no_code] = "sem código de participante"
        keys = tables.get(_BASE_LIMPA_ROLE[layer]) if use_base else None
        if keys is not None and not keys.empty:
            outside = ~sessions["sessao_id"].isin(set(keys["sessao_id"].astype(str))) & included
            included[outside] = False
            reason[outside] = "fora da BASE LIMPA"
        for index, sessao_id in sessions["sessao_id"].items():
            chosen = decisions.get(str(sessao_id), {})
            for scope in ("todas", layer):
                decision = chosen.get(scope)
                if not decision or decision["status"] == "auto":
                    continue
                if decision["status"] == "excluida":
                    included[index] = False
                    reason[index] = "excluída: {}".format(decision.get("reason") or "sem motivo")
                elif not no_code[index] and not no_condition[index]:
                    included[index] = True
                    reason[index] = "incluída por decisão"
        sessions["incluida_" + layer] = included
        sessions["motivo_" + layer] = reason
    return sessions


# ---------------------------------------------------------------------------
# Camadas
# ---------------------------------------------------------------------------

def _eeg_windows(tables: Dict[str, pd.DataFrame], sessions: pd.DataFrame, stages: List[str],
                 settings: Dict, issues: List[Dict]) -> pd.DataFrame:
    psd = tables.get("eeg_psd")
    indicators = tables.get("eeg_indicadores")
    base = psd if psd is not None else indicators
    if base is None:
        return pd.DataFrame()
    eeg = base[[column for column in _WINDOW_COLUMNS if column in base]].copy()
    flags = None
    if psd is not None and sensorial_indices.has_bands(psd):
        eeg = pd.concat([eeg, sensorial_indices.compute_indices(psd)], axis=1)
    else:
        _issue(issues, "warn", "sem_psd",
               "Sem o PSD por janela: os índices do relatório não são calculados, só os indicadores do pipeline.")
    if indicators is not None and psd is not None:
        values = [c for c in indicators.columns if c not in _WINDOW_COLUMNS and c not in _SESSION_COLUMNS
                  and pd.api.types.is_numeric_dtype(indicators[c])]
        keyed = indicators[values].copy()
        keyed.index = sensorial_cleaning.window_key(indicators)
        keyed = keyed[~keyed.index.duplicated()]
        joined = keyed.reindex(sensorial_cleaning.window_key(eeg))
        joined.index = eeg.index
        eeg = pd.concat([eeg, joined], axis=1)
    elif indicators is not None:
        values = [c for c in indicators.columns if c not in _WINDOW_COLUMNS and c not in _SESSION_COLUMNS
                  and pd.api.types.is_numeric_dtype(indicators[c])]
        eeg = pd.concat([eeg, indicators[values]], axis=1)

    by_session = sessions.set_index("sessao_id")
    eeg["participant_code"] = eeg["sessao_id"].map(by_session["participant_code"])
    eeg["condicao"] = eeg["sessao_id"].map(by_session["condicao"]).fillna("")
    session_in = eeg["sessao_id"].map(by_session["incluida_eeg"]).fillna(False).astype(bool)
    session_reason = eeg["sessao_id"].map(by_session["motivo_eeg"]).fillna("sessão sem catálogo")
    forced = session_reason == "incluída por decisão"
    in_stage = eeg["Etapa"].isin(stages)

    keys = tables.get("base_limpa_eeg") if settings["limpeza"].get("usar_base_limpa", True) else None
    in_base = sensorial_cleaning.in_base_limpa(eeg, keys)
    if in_base is None:
        in_base = pd.Series(True, index=eeg.index)
    elif settings["limpeza"].get("regra_spss"):
        _issue(issues, "info", "regra_spss_ignorada",
               "A BASE LIMPA do EEG decide as janelas; a regra de outliers do SPSS não é aplicada por cima.")
    valid = pd.Series(True, index=eeg.index)
    if settings["limpeza"].get("regra_spss") and keys is None and psd is not None:
        candidates = in_stage & session_in
        if candidates.any():
            flags = sensorial_cleaning.outlier_flags(
                psd.loc[candidates], sensorial_indices.BAND_COLUMNS, z=float(settings["limpeza"]["z"]),
                p_mahalanobis=float(settings["limpeza"]["p_mahalanobis"]),
                min_marks=int(settings["limpeza"]["min_marcas"]))
            valid.loc[flags.index] = flags["valida"]
            eeg["marcas_outlier"] = np.nan
            eeg.loc[flags.index, "marcas_outlier"] = flags["marcas"]
    included = session_in & in_stage & (in_base | forced) & valid
    reason = pd.Series("", index=eeg.index, dtype=object)
    reason[~valid] = "outlier pela regra do SPSS"
    reason[~in_base & ~forced] = "fora da BASE LIMPA"
    reason[~in_stage] = "etapa fora da análise"
    reason[~session_in] = session_reason[~session_in]
    eeg["incluida"] = included
    eeg["motivo"] = reason
    eeg["PPI"] = np.nan
    if "FAI" in eeg and included.any():
        ppi_settings = settings["indices"]["ppi"]
        eeg.loc[included, "PPI"] = sensorial_indices.ppi(eeg.loc[included], ppi_settings.get("modo", "z"),
                                                          ppi_settings.get("pesos"))
    return eeg


def _peripheral_windows(tables: Dict[str, pd.DataFrame], sessions: pd.DataFrame, stages: List[str],
                        settings: Dict, issues: List[Dict]) -> pd.DataFrame:
    metrics = tables.get("perifericos_metricas")
    if metrics is None:
        return pd.DataFrame()
    peri = metrics.copy()
    for column in ("GSR_CAL_zscore", "BPM_zscore", "RMSSD_zscore"):
        if column not in peri:
            peri[column] = np.nan
    gsr_z = peri["GSR_CAL_zscore"].astype("float64")
    bpm_z = peri["BPM_zscore"].astype("float64")
    # Sintaxe do SPSS (modelo de Cerf): ângulo entre GSR e BPM padronizados, e RMSSD numa sigmoide.
    peri["Emotional_Index"] = np.cos(np.arctan(gsr_z / (bpm_z + _EPSILON)))
    peri["Comfort_Score"] = 1.0 / (1.0 + np.exp(-peri["RMSSD_zscore"].astype("float64")))
    by_session = sessions.set_index("sessao_id")
    peri["participant_code"] = peri["sessao_id"].map(by_session["participant_code"])
    peri["condicao"] = peri["sessao_id"].map(by_session["condicao"]).fillna("")
    in_stage = peri["Etapa"].isin(stages) if "Etapa" in peri else pd.Series(True, index=peri.index)
    keys = tables.get("base_limpa_perifericos") if settings["limpeza"].get("usar_base_limpa", True) else None
    in_base = sensorial_cleaning.in_base_limpa(peri, keys)
    require_quality = bool(settings["perifericos"].get("exigir_qualidade", True)) and in_base is None
    for layer, quality_column in (("fc", "qualidade_fc"), ("gsr", "qualidade_gsr")):
        session_in = peri["sessao_id"].map(by_session["incluida_" + layer]).fillna(False).astype(bool)
        session_reason = peri["sessao_id"].map(by_session["motivo_" + layer]).fillna("sessão sem catálogo")
        forced = session_reason == "incluída por decisão"
        quality_ok = (peri[quality_column].astype(str).str.lower() == "ok") if quality_column in peri else \
            pd.Series(True, index=peri.index)
        selected = (in_base | forced) if in_base is not None else pd.Series(True, index=peri.index)
        good = quality_ok if require_quality else pd.Series(True, index=peri.index)
        reason = pd.Series("", index=peri.index, dtype=object)
        reason[~good] = "sinal ruim: " + peri[quality_column].astype(str) if quality_column in peri else ""
        reason[~selected] = "fora da BASE LIMPA"
        reason[~in_stage] = "etapa fora da análise"
        reason[~session_in] = session_reason[~session_in]
        peri["incluida_" + layer] = session_in & in_stage & selected & good
        peri["motivo_" + layer] = reason
    if in_base is not None and "qualidade_fc" in peri:
        kept_bad = peri["incluida_fc"] & (peri["qualidade_fc"].astype(str).str.lower() != "ok")
        if kept_bad.any():
            _issue(issues, "warn", "perifericos_qualidade",
                   "{} janela(s) da BASE LIMPA dos periféricos têm o pulso marcado como ruim pelo pipeline; "
                   "a BASE LIMPA foi seguida.".format(int(kept_bad.sum())))
    return peri


def _association_trials(tables: Dict[str, pd.DataFrame], sessions: pd.DataFrame, settings: Dict,
                        issues: List[Dict]) -> pd.DataFrame:
    trials = tables.get("associacao_tentativas")
    if trials is None:
        return pd.DataFrame()
    trials = trials.copy()
    empty = trials["palavra"].isna() | (trials["palavra"].astype(str).str.strip() == "")
    if empty.any():
        _issue(issues, "info", "tentativas_sem_palavra",
               "{} tentativa(s) sem palavra ficaram de fora do teste de associação.".format(int(empty.sum())))
    trials = trials[~empty].copy()
    by_session = sessions.set_index("sessao_id")
    trials["participant_code"] = trials["sessao_id"].map(by_session["participant_code"])
    trials["condicao"] = trials["sessao_id"].map(by_session["condicao"]).fillna("")
    session_in = trials["sessao_id"].map(by_session["incluida_associacao"]).fillna(False).astype(bool)
    session_reason = trials["sessao_id"].map(by_session["motivo_associacao"]).fillna("sessão sem catálogo")
    forced = session_reason == "incluída por decisão"
    keys = tables.get("base_limpa_associacao") if settings["limpeza"].get("usar_base_limpa", True) else None
    in_base = sensorial_cleaning.in_base_limpa(trials, keys)
    if in_base is None:
        in_base = pd.Series(True, index=trials.index)
    reason = pd.Series("", index=trials.index, dtype=object)
    reason[~in_base & ~forced] = "fora da BASE LIMPA"
    reason[~session_in] = session_reason[~session_in]
    trials["incluida"] = session_in & (in_base | forced)
    trials["motivo"] = reason
    trials["rt"] = pd.to_numeric(trials["rt"], errors="coerce")
    trials["sim"] = trials["resposta"].map(lambda value: fold(value) in ("sim", "s", "yes", "y"))
    # CR da tentativa: o quanto ela foi mais rápida que a média da própria sessão, em desvios.
    included = trials[trials["incluida"]]
    stats = included.groupby("sessao_id")["rt"].agg(["mean", "std"])
    mean = trials["sessao_id"].map(stats["mean"])
    deviation = trials["sessao_id"].map(stats["std"]).replace(0, np.nan)
    trials["cr"] = ((mean - trials["rt"]) / deviation).where(trials["incluida"])
    return trials


# ---------------------------------------------------------------------------
# Participantes, qualidade e o modelo inteiro
# ---------------------------------------------------------------------------

def _participants(bundle: Dict, sessions: pd.DataFrame, field_log: Optional[pd.DataFrame]) -> pd.DataFrame:
    coded = sessions[sessions["participant_code"].notna()]
    rows: Dict[str, Dict[str, Any]] = {}
    for code, group in coded.groupby("participant_code"):
        rows[str(code)] = {"participant_code": str(code), "sessoes": int(len(group)),
                           "condicoes": ", ".join(sorted({c for c in group["condicao"] if c}))}
    for item in bundle.get("participants") or []:
        row = rows.setdefault(item["code"], {"participant_code": item["code"], "sessoes": 0, "condicoes": ""})
        for key, value in (item.get("profile") or {}).items():
            row["perfil_" + str(key)] = value
        row["notas"] = item.get("notes")
    table = pd.DataFrame(sorted(rows.values(), key=lambda r: r["participant_code"]))
    if table.empty:
        return pd.DataFrame(columns=["participant_code", "sessoes", "condicoes"])
    if field_log is not None and not field_log.empty:
        log = field_log.drop_duplicates("participant_code", keep="last").set_index("participant_code")
        for column in ("n_canais_problema", "canais_problema", "qualidade_sinal", "observacao"):
            if column in log:
                table["campo_" + column] = table["participant_code"].map(log[column])
    return table


def build_model(bundle: Dict) -> Dict[str, Any]:
    """O modelo do projeto a partir do bundle de `sensorial_db.load_project_bundle`."""
    settings = sensorial_design.resolve_settings(bundle.get("settings"))
    issues: List[Dict] = []
    tables = _tables(bundle)
    files = bundle.get("files") or []
    manifests = [item["meta"].get("manifesto") or {} for item in files if item["role"] == "manifesto"]

    sessions = _session_catalog(tables)
    decisions = _decisions(bundle)
    sessions = _apply_overrides(sessions, decisions)
    stages_seen: List[str] = []
    for role in ("eeg_psd", "eeg_indicadores", "perifericos_metricas"):
        if role in tables and "Etapa" in tables[role]:
            stages_seen += [s for s in tables[role]["Etapa"].dropna().astype(str).unique() if s not in stages_seen]
    deduced = sensorial_design.deduce_design(sessions, stages_seen, manifests)
    design = sensorial_design.resolve_design(settings, deduced)
    sessions["condicao"] = [
        corrected or sensorial_design.condition_for(experimento, amostra, design)
        for corrected, experimento, amostra in zip(sessions["condicao_corrigida"], sessions["experimento"],
                                                   sessions["amostra"])]
    roles = sensorial_design.condition_roles(design)
    sessions["papel"] = sessions["condicao"].map(roles).fillna("")
    sessions = _layer_counts(sessions, tables, sensorial_design.analysis_stages(design))
    # Repetida, por camada: outra sessão do mesmo participante, na mesma condição, com dado
    # nessa camada. Sessão dividida (o teste numa, o EEG noutra) não é repetição.
    layer_data = {"eeg": sessions["janelas_eeg_analise"] > 0, "fc": sessions["janelas_perifericos_analise"] > 0,
                  "gsr": sessions["janelas_perifericos_analise"] > 0, "associacao": sessions["tentativas"] > 0}
    coded = sessions["participant_code"].notna() & (sessions["condicao"] != "")
    for layer, has_data in layer_data.items():
        pairs = sessions[coded & has_data]
        repeated = pairs.duplicated(["participant_code", "condicao"], keep=False)
        sessions["repetida_" + layer] = sessions.index.isin(pairs.index[repeated])
    sessions["repetida"] = sessions[["repetida_" + layer for layer in LAYERS]].any(axis=1)
    sessions = _session_status(sessions, tables, decisions, settings)

    stages = sensorial_design.analysis_stages(design)
    eeg = _eeg_windows(tables, sessions, stages, settings, issues)
    peripherals = _peripheral_windows(tables, sessions, stages, settings, issues)
    trials = _association_trials(tables, sessions, settings, issues)
    participants = _participants(bundle, sessions, tables.get("campo_qualidade"))

    if sessions.empty:
        _issue(issues, "warn", "sem_dados", "O projeto ainda não tem saídas do pipeline gravadas.")
    without_code = int(sessions["participant_code"].isna().sum())
    if without_code:
        _issue(issues, "warn", "sem_codigo",
               "{} sessão(ões) sem código de participante ficam fora; corrija em Participantes.".format(without_code))
    for layer in ("eeg", "fc", "associacao"):
        flagged = sessions["repetida_" + layer] if "repetida_" + layer in sessions else pd.Series(dtype=bool)
        if flagged.any():
            codes = sorted(sessions.loc[flagged, "participant_code"].astype(str).unique())
            _issue(issues, "warn", "repetidas",
                   "{}: {} participante(s) com mais de uma sessão na mesma condição ({}); todas entram até "
                   "alguém decidir em Participantes.".format(
                       "Periféricos" if layer == "fc" else LAYER_LABELS[layer], len(codes), ", ".join(codes)))
    field_log = tables.get("campo_qualidade")
    if field_log is not None and not sessions.empty:
        unknown = sorted(set(field_log["participant_code"]) - set(sessions["participant_code"].dropna()))
        if unknown:
            _issue(issues, "info", "campo_sem_sessao",
                   "O registro de campo cita {} código(s) sem sessão no pipeline: {}.".format(
                       len(unknown), ", ".join(unknown[:12])))
    for layer, role in _BASE_LIMPA_ROLE.items():
        if layer != "gsr" and role in tables and settings["limpeza"].get("usar_base_limpa", True):
            _issue(issues, "info", "base_limpa_" + layer,
                   "A BASE LIMPA decide a camada {}.".format(LAYER_LABELS[layer]))

    topomaps = [{"file_id": item["id"], "filename": item["filename"], "run_id": item.get("run_id"),
                 "experimento": item["meta"].get("experimento"), "etapa_arquivo": item["meta"].get("etapa_arquivo")}
                for item in files if item["role"] == "eeg_topomapa"]
    indicator_columns = [c for c in eeg.columns if c not in sensorial_indices.BAND_COLUMNS
                         and c not in sensorial_design.INDEX_CODES and c not in _WINDOW_COLUMNS
                         and c not in ("participant_code", "condicao", "incluida", "motivo", "PPI", "marcas_outlier")
                         and pd.api.types.is_numeric_dtype(eeg[c])] if not eeg.empty else []
    return {
        "project": bundle.get("project") or {},
        "data_version": int((bundle.get("project") or {}).get("data_version") or 0),
        "settings": settings,
        "design": design,
        "deduced_design": deduced,
        "index_names": sensorial_design.index_names(settings),
        "indices": [code for code in sensorial_design.INDEX_CODES if code in eeg],
        "pipeline_indicators": indicator_columns,
        "sessions": sessions,
        "participants": participants,
        "eeg": eeg,
        "peripherals": peripherals,
        "trials": trials,
        "claims": sensorial_design.resolve_claims(settings, trials["palavra"] if not trials.empty else []),
        "quality": {"eeg": tables.get("eeg_qualidade"), "perifericos": tables.get("perifericos_qualidade"),
                    "campo": field_log},
        "inventory": tables.get("inventario"),
        "psd_means": tables.get("eeg_psd_medio"),
        "topomaps": topomaps,
        "manifests": manifests,
        "issues": issues,
    }
