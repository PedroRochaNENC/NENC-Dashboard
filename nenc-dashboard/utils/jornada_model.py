"""
Modelo de dados de um projeto da Jornada de Compra.

Recebe o que o banco guarda (`jornada_db.load_project_bundle`: arquivos
brutos, participantes, decisoes sobre gravacoes, ajustes do catalogo) e
devolve tabelas prontas para as metricas, os graficos e as exportacoes.

O ponto delicado e a unidade. O Blickshift exporta duracoes e tempo ate a
primeira fixacao em segundos ou em amostras, conforme a coluna de tempo que o
analista escolheu — no mesmo estudo, uma loja veio em segundos e outra em
quadros. Por isso a unidade e decidida por GRAVACAO, comparando a razao
TotalGazeDuration / NormalizedGazeDuration (o comprimento da gravacao na
unidade do export) com os quadros da propria gravacao:

- razao ~ numero de quadros  -> amostras: duracoes x intervalo medio, e o
  TTFF (um numero de quadro) vira o timestamp daquele quadro;
- razao ~ duracao em segundos -> segundos, sem conversao.

Sem o arquivo de quadros, valores inteiros com razao >= 50 indicam amostras,
e a conversao usa o Hz nominal do projeto, se houver. Proporcoes (share,
alcance, primeira marca notada) nao dependem de unidade e sempre funcionam.

Uma gravacao com todas as AOIs zeradas nao e "atencao zero": no estudo que
originou este modulo, eram participantes que ninguem codificou. Ela fica como
`nao_codificada` e fora de todo denominador, ate alguem decidir o contrario.
"""

import math
from collections import Counter
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from utils.jornada_ingest import (
    METRIC_COLUMNS,
    TASK_LABELS,
    frames_timestamps,
    parse_duration_text,
    parse_upload,
)
from utils.jornada_taxonomy import (
    aoi_key,
    build_catalog,
    fold,
    normalize_label,
    suggest_brands,
)

RECORDING_STATUS_LABELS = {
    "incluida": "Incluída",
    "excluida": "Excluída",
    "nao_codificada": "Não codificada",
    "agregado": "Só agregado do grupo",
    "sem_aoi": "Só gravação (sem AOIs)",
}
UNIT_LABELS = {"segundos": "segundos", "amostras": "amostras (quadros)"}

_SAMPLES_TOLERANCE = 0.01
_SECONDS_TOLERANCE = 0.02


def recording_key(participant: str, task: str, store: str) -> str:
    return "{}|{}|{}".format(participant or "", task or "", store or "")


def _issue(issues: List[Dict], level: str, code: str, message: str, ref: str = "") -> None:
    issues.append({"level": level, "code": code, "message": message, "ref": ref})


def _median_ratio(rows: pd.DataFrame) -> float:
    valid = rows[(rows["TotalGazeDuration"] > 0) & (rows["NormalizedGazeDuration"] > 0)]
    if valid.empty:
        return math.nan
    return float((valid["TotalGazeDuration"] / valid["NormalizedGazeDuration"]).median())


_INTEGER_COLUMNS = (
    "TotalGazeDuration", "MaximumGazeDuration", "MinimumGazeDuration", "TimeToFirstFixation",
)
# No agregado o TTFF e media entre quem olhou: nao e inteiro nem em amostras.
_POOLED_INTEGER_COLUMNS = ("TotalGazeDuration", "MaximumGazeDuration", "MinimumGazeDuration")


def _looks_like_samples(
    rows: pd.DataFrame, ratio: float, columns: Tuple[str, ...] = _INTEGER_COLUMNS
) -> bool:
    values = pd.concat(
        [rows[column] for column in columns if column in rows],
        ignore_index=True,
    ).dropna()
    if values.empty or math.isnan(ratio):
        return False
    integral = np.all(np.isclose(values.to_numpy(), np.round(values.to_numpy()), atol=1e-6))
    return bool(integral and abs(ratio - round(ratio)) < 1e-3 and ratio >= 50)


def detect_unit(
    rows: pd.DataFrame, frames: Optional[Dict] = None
) -> Tuple[Optional[str], str]:
    """Unidade de uma gravacao: ("amostras" | "segundos" | None, evidencia)."""

    ratio = _median_ratio(rows)
    if math.isnan(ratio):
        return None, "sem AOI com olhar para comparar"
    if frames and frames.get("n_frames"):
        n = float(frames["n_frames"])
        duration = float(frames.get("duration_s") or math.nan)
        if abs(ratio - n) <= max(2.0, _SAMPLES_TOLERANCE * n):
            return "amostras", "comprimento do export = {:.0f} quadros".format(ratio)
        if duration == duration and abs(ratio - duration) <= max(0.5, _SECONDS_TOLERANCE * duration):
            return "segundos", "comprimento do export = {:.1f} s".format(ratio)
        unit = "amostras" if _looks_like_samples(rows, ratio) else "segundos"
        return unit, "ambigua: comprimento {:.1f} nao bate com {:.0f} quadros nem {:.1f} s".format(
            ratio, n, duration
        )
    unit = "amostras" if _looks_like_samples(rows, ratio) else "segundos"
    return unit, "heuristica (sem arquivo de quadros)"


def _convert_ttff(raw: float, timestamps: Optional[np.ndarray], t0: float, dt: float) -> Tuple[float, bool]:
    """TTFF em amostras -> segundos. Devolve (valor, usou_timestamp)."""

    if raw != raw:
        return math.nan, True
    if timestamps is not None and timestamps.size:
        index = int(round(raw))
        if 0 <= index < timestamps.size and timestamps[index] == timestamps[index]:
            return float(timestamps[index] - t0), True
    if dt == dt:
        return float(raw * dt), False
    return math.nan, False


def _latest_rows(frame: pd.DataFrame, keys: List[str]) -> Tuple[pd.DataFrame, int]:
    """Uma linha por chave, a do arquivo mais recente. Devolve (tabela, repetidas)."""

    if frame.empty:
        return frame, 0
    ordered = frame.sort_values("source_file_id")
    duplicated = int(ordered.duplicated(subset=keys, keep="last").sum())
    return ordered.drop_duplicates(subset=keys, keep="last").reset_index(drop=True), duplicated


def _most_common(values: List[str]) -> str:
    cleaned = [value for value in values if value]
    if not cleaned:
        return ""
    counts = Counter(cleaned)
    best = max(counts.values())
    return min((value for value, count in counts.items() if count == best), key=len)


def build_model(bundle: Dict) -> Dict:
    """Tabelas do projeto a partir do bundle do banco. Nunca levanta por dado ruim."""

    settings = bundle.get("settings") or {}
    project = bundle.get("project") or {}
    issues: List[Dict] = []

    individual_parts = []
    pooled_parts = []
    frames: Dict[str, Dict] = {}
    frame_stamps: Dict[str, np.ndarray] = {}
    participant_info: Dict[str, Dict] = {}
    store_labels: Dict[str, List[str]] = {}
    store_channels: Dict[str, str] = {}
    images = []

    for item in bundle.get("files") or []:
        meta = item.get("meta") or {}
        overrides = dict(meta.get("overrides") or {})
        if item.get("kind") and "kind" not in overrides:
            overrides["kind"] = item["kind"]
        parsed = parse_upload(item["filename"], item["content"], overrides)
        if not parsed.ok:
            for problem in parsed.issues:
                _issue(issues, problem["level"], "arquivo", problem["message"], item["filename"])
            continue
        if parsed.kind == "image":
            images.append(
                {
                    "file_id": item["id"],
                    "filename": item["filename"],
                    "caption": meta.get("caption") or parsed.meta.get("caption"),
                    "store": meta.get("store") or "",
                    "category": meta.get("category") or "",
                }
            )
            continue
        if parsed.kind == "gaze_frames":
            key = recording_key(
                parsed.meta.get("participant"), parsed.meta.get("task"), parsed.meta.get("store")
            )
            if key in frames:
                _issue(issues, "warn", "quadros_repetidos",
                       "Mais de um arquivo de quadros para a gravação; usado o mais recente.", key)
            frames[key] = dict(parsed.meta["frames"], file_id=item["id"],
                               store_label=parsed.meta.get("store_label", ""))
            frame_stamps[key] = frames_timestamps(item["content"])
            if parsed.meta.get("store"):
                store_labels.setdefault(parsed.meta["store"], []).append(parsed.meta.get("store_label", ""))
            continue
        if parsed.kind in ("bs_individual", "bs_enriched_xlsx", "legacy_tabelas"):
            table = parsed.table.copy()
            table["source_file_id"] = item["id"]
            individual_parts.append(table)
            for store, label in (parsed.meta.get("stores") or {}).items():
                store_labels.setdefault(store, []).append(label)
            for store, info in (parsed.meta.get("store_info") or {}).items():
                if info.get("channel"):
                    store_channels.setdefault(store, info["channel"])
            for info in parsed.meta.get("participant_info") or []:
                target = participant_info.setdefault(info["code"], {})
                for field in ("profile", "tempo_informado"):
                    if info.get(field) and not target.get(field):
                        target[field] = info[field]
            continue
        if parsed.kind == "bs_pooled":
            table = parsed.table.copy()
            table["source_file_id"] = item["id"]
            pooled_parts.append(table)
            if parsed.meta.get("store"):
                store_labels.setdefault(parsed.meta["store"], []).append(parsed.meta.get("store_label", ""))

    # ------------------------------------------------------------------
    # Lojas e participantes
    # ------------------------------------------------------------------
    configured_stores = settings.get("stores") or {}
    participants_db = {row["code"]: row for row in bundle.get("participants") or []}
    codes = set(participants_db) | set(participant_info)

    individual = pd.concat(individual_parts, ignore_index=True) if individual_parts else pd.DataFrame(
        columns=["participant", "task", "store", "store_label", "aoi", "source_file_id"] + list(METRIC_COLUMNS)
    )
    individual, repeated = _latest_rows(individual, ["participant", "task", "store", "aoi"])
    if repeated:
        _issue(issues, "info", "linhas_repetidas",
               "{} linha(s) apareceram em mais de um arquivo; valeu o arquivo mais recente.".format(repeated))
    codes |= set(individual["participant"].unique()) if not individual.empty else set()
    for key in frames:
        codes.add(key.split("|")[0])

    def _profile(code: str) -> str:
        row = participants_db.get(code) or {}
        return normalize_label(row.get("profile")) or normalize_label(
            (participant_info.get(code) or {}).get("profile")
        )

    def _store_label(store: str) -> str:
        configured = (configured_stores.get(store) or {}).get("label")
        return configured or _most_common(store_labels.get(store, [])) or store

    def _channel(store: str) -> str:
        return (configured_stores.get(store) or {}).get("channel") or store_channels.get(store, "")

    # ------------------------------------------------------------------
    # Gravacoes: unidade, conversao e status
    # ------------------------------------------------------------------
    overrides = {
        recording_key(row["participant_code"], row["task"], row["store"]): row
        for row in bundle.get("recordings") or []
    }
    hz_nominal = settings.get("hz_nominal")
    try:
        hz_nominal = float(hz_nominal) if hz_nominal not in (None, "") else math.nan
    except (TypeError, ValueError):
        hz_nominal = math.nan

    if not individual.empty:
        individual["recording_key"] = [
            recording_key(p, t, s)
            for p, t, s in zip(individual["participant"], individual["task"], individual["store"])
        ]
    groups = dict(tuple(individual.groupby("recording_key"))) if not individual.empty else {}
    pooled_all = pd.concat(pooled_parts, ignore_index=True) if pooled_parts else pd.DataFrame()
    # Celulas cobertas por agregado: loja vazia vale para todas as lojas.
    pooled_cells = (
        set(zip(pooled_all["task"], pooled_all["store"])) if not pooled_all.empty else set()
    )

    recording_rows = []
    gaze_parts = []
    for key in sorted(set(groups) | set(frames)):
        participant, task, store = key.split("|")
        rows = groups.get(key)
        frame_info = frames.get(key)
        override = overrides.get(key) or {}
        has_aoi = rows is not None and not rows.empty
        coded = bool(
            has_aoi
            and (
                (rows["TotalGazeDuration"].fillna(0) > 0).any()
                or (rows["GazeCount"].fillna(0) > 0).any()
                or rows["TimeToFirstFixation"].notna().any()
            )
        )
        unit, evidence = (None, "")
        if has_aoi and coded:
            unit, evidence = detect_unit(rows, frame_info)
            if evidence.startswith("ambigua"):
                _issue(issues, "warn", "unidade_ambigua",
                       "Unidade incerta ({}); confira ou fixe a unidade.".format(evidence), key)
        if override.get("unit_override"):
            unit, evidence = override["unit_override"], "definida manualmente"

        dt = math.nan
        conversion = ""
        if unit == "amostras":
            if frame_info and frame_info.get("dt_mean") == frame_info.get("dt_mean"):
                dt, conversion = float(frame_info["dt_mean"]), "quadros da gravação"
            elif hz_nominal == hz_nominal and hz_nominal > 0:
                dt, conversion = 1.0 / hz_nominal, "Hz nominal do projeto"
                _issue(issues, "info", "hz_nominal",
                       "Convertido pelo Hz nominal ({:g} Hz), sem arquivo de quadros.".format(hz_nominal), key)
            else:
                conversion = "sem conversão"
                _issue(issues, "warn", "sem_conversao",
                       "Export em amostras sem quadros nem Hz nominal: tempos ficam sem segundos.", key)
        elif unit == "segundos":
            conversion = "já em segundos"

        ratio = _median_ratio(rows) if has_aoi else math.nan
        duration = frame_info.get("duration_s") if frame_info else math.nan
        if (duration is None or duration != duration) and ratio == ratio:
            duration = ratio if unit == "segundos" else (ratio * dt if dt == dt else math.nan)

        if override.get("status") == "excluida":
            status, reason = "excluida", override.get("reason") or ""
        elif has_aoi and (coded or override.get("status") == "incluida"):
            status, reason = "incluida", "" if coded else "incluída manualmente (zeros)"
        elif has_aoi:
            status, reason = "nao_codificada", "todas as AOIs zeradas"
        elif (task, store) in pooled_cells or (task, "") in pooled_cells:
            status, reason = "agregado", "só há o agregado do grupo"
        else:
            status, reason = "sem_aoi", "gravação sem AOIs codificadas"

        recording_rows.append(
            {
                "recording_key": key,
                "participant": participant,
                "task": task,
                "task_label": TASK_LABELS.get(task, task or "—"),
                "store": store,
                "store_label": _store_label(store),
                "channel": _channel(store),
                "profile": _profile(participant),
                "has_frames": bool(frame_info),
                "n_frames": (frame_info or {}).get("n_frames", math.nan),
                "duration_s": duration,
                "hz": (frame_info or {}).get("hz", math.nan),
                "max_gap_s": (frame_info or {}).get("max_gap_s", math.nan),
                "loss_pct": (frame_info or {}).get("loss_pct", math.nan),
                "has_aoi_data": has_aoi,
                "coded": coded,
                "export_length": ratio,
                "unit": unit or "",
                "unit_evidence": evidence,
                "conversion": conversion,
                "status": status,
                "status_reason": reason,
                "override_status": override.get("status") or "auto",
                "frames_file_id": (frame_info or {}).get("file_id"),
            }
        )

        if not has_aoi:
            continue
        work = rows.copy()
        stamps = frame_stamps.get(key)
        t0 = float((frame_info or {}).get("t0") or 0.0)
        if unit == "amostras":
            factor = dt
            for source, target in (("TotalGazeDuration", "dwell_s"),
                                   ("AverageGazeDuration", "avg_visit_s"),
                                   ("MaximumGazeDuration", "max_visit_s")):
                work[target] = work[source] * factor if factor == factor else math.nan
            converted = [
                _convert_ttff(value, stamps, t0, dt) for value in work["TimeToFirstFixation"]
            ]
            work["ttff_s"] = [value for value, _ in converted]
            if any(not used for value, used in converted if value == value) and stamps is not None:
                _issue(issues, "warn", "ttff_fora_dos_quadros",
                       "TTFF além dos quadros da gravação; usado o intervalo médio.", key)
        else:
            work["dwell_s"] = work["TotalGazeDuration"]
            work["avg_visit_s"] = work["AverageGazeDuration"]
            work["max_visit_s"] = work["MaximumGazeDuration"]
            work["ttff_s"] = work["TimeToFirstFixation"]
        work["visits"] = work["GazeCount"].fillna(0)
        work["looked"] = (work["visits"] > 0) | (work["TotalGazeDuration"].fillna(0) > 0)
        work["share_of_recording"] = work["NormalizedGazeDuration"]
        work["dwell_raw"] = work["TotalGazeDuration"]
        work["ttff_raw"] = work["TimeToFirstFixation"]
        work["unit"] = unit or ""
        work["status"] = status
        gaze_parts.append(work)

    recordings = pd.DataFrame(recording_rows)

    # ------------------------------------------------------------------
    # Catalogo de AOIs
    # ------------------------------------------------------------------
    gaze = pd.concat(gaze_parts, ignore_index=True) if gaze_parts else pd.DataFrame()
    known_brands = [
        normalize_label(line) for line in str(project.get("marcas") or "").splitlines()
        if normalize_label(line)
    ]
    all_aois = list(gaze["aoi"].unique()) if not gaze.empty else []
    if not pooled_all.empty:
        all_aois += list(pooled_all["aoi"].unique())
    brands = suggest_brands(all_aois, known=known_brands)
    dimensions = settings.get("dimensions") or {}
    keys = []
    if not gaze.empty:
        keys += list(zip(gaze["store"], gaze["aoi"]))
    if not pooled_all.empty:
        keys += list(zip(pooled_all["store"], pooled_all["aoi"]))
    catalog = build_catalog(
        keys,
        brands=brands,
        dimensions=dimensions,
        overrides=bundle.get("aoi_overrides") or [],
        focus_brand=project.get("marca_foco") or "",
    )
    catalog_columns = [column for column in catalog.columns if column not in ("store", "aoi", "source")]

    if not gaze.empty:
        gaze["aoi_key"] = [aoi_key(s, a) for s, a in zip(gaze["store"], gaze["aoi"])]
        recordings_by_key = recordings.set_index("recording_key")
        for column in ("store_label", "channel", "profile"):
            gaze[column] = gaze["recording_key"].map(recordings_by_key[column])
        gaze = gaze.merge(catalog[catalog_columns], on="aoi_key", how="left")

    gaze_columns = [
        "recording_key", "participant", "task", "store", "store_label", "channel", "profile",
        "aoi", "aoi_key", "kind", "brand", "line", "product", "part", "element",
    ] + [c for c in catalog.columns if c.startswith("attr_")] + [
        "include", "is_focus", "shelf_weight", "dwell_s", "visits", "ttff_s", "looked",
        "avg_visit_s", "max_visit_s", "share_of_recording", "dwell_raw", "ttff_raw", "unit",
        "status", "source_file_id",
    ]
    gaze_raw = pd.DataFrame()
    if not gaze.empty:
        gaze_raw = gaze[["recording_key", "participant", "task", "store", "aoi", "unit",
                         "source_file_id"] + list(METRIC_COLUMNS)].copy()
        gaze = gaze[[column for column in gaze_columns if column in gaze.columns]]
    else:
        gaze = pd.DataFrame(columns=gaze_columns)

    # ------------------------------------------------------------------
    # Agregados por grupo (so onde nao ha dado individual da mesma celula)
    # ------------------------------------------------------------------
    pooled = _build_pooled(
        pooled_all, gaze, recordings, catalog, catalog_columns, settings, participants_db,
        participant_info, frames, hz_nominal, issues, _store_label,
    )

    # ------------------------------------------------------------------
    # Participantes, lojas e cobertura
    # ------------------------------------------------------------------
    participant_rows = []
    for code in sorted(codes):
        row = participants_db.get(code) or {}
        seeded = participant_info.get(code) or {}
        tempo = normalize_label(row.get("tempo_informado")) or normalize_label(seeded.get("tempo_informado"))
        participant_rows.append(
            {
                "participant": code,
                "profile": _profile(code),
                "tempo_informado": tempo,
                "tempo_decisao_s": parse_duration_text(tempo) if tempo else math.nan,
                "notes": normalize_label(row.get("notes")),
                "stores": ", ".join(sorted({
                    _store_label(r["store"]) for r in recording_rows
                    if r["participant"] == code and r["store"]
                })),
                "channel": ", ".join(sorted({
                    r["channel"] for r in recording_rows if r["participant"] == code and r["channel"]
                })),
            }
        )
    participants = pd.DataFrame(
        participant_rows,
        columns=["participant", "profile", "tempo_informado", "tempo_decisao_s", "notes", "stores", "channel"],
    )

    store_keys = sorted(set(store_labels) | set(configured_stores) | set(recordings.get("store", pd.Series(dtype=str))))
    stores = pd.DataFrame(
        [
            {
                "store": store,
                "label": _store_label(store),
                "channel": _channel(store),
                "tasks": ", ".join(sorted({
                    TASK_LABELS.get(r["task"], r["task"]) for r in recording_rows if r["store"] == store
                })),
            }
            for store in store_keys
            if store
        ],
        columns=["store", "label", "channel", "tasks"],
    )

    coverage = _coverage(recordings, participants, pooled)
    tasks_present = sorted(
        {t for t in recordings.get("task", pd.Series(dtype=str)) if t} | set(pooled.get("task", pd.Series(dtype=str)))
    )

    if not recordings.empty:
        uncoded = recordings[recordings["status"] == "nao_codificada"]
        for _, row in uncoded.iterrows():
            _issue(issues, "warn", "nao_codificada",
                   "{} · {} · {}: todas as AOIs zeradas; fora da análise como não codificada.".format(
                       row["participant"], row["task_label"], row["store_label"]),
                   row["recording_key"])

    return {
        "recordings": recordings,
        "gaze": gaze,
        "gaze_raw": gaze_raw,
        "pooled": pooled,
        "catalog": catalog,
        "participants": participants,
        "stores": stores,
        "coverage": coverage,
        "images": images,
        "issues": issues,
        "meta": {
            "brands": brands,
            "focus_brand": project.get("marca_foco") or "",
            "dimensions": dimensions,
            "tasks": tasks_present,
            "element_labels": settings.get("element_labels") or {},
            "examined_threshold_s": float(settings.get("examined_threshold_s") or 1.0),
            "data_version": project.get("data_version"),
        },
    }


def _build_pooled(
    pooled_all: pd.DataFrame,
    gaze: pd.DataFrame,
    recordings: pd.DataFrame,
    catalog: pd.DataFrame,
    catalog_columns: List[str],
    settings: Dict,
    participants_db: Dict,
    participant_info: Dict,
    frames: Dict,
    hz_nominal: float,
    issues: List[Dict],
    store_label,
) -> pd.DataFrame:
    columns = [
        "task", "store", "store_label", "group", "profile", "n_group", "n_group_source",
        "aoi", "aoi_key", "kind", "brand", "line", "product", "part", "element", "include",
        "is_focus", "is_outside", "dwell_sum_s", "visits_sum", "lookers", "ttff_mean_lookers_s",
        "share_of_pool_time", "max_visit_s", "unit", "used", "source_file_id",
    ]
    if pooled_all.empty:
        return pd.DataFrame(columns=columns)

    pooled, repeated = _latest_rows(pooled_all, ["task", "store", "group", "aoi"])
    if repeated:
        _issue(issues, "info", "agregado_repetido",
               "Agregado repetido em mais de um arquivo; valeu o mais recente.")
    groups_config = settings.get("groups") or {}

    def _profile_of(code: str) -> str:
        return normalize_label((participants_db.get(code) or {}).get("profile")) or normalize_label(
            (participant_info.get(code) or {}).get("profile")
        )

    individual_cells = set(zip(gaze["task"], gaze["store"])) if not gaze.empty else set()
    out = []
    for (task, store, group), rows in pooled.groupby(["task", "store", "group"], sort=True):
        config = groups_config.get(group) or {}
        profile = normalize_label(config.get("profile")) or ("Todos" if group == "TODOS" else group)
        # Membros: participantes do perfil com gravacao desta tarefa (e loja).
        members = []
        if not recordings.empty:
            candidates = recordings[recordings["task"] == task]
            if store:
                candidates = candidates[candidates["store"] == store]
            for code in candidates["participant"].unique():
                if group == "TODOS" or (config.get("profile") and fold(_profile_of(code)) == fold(config["profile"])):
                    members.append(code)
        max_lookers = rows["GazedAtBy"].max()
        if config.get("size"):
            n_group, source = int(config["size"]), "configurado"
        elif members:
            n_group, source = len(set(members)), "participantes"
        elif max_lookers == max_lookers and max_lookers > 0:
            n_group, source = int(max_lookers), "maior GazedAtBy"
        else:
            n_group, source = 0, "desconhecido"
        if n_group and max_lookers == max_lookers and max_lookers > n_group:
            _issue(issues, "warn", "grupo_menor_que_gazedatby",
                   "{} ({}): {} participantes olharam, mas o grupo tem {}.".format(
                       group, TASK_LABELS.get(task, task), int(max_lookers), n_group))

        ratio = _median_ratio(rows)
        unit = "amostras" if _looks_like_samples(rows, ratio, _POOLED_INTEGER_COLUMNS) else "segundos"
        dt = math.nan
        if unit == "amostras":
            member_dts = [
                frames[recording_key(code, task, store)]["dt_mean"]
                for code in set(members)
                if recording_key(code, task, store) in frames
            ]
            if member_dts:
                dt = float(np.mean(member_dts))
            elif hz_nominal == hz_nominal and hz_nominal > 0:
                dt = 1.0 / hz_nominal
        factor = dt if unit == "amostras" else 1.0

        used = (task, store) not in individual_cells
        for _, row in rows.iterrows():
            key = aoi_key(store, row["aoi"])
            out.append(
                {
                    "task": task,
                    "store": store,
                    "store_label": store_label(store) if store else "Todas as lojas",
                    "group": group,
                    "profile": profile,
                    "n_group": n_group,
                    "n_group_source": source,
                    "aoi": row["aoi"],
                    "aoi_key": key,
                    "is_outside": row["aoi"] == "",
                    "dwell_sum_s": row["TotalGazeDuration"] * factor if factor == factor else math.nan,
                    "visits_sum": row["GazeCount"],
                    "lookers": row["GazedAtBy"],
                    "ttff_mean_lookers_s": row["TimeToFirstFixation"] * factor if factor == factor else math.nan,
                    "share_of_pool_time": row["NormalizedGazeDuration"],
                    "max_visit_s": row["MaximumGazeDuration"] * factor if factor == factor else math.nan,
                    "unit": unit,
                    "used": used,
                    "source_file_id": row["source_file_id"],
                }
            )
        if not used and group == "TODOS" and not gaze.empty:
            _cross_check(rows, gaze, task, store, issues)

    frame = pd.DataFrame(out)
    if frame.empty:
        return pd.DataFrame(columns=columns)
    frame = frame.merge(catalog[catalog_columns], on="aoi_key", how="left")
    return frame[[column for column in columns if column in frame.columns]]


def _cross_check(rows: pd.DataFrame, gaze: pd.DataFrame, task: str, store: str, issues: List[Dict]) -> None:
    """Soma do individual contra o TODOS do mesmo export (so conferencia)."""

    cell = gaze[(gaze["task"] == task) & (gaze["store"] == store)]
    sums = cell.groupby("aoi")["dwell_raw"].sum()
    mismatched = []
    for _, row in rows.iterrows():
        if not row["aoi"] or row["aoi"] not in sums.index:
            continue
        expected = float(sums[row["aoi"]])
        found = float(row["TotalGazeDuration"])
        if expected and abs(found - expected) / expected > 0.01:
            mismatched.append(row["aoi"])
    if mismatched:
        _issue(issues, "warn", "agregado_nao_confere",
               "O agregado TODOS de {} não confere com a soma individual em {} AOI(s).".format(
                   TASK_LABELS.get(task, task), len(mismatched)),
               "{}|{}".format(task, store))


def _coverage(recordings: pd.DataFrame, participants: pd.DataFrame, pooled: pd.DataFrame) -> pd.DataFrame:
    """Participante × tarefa: o que existe de cada gravacao."""

    if recordings.empty:
        return pd.DataFrame(columns=["participant", "task", "status", "recording_key"])
    rows = []
    for participant in participants["participant"]:
        for task in sorted(recordings["task"].unique()):
            match = recordings[(recordings["participant"] == participant) & (recordings["task"] == task)]
            if match.empty:
                rows.append({"participant": participant, "task": task, "status": "ausente", "recording_key": ""})
                continue
            record = match.iloc[0]
            rows.append({"participant": participant, "task": task, "status": record["status"],
                         "recording_key": record["recording_key"]})
    return pd.DataFrame(rows)
