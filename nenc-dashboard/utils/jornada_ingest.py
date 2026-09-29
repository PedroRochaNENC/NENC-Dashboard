"""
Leitura dos arquivos de eye tracking enviados à Jornada de Compra.

Funções puras — bytes entram, tabela e metadados saem — para servirem a tela
de Uploads (prévia antes de gravar), o modelo (que relê os arquivos gravados)
e os testes. Nenhuma decide unidade: um export do Blickshift pode estar em
segundos ou em amostras, e isso só se resolve por gravação, com os quadros
dela à mão (`utils/jornada_model.py`).

Formatos reconhecidos:

- `gaze_frames`: olhar por quadro de uma gravação (`frame,timestamp,x,y`),
  com participante, tarefa e loja no nome (`Pt04-JEstimulada-ASSAI.csv`);
- `bs_individual`: Blickshift "Gaze Statistics" por participante × AOI;
- `bs_pooled`: o mesmo export agregado por grupo (`Participant` = "Participants"),
  com o grupo no nome do arquivo (`..._PERFIL 1.csv`, `..._TODOS.csv`);
- `bs_enriched_xlsx`: planilha com as colunas do Blickshift mais loja, canal,
  perfil e tempo até a decisão, escritas pela equipe;
- `interviews`: transcrições (`arquivo, ep, identificacao, texto`);
- `legacy_tabelas`: o `Banco_Tabelas` da versão antiga do módulo;
- `image`: foto de gôndola, heatmap ou embalagem.
"""

import io
import math
import re
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from utils.jornada_taxonomy import fold, normalize_label

# Colunas numericas do export "Gaze Statistics" do Blickshift.
METRIC_COLUMNS = (
    "TotalGazeDuration",
    "NormalizedGazeDuration",
    "AverageGazeDuration",
    "MaximumGazeDuration",
    "MinimumGazeDuration",
    "GazeCount",
    "TimeToFirstFixation",
    "GazedAtBy",
    "AOITransitionRate",
    "FixationCount",
    "SaccadeCount",
    "GazePointValidity",
    "AverageFixationDuration",
    "AverageSaccadeDuration",
    "AverageSaccadeLength",
    "FixationRate",
    "FixationSaccadeTimeRatio",
    "ScanPathLength",
    "ScanPathDuration",
    "ScanPathArea",
    "MeanPupilDilation",
)

KIND_LABELS = {
    "gaze_frames": "Olhar por quadro (gravação)",
    "bs_individual": "Blickshift por participante",
    "bs_pooled": "Blickshift agregado por grupo",
    "bs_enriched_xlsx": "Planilha enriquecida",
    "interviews": "Entrevistas",
    "legacy_tabelas": "Formato legado (Banco_Tabelas)",
    "image": "Imagem",
}

TASK_LABELS = {
    "livre": "Jornada Livre",
    "estimulada": "Jornada Estimulada",
    "embalagens": "Embalagens",
    "outra": "Outra tarefa",
}

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".webm", ".avi")
TABLE_EXTENSIONS = (".csv", ".tsv", ".txt", ".xlsx", ".xls")

# Participante: "Pt04", "PT4", "P04", "Participante 4".
_PARTICIPANT = re.compile(r"(?i)^p(?:t|art(?:icipante)?)?[\s_-]*0*(\d{1,4})$")
# Nome de gravação: "Pt04-JEstimulada-ASSAI", "Pt01-Emb-DSP2250-out".
_RECORDING = re.compile(
    r"(?i)^(?P<participant>p(?:t|art)?[\s_-]*\d{1,4})[\s_-]+(?P<task>[a-z]+)[\s_-]+"
    r"(?P<store>[a-z]*\d*[a-z]*)(?:[\s_-]+out)?$"
)
_GROUP = re.compile(r"(?i)(perfil\s*\d+|todos|geral)")
_DURATION = re.compile(r"(?i)^\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m(?:in)?)?\s*(?:(\d+(?:[.,]\d+)?)\s*s)?\s*$")


@dataclass
class ParsedFile:
    """Resultado da leitura de um arquivo, antes e depois de gravado."""

    filename: str
    kind: Optional[str]
    meta: Dict[str, object] = field(default_factory=dict)
    table: Optional[pd.DataFrame] = None
    issues: List[Dict[str, str]] = field(default_factory=list)

    def warn(self, message: str) -> None:
        self.issues.append({"level": "warn", "message": message})

    def error(self, message: str) -> None:
        self.issues.append({"level": "error", "message": message})

    @property
    def ok(self) -> bool:
        return self.kind is not None and not any(i["level"] == "error" for i in self.issues)


# ---------------------------------------------------------------------------
# Valores e chaves
# ---------------------------------------------------------------------------

def to_number(value: object) -> float:
    """Numero de uma celula exportada ou editada a mao.

    Aceita decimal com virgula ("0,0119"), porcentagem ("0,7%" -> 0.007),
    espacos e espaco duro (" 14,08 "), e os literais do Blickshift: "NaN" e
    "Infinity" viram NaN — infinito nao tem leitura nas metricas usadas.
    """

    if value is None:
        return math.nan
    if isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool):
        number = float(value)
        return number if math.isfinite(number) else math.nan
    text = str(value).replace(" ", " ").strip()
    if not text:
        return math.nan
    if fold(text) in ("nan", "infinity", "-infinity", "inf", "-inf", "none", "null", "-"):
        return math.nan
    percent = text.endswith("%")
    if percent:
        text = text[:-1].strip()
    text = text.replace(" ", "")
    if "," in text and "." in text:
        # "1.234,56": ponto de milhar, virgula decimal.
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return math.nan
    if not math.isfinite(number):
        return math.nan
    return number / 100.0 if percent else number


def normalize_participant(value: object) -> str:
    """"PT4", "Pt04" e "P04" viram "Pt04"; o resto so e limpo."""

    text = normalize_label(value)
    match = _PARTICIPANT.match(text)
    if match:
        return "Pt{:02d}".format(int(match.group(1)))
    return text


def task_key(value: object) -> Optional[str]:
    """Tarefa pelo nome: livre, estimulada, embalagens — ou None."""

    folded = fold(value)
    if not folded:
        return None
    if "embal" in folded or re.search(r"(?<![a-z])emb(?![a-z])", folded):
        return "embalagens"
    if "estimul" in folded:
        return "estimulada"
    if "livre" in folded:
        return "livre"
    return None


def store_key(value: object) -> str:
    """Chave estavel da loja.

    Loja numerada fica so com os digitos: "DSP2250", "DGSP2250", "DG2250" e
    "DSP-2250" sao a mesma loja nos arquivos reais. Sem numero, o nome dobrado
    ("ASSAI", "Assaí" -> "assai").
    """

    folded = fold(value)
    digits = re.findall(r"\d{3,}", folded)
    if digits:
        return digits[0]
    return re.sub(r"[^a-z0-9]+", "", folded)


def scenario_parts(value: object) -> Dict[str, str]:
    """"Jornadas Estimuladas-Assai" -> tarefa estimulada, loja assai."""

    text = normalize_label(value)
    result = {"task": task_key(text) or "", "store": "", "store_label": ""}
    if "-" in text:
        _, tail = text.rsplit("-", 1)
        tail = normalize_label(tail)
        if tail and not task_key(tail):
            result["store"] = store_key(tail)
            result["store_label"] = tail
    return result


def parse_recording_filename(filename: str) -> Optional[Dict[str, str]]:
    """Participante, tarefa e loja de "Pt04-JEstimulada-ASSAI.csv"."""

    stem = PurePath(str(filename)).stem
    match = _RECORDING.match(stem.strip())
    if not match:
        return None
    task = task_key(match.group("task"))
    store_label = match.group("store")
    return {
        "participant": normalize_participant(match.group("participant")),
        "task": task or "",
        "task_token": match.group("task"),
        "store": store_key(store_label),
        "store_label": store_label,
    }


def parse_duration_text(value: object) -> float:
    """"1m21s" -> 81; "17s" -> 17; "1:21" -> 81; numero puro = segundos."""

    text = fold(value).replace(" ", "")
    if not text:
        return math.nan
    if re.fullmatch(r"\d+:\d{1,2}(?::\d{1,2})?", text):
        parts = [int(part) for part in text.split(":")]
        seconds = 0
        for part in parts:
            seconds = seconds * 60 + part
        return float(seconds)
    number = to_number(text)
    if not math.isnan(number):
        return number
    match = _DURATION.match(text)
    if not match or not any(match.groups()):
        return math.nan
    hours, minutes, seconds = match.groups()
    total = 0.0
    if hours:
        total += int(hours) * 3600
    if minutes:
        total += int(minutes) * 60
    if seconds:
        total += float(seconds.replace(",", "."))
    return total


def _file_store_token(filename: str) -> str:
    """Primeiro pedaco do nome que parece loja: "DGSP2250-INDIVIDUAL2.csv" -> "DGSP2250"."""

    stem = PurePath(str(filename)).stem
    token = re.split(r"[_\-\s]", stem, maxsplit=1)[0]
    token = normalize_label(token)
    if not token or task_key(token) or fold(token) in ("individual", "gaze"):
        return ""
    return token


def _group_label(filename: str) -> str:
    match = _GROUP.search(PurePath(str(filename)).stem)
    if not match:
        return ""
    label = normalize_label(match.group(1)).upper()
    number = re.search(r"\d+", label)
    if number:
        return "PERFIL {}".format(int(number.group(0)))
    return "TODOS"


# ---------------------------------------------------------------------------
# Leitura de tabela
# ---------------------------------------------------------------------------

def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def read_delimited(content: bytes) -> pd.DataFrame:
    """CSV/TSV como texto puro, com o separador descoberto no cabeçalho."""

    text = _decode(content)
    header = text.splitlines()[0] if text else ""
    separator = max((";", "\t", ","), key=header.count)
    frame = pd.read_csv(
        io.StringIO(text), sep=separator, dtype=str, keep_default_na=False, engine="python"
    )
    frame.columns = [normalize_label(column) for column in frame.columns]
    return frame


def read_table(filename: str, content: bytes) -> pd.DataFrame:
    suffix = PurePath(str(filename)).suffix.lower()
    if suffix in (".xlsx", ".xls"):
        frame = pd.read_excel(io.BytesIO(content), dtype=str)
        frame = frame.fillna("")
        frame.columns = [normalize_label(column) for column in frame.columns]
        return frame
    return read_delimited(content)


def _numeric(frame: pd.DataFrame, columns) -> pd.DataFrame:
    for column in columns:
        if column in frame.columns:
            frame[column] = frame[column].map(to_number).astype(float)
    return frame


def detect_kind(filename: str, content: bytes) -> Optional[str]:
    """Tipo do arquivo pelo nome e pelo cabeçalho; None se nao reconhecer."""

    suffix = PurePath(str(filename)).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix not in TABLE_EXTENSIONS:
        return None
    try:
        if suffix in (".xlsx", ".xls"):
            columns = list(pd.read_excel(io.BytesIO(content), nrows=5).columns)
        else:
            columns = list(read_delimited(content[:20000]).columns)
    except Exception:
        return None
    folded = {fold(column) for column in columns}
    if {"frame", "timestamp"} <= folded:
        return "gaze_frames"
    if "aoi" in folded and "totalgazeduration" in folded:
        if suffix in (".xlsx", ".xls"):
            return "bs_enriched_xlsx"
        return "bs_individual_or_pooled"
    if "nome da origem" in folded:
        return "legacy_tabelas"
    if {"texto"} <= folded and ({"identificacao"} & folded or {"arquivo"} & folded):
        return "interviews"
    return None


# ---------------------------------------------------------------------------
# Parsers por tipo
# ---------------------------------------------------------------------------

def frames_summary(frame: pd.DataFrame, gap_threshold_s: float = 0.25) -> Dict[str, float]:
    """Duracao, taxa e perdas de uma gravacao a partir dos quadros."""

    timestamps = pd.to_numeric(frame["timestamp"], errors="coerce").dropna().to_numpy()
    n = int(len(timestamps))
    if n < 2:
        return {"n_frames": n, "duration_s": math.nan, "dt_mean": math.nan, "hz": math.nan,
                "max_gap_s": math.nan, "loss_pct": math.nan, "t0": math.nan}
    t0 = float(timestamps[0])
    duration = float(timestamps[-1] - t0)
    deltas = np.diff(timestamps)
    positive = deltas[deltas > 0]
    median_dt = float(np.median(positive)) if positive.size else math.nan
    long_gaps = deltas[deltas > gap_threshold_s]
    lost = float(np.sum(long_gaps - median_dt)) if long_gaps.size and median_dt == median_dt else 0.0
    return {
        "n_frames": n,
        "t0": t0,
        "duration_s": duration,
        "dt_mean": duration / (n - 1) if n > 1 else math.nan,
        "hz": 1.0 / median_dt if median_dt and median_dt == median_dt else math.nan,
        "max_gap_s": float(deltas.max()) if deltas.size else math.nan,
        "loss_pct": 100.0 * lost / duration if duration > 0 else math.nan,
    }


def frames_timestamps(content: bytes) -> np.ndarray:
    """Timestamp de cada numero de quadro (indice = frame), NaN onde faltar."""

    frame = read_delimited(content)
    columns = {fold(column): column for column in frame.columns}
    frames = pd.to_numeric(frame[columns["frame"]], errors="coerce")
    stamps = pd.to_numeric(frame[columns["timestamp"]], errors="coerce")
    valid = frames.notna() & stamps.notna() & (frames >= 0)
    if not valid.any():
        return np.array([], dtype=float)
    size = int(frames[valid].max()) + 1
    result = np.full(size, np.nan)
    result[frames[valid].astype(int).to_numpy()] = stamps[valid].to_numpy()
    return result


def _parse_frames(parsed: ParsedFile, content: bytes, overrides: Dict) -> None:
    frame = read_delimited(content)
    columns = {fold(column): column for column in frame.columns}
    frame = frame.rename(columns={columns[key]: key for key in ("frame", "timestamp", "x", "y") if key in columns})
    summary = frames_summary(frame)
    key = parse_recording_filename(parsed.filename) or {}
    participant = overrides.get("participant") or key.get("participant", "")
    task = overrides.get("task") or key.get("task", "")
    store = overrides.get("store") if overrides.get("store") is not None else key.get("store", "")
    parsed.meta.update(
        participant=participant,
        task=task,
        store=store,
        store_label=overrides.get("store_label") or key.get("store_label", ""),
        frames=summary,
        n_rows=summary["n_frames"],
    )
    needs = [name for name, value in (("participant", participant), ("task", task)) if not value]
    if needs:
        parsed.meta["needs"] = needs
        parsed.warn(
            "Não reconheci {} no nome do arquivo; informe na prévia.".format(
                " e ".join({"participant": "o participante", "task": "a tarefa"}[n] for n in needs)
            )
        )
    if summary["n_frames"] < 2:
        parsed.error("Arquivo de quadros sem linhas suficientes.")


def _individual_table(frame: pd.DataFrame, parsed: ParsedFile, overrides: Dict) -> pd.DataFrame:
    """Normaliza participante, tarefa, loja e AOI de um export por participante."""

    columns = {fold(column): column for column in frame.columns}
    file_store = _file_store_token(parsed.filename)
    rows = pd.DataFrame(index=frame.index)
    rows["aoi"] = frame[columns["aoi"]].map(normalize_label)
    rows["participant"] = (
        frame[columns["participant"]].map(normalize_participant)
        if "participant" in columns
        else ""
    )
    scenario = frame[columns["scenario"]] if "scenario" in columns else pd.Series("", index=frame.index)
    parts = scenario.map(scenario_parts)
    rows["scenario"] = scenario.map(normalize_label)
    rows["task"] = parts.map(lambda part: part["task"])
    rows["store"] = parts.map(lambda part: part["store"])
    rows["store_label"] = parts.map(lambda part: part["store_label"])
    if "loja" in columns:
        rows["store_label"] = frame[columns["loja"]].map(normalize_label)
        rows["store"] = rows["store_label"].map(store_key)
    if file_store:
        missing = rows["store"] == ""
        rows.loc[missing, "store"] = store_key(file_store)
        rows.loc[missing, "store_label"] = file_store
    file_task = task_key(PurePath(parsed.filename).stem)
    if file_task:
        rows.loc[rows["task"] == "", "task"] = file_task
    if overrides.get("task"):
        rows["task"] = overrides["task"]
    if overrides.get("store") is not None:
        rows["store"] = overrides["store"]
        rows["store_label"] = overrides.get("store_label") or overrides["store"]
    for column in METRIC_COLUMNS:
        source = columns.get(fold(column))
        rows[column] = frame[source].map(to_number).astype(float) if source else math.nan
    return rows


def _parse_individual(parsed: ParsedFile, frame: pd.DataFrame, overrides: Dict) -> None:
    table = _individual_table(frame, parsed, overrides)
    table = table[table["participant"] != ""].reset_index(drop=True)
    parsed.table = table
    stores = {
        row["store"]: row["store_label"]
        for _, row in table[["store", "store_label"]].drop_duplicates().iterrows()
        if row["store"]
    }
    parsed.meta.update(
        participants=sorted(table["participant"].unique().tolist()),
        tasks=sorted(t for t in table["task"].unique().tolist() if t),
        stores=stores,
        n_aois=int(table["aoi"].nunique()),
        n_rows=int(len(table)),
    )
    needs = []
    if (table["task"] == "").any():
        needs.append("task")
    if (table["store"] == "").any():
        needs.append("store")
    if needs:
        parsed.meta["needs"] = needs
        parsed.warn("Informe na prévia: {}.".format(", ".join({"task": "tarefa", "store": "loja"}[n] for n in needs)))


def _parse_pooled(parsed: ParsedFile, frame: pd.DataFrame, overrides: Dict) -> None:
    columns = {fold(column): column for column in frame.columns}
    file_store = _file_store_token(parsed.filename)
    scenario = ""
    if "scenario" in columns:
        values = [normalize_label(v) for v in frame[columns["scenario"]].unique() if normalize_label(v)]
        scenario = values[0] if values else ""
    parts = scenario_parts(scenario)
    task = overrides.get("task") or parts["task"] or task_key(PurePath(parsed.filename).stem) or ""
    if overrides.get("store") is not None:
        store, store_label = overrides["store"], overrides.get("store_label") or overrides["store"]
    elif parts["store"]:
        store, store_label = parts["store"], parts["store_label"]
    elif file_store:
        store, store_label = store_key(file_store), file_store
    else:
        store, store_label = "", ""
    group = overrides.get("group") or _group_label(parsed.filename)

    table = pd.DataFrame(index=frame.index)
    table["aoi"] = frame[columns["aoi"]].map(normalize_label)
    for column in METRIC_COLUMNS:
        source = columns.get(fold(column))
        table[column] = frame[source].map(to_number).astype(float) if source else math.nan
    table["task"] = task
    table["store"] = store
    table["store_label"] = store_label
    table["group"] = group
    parsed.table = table.reset_index(drop=True)
    parsed.meta.update(
        task=task,
        store=store,
        store_label=store_label,
        group=group,
        scenario=scenario,
        n_aois=int((table["aoi"] != "").sum()),
        n_rows=int(len(table)),
        outside_row=bool((table["aoi"] == "").any()),
    )
    needs = [name for name, value in (("task", task), ("group", group)) if not value]
    if needs:
        parsed.meta["needs"] = needs
        parsed.warn("Informe na prévia: {}.".format(", ".join({"task": "tarefa", "group": "grupo"}[n] for n in needs)))


def _parse_enriched(parsed: ParsedFile, content: bytes, overrides: Dict) -> None:
    workbook = pd.ExcelFile(io.BytesIO(content))
    frame = None
    for sheet in workbook.sheet_names:
        candidate = workbook.parse(sheet, dtype=str).fillna("")
        candidate.columns = [normalize_label(column) for column in candidate.columns]
        folded = {fold(column) for column in candidate.columns}
        if "aoi" in folded and "totalgazeduration" in folded:
            frame = candidate
            parsed.meta["sheet"] = sheet
            break
    if frame is None:
        parsed.error("Nenhuma aba com as colunas AOI e TotalGazeDuration.")
        return
    columns = {fold(column): column for column in frame.columns}
    table = _individual_table(frame, parsed, overrides)
    table = table[table["participant"] != ""]
    parsed.table = table.reset_index(drop=True)

    participants: Dict[str, Dict[str, str]] = {}
    stores: Dict[str, Dict[str, str]] = {}
    for index, row in frame.iterrows():
        code = normalize_participant(row.get(columns.get("participant", ""), ""))
        if not code:
            continue
        info = participants.setdefault(code, {"code": code})
        if "perfil" in columns and normalize_label(row[columns["perfil"]]):
            info.setdefault("profile", normalize_label(row[columns["perfil"]]))
        if "tempo" in columns and normalize_label(row[columns["tempo"]]):
            info.setdefault("tempo_informado", normalize_label(row[columns["tempo"]]))
        store = table.loc[index, "store"] if index in table.index else ""
        if store:
            entry = stores.setdefault(store, {"label": table.loc[index, "store_label"]})
            if "canal" in columns and normalize_label(row[columns["canal"]]):
                entry.setdefault("channel", normalize_label(row[columns["canal"]]))
    parsed.meta.update(
        participants=sorted(participants),
        participant_info=list(participants.values()),
        stores={key: value["label"] for key, value in stores.items()},
        store_info=stores,
        tasks=sorted(t for t in table["task"].unique().tolist() if t),
        n_aois=int(table["aoi"].nunique()),
        n_rows=int(len(table)),
    )


def _parse_interviews(parsed: ParsedFile, frame: pd.DataFrame) -> None:
    columns = {fold(column): column for column in frame.columns}
    title_column = columns.get("identificacao") or columns.get("arquivo")
    table = pd.DataFrame(
        {
            "titulo": frame[title_column].map(normalize_label) if title_column else "",
            "participante_id": frame[columns["ep"]].map(normalize_label) if "ep" in columns else "",
            "texto": frame[columns["texto"]].map(lambda v: str(v or "").strip()),
        }
    )
    table = table[table["texto"] != ""].reset_index(drop=True)
    parsed.table = table
    parsed.meta.update(n_rows=int(len(table)))


def _parse_legacy(parsed: ParsedFile, frame: pd.DataFrame, overrides: Dict) -> None:
    """Banco_Tabelas: "Nome da Origem" e o participante; tarefa e loja vem da previa."""

    columns = {fold(column): column for column in frame.columns}
    renamed = frame.rename(columns={columns["nome da origem"]: "Participant"})
    renamed["Participant"] = renamed["Participant"].map(
        lambda value: PurePath(normalize_label(value)).stem
    )
    _parse_individual(parsed, renamed, overrides)


def parse_upload(filename: str, content: bytes, overrides: Optional[Dict] = None) -> ParsedFile:
    """Le um arquivo enviado. `overrides` e o que a previa corrigiu (tarefa, loja, grupo)."""

    overrides = dict(overrides or {})
    parsed = ParsedFile(filename=str(filename), kind=None)
    if PurePath(str(filename)).name.startswith("~$"):
        # Trava que o Excel cria enquanto a planilha esta aberta.
        parsed.error("Arquivo temporário do Excel (~$): feche a planilha e envie o original.")
        return parsed
    if not content:
        parsed.error("Arquivo vazio.")
        return parsed
    kind = overrides.get("kind") or detect_kind(filename, content)
    try:
        if kind == "image":
            parsed.kind = "image"
            parsed.meta.update(
                caption=overrides.get("caption") or PurePath(str(filename)).stem,
                store=overrides.get("store", ""),
                category=overrides.get("category", ""),
            )
            return parsed
        if kind == "video":
            parsed.kind = None
            parsed.error("Vídeos são enviados na seção de vídeos, não aqui.")
            return parsed
        if kind is None:
            parsed.error("Formato não reconhecido.")
            return parsed
        if kind == "gaze_frames":
            parsed.kind = kind
            _parse_frames(parsed, content, overrides)
            return parsed
        if kind == "bs_enriched_xlsx":
            parsed.kind = kind
            _parse_enriched(parsed, content, overrides)
            return parsed

        frame = read_table(filename, content)
        folded = {fold(column) for column in frame.columns}
        if kind in ("bs_individual_or_pooled", "bs_individual", "bs_pooled"):
            participants = (
                {normalize_label(v) for v in frame[[c for c in frame.columns if fold(c) == "participant"][0]]}
                if "participant" in folded
                else set()
            )
            pooled = kind == "bs_pooled" or not participants or participants <= {"Participants", ""}
            if kind == "bs_individual":
                pooled = False
            parsed.kind = "bs_pooled" if pooled else "bs_individual"
            if pooled:
                _parse_pooled(parsed, frame, overrides)
            else:
                _parse_individual(parsed, frame, overrides)
            return parsed
        if kind == "interviews":
            parsed.kind = kind
            _parse_interviews(parsed, frame)
            return parsed
        if kind == "legacy_tabelas":
            parsed.kind = kind
            _parse_legacy(parsed, frame, overrides)
            return parsed
        parsed.error("Formato não reconhecido.")
    except Exception as error:  # arquivo corrompido ou layout inesperado
        parsed.kind = None
        parsed.error("Não consegui ler o arquivo: {}".format(error))
    return parsed
