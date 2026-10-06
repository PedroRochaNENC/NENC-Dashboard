"""
Leitura dos arquivos do Teste Sensorial.

Funções puras: bytes (ou um arquivo no disco) entram, tabela normalizada e
metadados saem. Servem a revisão das importações (prévia antes de gravar), a
gravação (a tabela vira Parquet em `utils/sensorial_store.py`) e os testes.

Papéis reconhecidos (`ROLES`): as saídas do pipeline por modalidade (EEG,
periféricos, teste de associação), o inventário das sessões, o manifesto de
cada rodada, o registro de campo da qualidade do sinal, as chaves da BASE
LIMPA, a planilha de perfil, imagens e documentos.

Privacidade. As saídas do pipeline trazem o nome do participante em
`participante`, `participante_original`, `filename` e `Participant Name`. Aqui
o nome vira código (`P07`, do número que o pipeline põe na frente do nome) e
essas colunas saem da tabela. Texto livre que fica (avisos, observações)
passa por uma troca das palavras do nome por "[participante]". O manifesto é
lido por lista de chaves permitidas: a lista de arquivos de entrada, o
usuário e o computador nunca entram.
"""

import contextlib
import gzip
import io
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Set, Union

import pandas as pd

from utils import xlsx_keys
from utils.jornada_taxonomy import fold, normalize_label

# Muda quando a normalização muda: a tabela gravada leva a versão no nome e
# pode ser refeita a partir do original.
PARSER_VERSION = 1

CODE_FORMAT = "P{:02d}"

ROLES: Dict[str, Dict[str, object]] = {
    "eeg_indicadores": {"label": "EEG: indicadores por janela", "layer": "eeg", "kind": "table", "single": True},
    "eeg_psd": {"label": "EEG: potência por canal e banda, por janela", "layer": "eeg", "kind": "table",
                "single": True},
    "eeg_psd_medio": {"label": "EEG: potência média por etapa", "layer": "eeg", "kind": "table", "single": True},
    "eeg_qualidade": {"label": "EEG: qualidade por sessão", "layer": "eeg", "kind": "table", "single": True},
    "eeg_topomapa": {"label": "EEG: topomapa", "layer": "eeg", "kind": "image", "single": False},
    "perifericos_metricas": {"label": "Periféricos: métricas por janela", "layer": "perifericos",
                             "kind": "table", "single": True},
    "perifericos_qualidade": {"label": "Periféricos: qualidade por sessão", "layer": "perifericos",
                              "kind": "table", "single": True},
    "associacao_tentativas": {"label": "Teste de associação: tentativas", "layer": "associacao",
                              "kind": "table", "single": True},
    "inventario": {"label": "Inventário das sessões", "layer": "geral", "kind": "table", "single": True},
    "manifesto": {"label": "Manifesto da rodada do pipeline", "layer": "geral", "kind": "json", "single": False},
    "campo_qualidade": {"label": "Registro de campo: qualidade do sinal por canal", "layer": "eeg",
                        "kind": "table", "single": True},
    "base_limpa_eeg": {"label": "BASE LIMPA do EEG: janelas mantidas", "layer": "eeg", "kind": "table",
                       "single": True},
    "base_limpa_perifericos": {"label": "BASE LIMPA dos periféricos: janelas mantidas", "layer": "perifericos",
                               "kind": "table", "single": True},
    "base_limpa_associacao": {"label": "BASE LIMPA do teste de associação: tentativas mantidas",
                              "layer": "associacao", "kind": "table", "single": True},
    "perfil": {"label": "Perfil dos participantes", "layer": "geral", "kind": "table", "single": True},
    "estimulo": {"label": "Estímulo", "layer": "geral", "kind": "file", "single": False},
    "documento": {"label": "Documento do projeto", "layer": "geral", "kind": "file", "single": False},
    "literatura": {"label": "Literatura", "layer": "geral", "kind": "file", "single": False},
}

# Tabelas por janela: dezenas de milhares de linhas; os valores vão em float32.
WINDOW_ROLES = frozenset(("eeg_indicadores", "eeg_psd", "perifericos_metricas"))
PIPELINE_ROLES = frozenset((
    "eeg_indicadores", "eeg_psd", "eeg_psd_medio", "eeg_qualidade",
    "perifericos_metricas", "perifericos_qualidade", "associacao_tentativas",
))

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
DOCUMENT_EXTENSIONS = (".pdf", ".docx", ".pptx", ".txt", ".md")
_EXTENSIONS = {
    "table": (".csv", ".gz", ".xlsx", ".xls"),
    "json": (".json",),
    "image": IMAGE_EXTENSIONS,
    "file": IMAGE_EXTENSIONS + DOCUMENT_EXTENSIONS,
}
_ROLE_EXTENSIONS = {
    "inventario": (".xlsx",),
    "campo_qualidade": (".xlsx", ".xls", ".csv"),
}

# Cada camada tem a sua BASE LIMPA no consolidado do SPSS; dela só vêm as chaves
# do que ficou (janelas do EEG e dos periféricos, tentativas do teste).
BASE_LIMPA_SHEET = "BASE LIMPA"
_WINDOW_KEYS = ("sessao_id", "Etapa", "Bloco", "Tempo")
BASE_LIMPA_KEYS = {
    "base_limpa_eeg": _WINDOW_KEYS,
    "base_limpa_perifericos": _WINDOW_KEYS,
    "base_limpa_associacao": ("sessao_id", "Trial Number"),
}
BASE_LIMPA_ROLES = tuple(BASE_LIMPA_KEYS)

_NA_VALUES = ["#NULO!", "#NULL!", "#N/D", "#DIV/0!", "#VALOR!", "#VALUE!"]
_RUN = re.compile(r"^run_\d{8}-\d{6}$")
_NUMBER = re.compile(r"\d{1,4}")
_NAME_IN_FILENAME = re.compile(r"(?:^|_)\d{1,4}-([^_]+)")
_NAME_WORD = re.compile(r"[^\W\d_]{3,}")
_TOKEN_STOPWORDS = frozenset(("dos", "das", "del", "von", "van", "sem", "com", "por", "para"))

# Colunas com o nome do participante, de onde também sai o código.
_CODE_SOURCES = ("participante", "participante_original", "Participant Name")
# Colunas que nunca ficam numa tabela (comparadas sem acento e sem caixa).
_PERSONAL_EXACT = frozenset(fold(c) for c in (
    "filename", "arquivo", "arquivos", "participante", "participante_original", "participant name",
    "nome", "name", "computador", "usuario", "origens", "sugestao", "aviso_nome", "entrada",
    "id participante", "id\nparticipante",
))
_PERSONAL_PREFIXES = ("nome", "e-mail", "email", "telefone", "celular", "whatsapp", "cpf", "rg ",
                      "endereco", "instagram", "data de nascimento", "nascimento", "contato")
_FILENAME_COLUMNS = frozenset(("filename", "arquivo", "arquivos", "origens"))

# Texto que descreve a estrutura do estudo e nunca é convertido em número.
_TEXT_COLUMNS = frozenset((
    "sessao_id", "participant_code", "amostra", "experimento", "data", "hora", "avisos_sessao", "dispositivo",
    "Codigo", "Etapa", "Etapa_variante", "Etapa_original", "GSR_unidade", "basal_etapa", "qualidade_fc",
    "qualidade_gsr", "unidade", "filtro", "ica", "ica_componentes_removidos", "observacao", "sanidade_detalhe",
    "coluna_fc", "motivo", "palavra", "lado_sim", "botao", "resposta", "amostra_teste",
))
_UNSCRUBBED = frozenset(("sessao_id", "participant_code"))
# Tempo e posição na sessão ficam em float64 mesmo nas tabelas por janela.
_FLOAT64_COLUMNS = frozenset(("Tempo", "primeira_olfacao_s", "t_s", "t_na_etapa_s", "duracao_s"))

_ASSOCIATION_COLUMNS = {
    "Amostra": "amostra_teste",
    "Trial Number": "tentativa",
    "Word": "palavra",
    "Yes Side": "lado_sim",
    "Triggered Button": "botao",
    "R T": "rt",
    "Trial Response": "resposta",
}

_DETECT = (
    (re.compile(r"^indicadores\.(csv|xlsx)$"), "eeg_indicadores"),
    (re.compile(r"^psd_results\.(csv|xlsx)$"), "eeg_psd"),
    (re.compile(r"^psd_mean_results\.(csv|xlsx)$"), "eeg_psd_medio"),
    (re.compile(r"^data_quality_assessment\.(csv|xlsx)$"), "eeg_qualidade"),
    (re.compile(r"^topomap_.+\.(png|jpe?g|webp)$"), "eeg_topomapa"),
    (re.compile(r"^perifericos_metrics\.(csv|xlsx)$"), "perifericos_metricas"),
    (re.compile(r"^perifericos_dqa\.(csv|xlsx)$"), "perifericos_qualidade"),
    (re.compile(r"^iat_consolidado\.(csv|xlsx)$"), "associacao_tentativas"),
    (re.compile(r"^inventario_.*\.xlsx$"), "inventario"),
    (re.compile(r"^manifest\.json$"), "manifesto"),
    (re.compile(r"base_limpa_eeg\.csv$"), "base_limpa_eeg"),
    (re.compile(r"base_limpa_perifericos\.csv$"), "base_limpa_perifericos"),
    (re.compile(r"base_limpa_associacao\.csv$"), "base_limpa_associacao"),
)

Source = Union[bytes, bytearray, memoryview, str, Path]


@dataclass
class ParsedFile:
    """Resultado da leitura de um arquivo, antes de gravado."""

    filename: str
    role: Optional[str]
    meta: Dict[str, object] = field(default_factory=dict)
    table: Optional[pd.DataFrame] = None
    issues: List[Dict[str, str]] = field(default_factory=list)

    def warn(self, message: str) -> None:
        self.issues.append({"level": "warn", "message": message})

    def error(self, message: str) -> None:
        self.issues.append({"level": "error", "message": message})

    @property
    def ok(self) -> bool:
        return self.role is not None and not any(i["level"] == "error" for i in self.issues)


# ---------------------------------------------------------------------------
# Papel, código e nomes
# ---------------------------------------------------------------------------

def _stem(filename: str) -> str:
    name = fold(PurePath(str(filename)).name)
    return name[:-3] if name.endswith(".gz") else name


def detect_role(filename: str) -> Optional[str]:
    """Papel pelo nome do arquivo, para as saídas do pipeline. O resto é escolhido por quem envia."""
    name = _stem(filename)
    for pattern, role in _DETECT:
        if pattern.search(name):
            return role
    return None


def run_id_from_path(rel_path: Optional[str]) -> Optional[str]:
    """`run_20260915-171636` da pasta da rodada do pipeline, se o caminho tiver uma."""
    for part in reversed(PurePath(str(rel_path or "")).parts[:-1]):
        if _RUN.match(part):
            return part
    return None


def _missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return value is pd.NA or value is pd.NaT


def participant_code(value: object) -> Optional[str]:
    """`07-Nome`, `x07-Nome`, `Participante 7` e `P7` viram `P07`; sem número, None."""
    if _missing(value):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return CODE_FORMAT.format(int(value)) if float(value).is_integer() and value >= 0 else None
    match = _NUMBER.search(normalize_label(value))
    return CODE_FORMAT.format(int(match.group())) if match else None


def name_tokens(values: Iterable[object], *, from_filename: bool = False) -> Set[str]:
    """Palavras de nome de pessoa, para apagar de texto livre."""
    tokens: Set[str] = set()
    for value in values:
        if _missing(value):
            continue
        text = str(value)
        pieces = _NAME_IN_FILENAME.findall(text) if from_filename else [re.sub(r"^\D*\d+\s*[-_]?\s*", "", text)]
        for piece in pieces:
            for word in _NAME_WORD.findall(piece):
                lowered = word.lower()
                if lowered in _TOKEN_STOPWORDS:
                    continue
                tokens.add(lowered)
                tokens.add(fold(lowered))
    return tokens


def scrubber(tokens: Iterable[str]) -> Callable[[str], str]:
    """Função que troca as palavras do nome por "[participante]" num texto."""
    ordered = sorted({t for t in tokens if t}, key=len, reverse=True)
    if not ordered:
        return lambda text: text
    pattern = re.compile(r"(?i)\b(?:" + "|".join(re.escape(t) for t in ordered) + r")\b")
    return lambda text: pattern.sub("[participante]", text)


def is_personal_column(column: object) -> bool:
    key = fold(column)
    if key in _PERSONAL_EXACT:
        return True
    return any(key.startswith(prefix) for prefix in _PERSONAL_PREFIXES)


def _snake(column: object) -> str:
    return re.sub(r"[^0-9a-z]+", "_", fold(column)).strip("_")


# ---------------------------------------------------------------------------
# Leitura de tabela
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _binary(source: Source) -> Iterator[io.BufferedIOBase]:
    """O conteúdo em bytes, descomprimido se for gzip (pelo cabeçalho, não pelo nome)."""
    with contextlib.ExitStack() as stack:
        if isinstance(source, (bytes, bytearray, memoryview)):
            handle = io.BytesIO(bytes(source))
        else:
            handle = stack.enter_context(open(source, "rb"))
        magic = handle.read(2)
        handle.seek(0)
        if magic == b"\x1f\x8b":
            handle = stack.enter_context(gzip.GzipFile(fileobj=handle, mode="rb"))
        yield handle


def _sniff(source: Source) -> Dict[str, str]:
    with _binary(source) as handle:
        head = handle.read(64 * 1024)
    try:
        text = head.decode("utf-8")
        encoding = "utf-8-sig"
    except UnicodeDecodeError as error:
        if error.start >= len(head) - 4:
            text, encoding = head[:error.start].decode("utf-8"), "utf-8-sig"
        else:
            text, encoding = head.decode("cp1252", errors="replace"), "cp1252"
    lines = text.lstrip("﻿").splitlines()
    header = lines[0] if lines else ""
    separator = max((";", "\t", ","), key=header.count)
    decimal = "."
    if separator != "," and len(lines) > 1 and re.search(r"\d,\d", " ".join(lines[1:20])):
        decimal = ","
    return {"encoding": encoding, "sep": separator, "decimal": decimal}


def _is_excel(filename: str) -> bool:
    return PurePath(str(filename)).suffix.lower() in (".xlsx", ".xls")


def _read_excel(source: Source, **kwargs) -> pd.DataFrame:
    data = bytes(source) if isinstance(source, (bytes, bytearray, memoryview)) else Path(source).read_bytes()
    return pd.read_excel(io.BytesIO(data), na_values=_NA_VALUES, **kwargs)


def _chunks(filename: str, source: Source, chunksize: int) -> Iterator[pd.DataFrame]:
    """Blocos da tabela, CSV (texto ou gzip) ou planilha, com o texto da estrutura como texto."""
    if _is_excel(filename):
        yield _read_excel(source, dtype={column: str for column in _TEXT_COLUMNS | set(_CODE_SOURCES)})
        return
    options = _sniff(source)
    with _binary(source) as handle:
        reader = pd.read_csv(
            handle,
            sep=options["sep"],
            decimal=options["decimal"],
            encoding=options["encoding"],
            encoding_errors="replace",
            dtype={column: str for column in _TEXT_COLUMNS | set(_CODE_SOURCES)},
            na_values=_NA_VALUES,
            chunksize=chunksize,
            low_memory=False,
        )
        for chunk in reader:
            yield chunk


def _strip_names(chunk: pd.DataFrame, tokens: Set[str]) -> pd.DataFrame:
    """Código no lugar do nome: deriva `participant_code` e tira as colunas pessoais."""
    chunk.columns = [str(column).strip() for column in chunk.columns]
    code = None
    for column in _CODE_SOURCES:
        if column not in chunk:
            continue
        uniques = chunk[column].dropna().unique()
        mapped = chunk[column].map({value: participant_code(value) for value in uniques})
        code = mapped if code is None else code.fillna(mapped)
    for column in chunk.columns:
        if is_personal_column(column):
            tokens.update(name_tokens(chunk[column].dropna().unique(),
                                      from_filename=fold(column) in _FILENAME_COLUMNS))
    chunk = chunk.drop(columns=[column for column in chunk.columns if is_personal_column(column)])
    if code is not None:
        position = chunk.columns.get_loc("sessao_id") + 1 if "sessao_id" in chunk else 0
        chunk.insert(position, "participant_code", code.astype(object).where(code.notna(), None))
    return chunk


def _downcast(chunk: pd.DataFrame) -> pd.DataFrame:
    for column in chunk.select_dtypes(include="float64").columns:
        if column not in _FLOAT64_COLUMNS:
            chunk[column] = chunk[column].astype("float32")
    return chunk


def _finish(frame: pd.DataFrame, tokens: Set[str]) -> pd.DataFrame:
    """Tipos finais e texto livre sem nome."""
    scrub = scrubber(tokens)
    for column in list(frame.columns):
        series = frame[column]
        if series.dtype != object:
            continue
        values = series.dropna()
        if values.empty:
            if column == "Codigo":
                frame = frame.drop(columns=[column])
            continue
        uniques = pd.Series(values.unique())
        as_text = uniques.astype(str).str.strip()
        if set(as_text.str.lower()) <= {"true", "false"}:
            frame[column] = series.map(lambda v: None if _missing(v) else str(v).strip().lower() == "true") \
                .astype("boolean")
            continue
        if column not in _TEXT_COLUMNS:
            numbers = pd.to_numeric(as_text.str.replace(",", ".", regex=False), errors="coerce")
            if numbers.notna().mean() >= 0.9:
                frame[column] = pd.to_numeric(series.astype(str).str.replace(",", ".", regex=False),
                                              errors="coerce")
                continue
        if column in _UNSCRUBBED:
            continue
        mapping = {value: scrub(str(value)) for value in values.unique()}
        frame[column] = series.map(lambda v: None if _missing(v) else mapping.get(v, scrub(str(v))))
    if "Bloco" in frame:
        frame["Bloco"] = pd.to_numeric(frame["Bloco"], errors="coerce").round().astype("Int64")
    return frame.reset_index(drop=True)


def read_pipeline_table(filename: str, source: Source, *, windows: bool = False,
                        chunksize: int = 50_000) -> pd.DataFrame:
    """Tabela de uma saída do pipeline, sem nome de participante, em blocos."""
    tokens: Set[str] = set()
    parts = []
    for chunk in _chunks(filename, source, chunksize):
        chunk = _strip_names(chunk, tokens)
        parts.append(_downcast(chunk) if windows else chunk)
    frame = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    return _finish(frame, tokens)


def table_summary(frame: pd.DataFrame) -> Dict[str, object]:
    """O que a revisão mostra de uma tabela: contagens e rótulos, nunca linhas."""
    summary: Dict[str, object] = {"linhas": int(len(frame)), "colunas": int(len(frame.columns))}
    if "sessao_id" in frame:
        summary["sessoes"] = int(frame["sessao_id"].nunique())
    if "participant_code" in frame:
        codes = frame["participant_code"].dropna().astype(str)
        summary["participantes"] = sorted(codes.unique())
        summary["linhas_sem_codigo"] = int(frame["participant_code"].isna().sum())
    for column, key in (("experimento", "experimentos"), ("amostra", "amostras")):
        if column in frame:
            counts = frame[column].fillna("(vazio)").astype(str).value_counts()
            summary[key] = {str(k): int(v) for k, v in counts.head(20).items()}
    if "Etapa" in frame:
        summary["etapas"] = sorted(frame["Etapa"].dropna().astype(str).unique())[:40]
    return summary


# ---------------------------------------------------------------------------
# Papéis
# ---------------------------------------------------------------------------

def _parse_pipeline(parsed: ParsedFile, source: Source) -> None:
    table = read_pipeline_table(parsed.filename, source, windows=parsed.role in WINDOW_ROLES)
    if parsed.role == "associacao_tentativas":
        table = table.rename(columns=_ASSOCIATION_COLUMNS)
        missing = [c for c in ("palavra", "rt", "resposta") if c not in table]
        if missing:
            parsed.error("Faltam colunas do teste de associação: {}.".format(", ".join(missing)))
            return
        table["rt"] = pd.to_numeric(table["rt"], errors="coerce")
        if "tentativa" in table:
            table["tentativa"] = pd.to_numeric(table["tentativa"], errors="coerce").astype("Int64")
    if "sessao_id" not in table:
        parsed.error("A tabela não tem a coluna sessao_id, que liga as camadas.")
        return
    if "participant_code" not in table:
        parsed.warn("Sem coluna de participante: as sessões ficam sem código até a revisão.")
    elif table["participant_code"].isna().all():
        parsed.warn("Nenhuma linha com código de participante.")
    parsed.table = table


def _parse_inventory(parsed: ParsedFile, source: Source) -> None:
    sheets = _read_excel(source, sheet_name=None, dtype=object)
    by_name = {fold(name): frame for name, frame in sheets.items()}
    tokens: Set[str] = set()
    parts = []
    for sheet, origin in (("sessoes", "sessao"), ("sem_participante", "sem_participante"),
                          ("excluidas", "excluida")):
        frame = by_name.get(sheet)
        if frame is None or frame.empty:
            continue
        frame = _strip_names(frame.copy(), tokens)
        frame.columns = [column if column in ("participant_code", "sessao_id") else _snake(column)
                         for column in frame.columns]
        if "experimento" in frame and "slot" not in frame:
            frame = frame.rename(columns={"experimento": "slot"})
        frame.insert(0, "origem", origin)
        parts.append(frame)
    if not parts:
        parsed.error("A planilha não tem a aba 'sessoes' do inventário.")
        return
    table = pd.concat(parts, ignore_index=True)
    for column in table.columns:
        if column == "duracao_s":
            table[column] = pd.to_numeric(table[column], errors="coerce")
        else:
            table[column] = table[column].map(lambda v: None if _missing(v) else str(v))
    scrub = scrubber(tokens)
    for column in table.columns:
        if column not in _UNSCRUBBED and table[column].dtype == object:
            table[column] = table[column].map(lambda v: None if v is None else scrub(v))
    parsed.table = table.reset_index(drop=True)
    summary = by_name.get("resumo")
    if summary is not None and summary.shape[1] >= 2:
        items = {}
        for key, value in summary.iloc[:, :2].itertuples(index=False):
            if _missing(key) or fold(key).startswith("projeto"):
                continue  # o caminho da pasta do estudo não interessa à análise
            items[scrub(str(key))] = None if _missing(value) else scrub(str(value))
        parsed.meta["inventario"] = items


_MANIFEST_ALLOWED = {
    "gerado_em": True,
    "tipo": True,
    "inicio": True,
    "fim": True,
    "contagens": True,
    "codigo": {"pacote": True, "commit": True, "branch": True, "alteracoes_nao_commitadas": True},
    "ambiente": {"python": True, "plataforma": True, "pacotes": True},
    "configuracao": {
        "EEG_CONFIG": True,
        "EEG_DEVICE_PROFILES": True,
        "PERIFERICOS_CONFIG": True,
        "PERIFERICOS_DEVICE_PROFILES": True,
        "RR_VALID_MS": True,
        "PROJETO": {
            "sessoes": {"experimentos_excluir": True},
            "participantes": {"padrao": True},
            "analise": True,
            "eeg": True,
            "etapas": True,
        },
    },
}


def _allowed(value, spec):
    if spec is True:
        return value
    if not isinstance(value, dict):
        return None
    return {key: _allowed(value[key], sub) for key, sub in spec.items() if key in value}


def _parse_manifest(parsed: ParsedFile, source: Source) -> None:
    with _binary(source) as handle:
        raw = handle.read()
    try:
        manifest = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        parsed.error("O manifesto não é um JSON válido.")
        return
    if not isinstance(manifest, dict):
        parsed.error("O manifesto não é um objeto JSON.")
        return
    kept = _allowed(manifest, _MANIFEST_ALLOWED)
    # Do que tem nome de arquivo, só a contagem.
    kept["n_entradas"] = len(manifest.get("entradas") or [])
    kept["n_erros"] = len(manifest.get("erros") or [])
    kept["interrompido"] = bool(manifest.get("interrompido"))
    kept["falhou"] = bool(manifest.get("falha"))
    parsed.meta["manifesto"] = kept


def _parse_field_log(parsed: ParsedFile, source: Source) -> None:
    if _is_excel(parsed.filename):
        raw = _read_excel(source, header=None, dtype=object)
    else:
        options = _sniff(source)
        with _binary(source) as handle:
            raw = pd.read_csv(handle, sep=options["sep"], header=None, dtype=object, encoding=options["encoding"],
                              encoding_errors="replace")
    header_row = None
    for index in range(min(len(raw), 15)):
        cells = [fold(value) for value in raw.iloc[index].tolist() if not _missing(value)]
        if any("participante" in cell for cell in cells) and any(cell.startswith("canal") for cell in cells):
            header_row = index
            break
    if header_row is None:
        parsed.error("Não achei o cabeçalho com o participante e os canais (CANAL 1, CANAL 2...).")
        return
    header = [normalize_label(value) if not _missing(value) else "" for value in raw.iloc[header_row].tolist()]
    body = raw.iloc[header_row + 1:].reset_index(drop=True)
    code_index = next(i for i, name in enumerate(header) if "participante" in fold(name))
    channel_columns = {}
    quality_index = observation_index = None
    for index, name in enumerate(header):
        key = fold(name)
        match = re.match(r"canal\s*(\d+)", key)
        if match:
            channel_columns[int(match.group(1))] = index
        elif key.startswith("qualidade"):
            quality_index = index
        elif key.startswith("observa"):
            observation_index = index
    rows = []
    for values in body.itertuples(index=False):
        code = participant_code(values[code_index])
        if code is None:
            continue
        row = {"participant_code": code}
        row["qualidade_sinal"] = None if quality_index is None or _missing(values[quality_index]) \
            else str(values[quality_index]).strip()
        row["observacao"] = None if observation_index is None or _missing(values[observation_index]) \
            else str(values[observation_index]).strip()
        flagged = []
        for number, index in sorted(channel_columns.items()):
            value = values[index]
            problem = (not _missing(value)) and fold(value) in ("true", "1", "x", "sim", "s")
            row["canal_{:02d}".format(number)] = problem
            if problem:
                flagged.append(str(number))
        row["canais_problema"] = ",".join(flagged)
        row["n_canais_problema"] = len(flagged)
        rows.append(row)
    if not rows:
        parsed.error("Nenhuma linha com o código do participante.")
        return
    parsed.table = pd.DataFrame(rows)


def _parse_base_limpa(parsed: ParsedFile, source: Source) -> None:
    keys = BASE_LIMPA_KEYS[parsed.role]
    if _is_excel(parsed.filename):
        data = source if isinstance(source, (str, Path)) else io.BytesIO(bytes(source))
        try:
            table = xlsx_keys.read_columns(data, BASE_LIMPA_SHEET, keys)
        except (ValueError, KeyError) as error:
            parsed.error(str(error))
            return
    else:
        # A tentativa pode chegar com o nome do pipeline ou com o do app.
        aliases = {"tentativa": "Trial Number"}
        options = _sniff(source)
        with _binary(source) as handle:
            table = pd.read_csv(handle, sep=options["sep"], decimal=options["decimal"], encoding=options["encoding"],
                                encoding_errors="replace", dtype={"sessao_id": str, "Etapa": str},
                                usecols=lambda column: aliases.get(str(column).strip(), str(column).strip()) in keys)
        table.columns = [aliases.get(str(column).strip(), str(column).strip()) for column in table.columns]
        missing = [column for column in keys if column not in table]
        if missing:
            parsed.error("Faltam as colunas-chave da BASE LIMPA: {}.".format(", ".join(missing)))
            return
    table = table[list(keys)].rename(columns=_ASSOCIATION_COLUMNS).copy()
    for column in ("sessao_id", "Etapa"):
        if column in table:
            table[column] = table[column].map(lambda v: None if _missing(v) else str(v).strip())
    for column in ("Bloco", "tentativa"):
        if column in table:
            table[column] = pd.to_numeric(table[column], errors="coerce").round().astype("Int64")
    if "Tempo" in table:
        table["Tempo"] = pd.to_numeric(table["Tempo"], errors="coerce").astype("float64")
    table = table.dropna(subset=["sessao_id"]).drop_duplicates().reset_index(drop=True)
    if table.empty:
        parsed.error("Nenhuma linha nas chaves da BASE LIMPA.")
        return
    parsed.table = table


def _parse_profile(parsed: ParsedFile, source: Source) -> None:
    if _is_excel(parsed.filename):
        frame = _read_excel(source, dtype=str)
    else:
        options = _sniff(source)
        with _binary(source) as handle:
            frame = pd.read_csv(handle, sep=options["sep"], dtype=str, encoding=options["encoding"],
                                encoding_errors="replace", keep_default_na=False, na_values=[""])
    frame.columns = [normalize_label(column) for column in frame.columns]
    candidates = [c for c in frame.columns
                  if fold(c) in ("codigo", "code", "cod", "codigo do participante", "id", "participante",
                                 "id participante", "numero", "n")]
    if not candidates:
        parsed.error("A planilha de perfil precisa de uma coluna com o código do participante "
                     "(Código, Participante ou ID).")
        return
    codes = None
    for column in candidates:
        mapped = frame[column].map(participant_code)
        codes = mapped if codes is None else codes.fillna(mapped)
    attributes = [c for c in frame.columns if c not in candidates and not is_personal_column(c)]
    dropped = [c for c in frame.columns if c not in candidates and is_personal_column(c)]
    table = pd.DataFrame({"participant_code": codes})
    for column in attributes:
        table[_snake(column) or "atributo"] = frame[column].map(
            lambda v: None if _missing(v) or not str(v).strip() else normalize_label(v))
    table = table.dropna(subset=["participant_code"]).drop_duplicates("participant_code", keep="last")
    if table.empty:
        parsed.error("Nenhuma linha com código de participante.")
        return
    if dropped:
        parsed.warn("{} coluna(s) com dado pessoal ficaram de fora.".format(len(dropped)))
    parsed.table = table.reset_index(drop=True)
    parsed.meta["atributos"] = [c for c in table.columns if c != "participant_code"]


_IMAGE_MAGIC = ((b"\x89PNG", "png"), (b"\xff\xd8\xff", "jpeg"), (b"RIFF", "webp"))


def _parse_file(parsed: ParsedFile, source: Source) -> None:
    suffix = PurePath(parsed.filename).suffix.lower()
    with _binary(source) as handle:
        head = handle.read(16)
    parsed.meta["extensao"] = suffix
    if suffix in IMAGE_EXTENSIONS:
        kind = next((name for magic, name in _IMAGE_MAGIC if head.startswith(magic)), None)
        if kind is None:
            parsed.error("O arquivo não é uma imagem PNG, JPEG ou WebP.")
            return
        parsed.meta["formato"] = kind
    if parsed.role == "eeg_topomapa":
        stem = PurePath(parsed.filename).stem
        parts = stem.split("_", 2)
        if len(parts) == 3 and fold(parts[0]) == "topomap":
            parsed.meta["experimento"] = parts[1]
            parsed.meta["etapa_arquivo"] = parts[2]


def parse_file(filename: str, source: Source, *, role: Optional[str] = None,
               rel_path: Optional[str] = None) -> ParsedFile:
    """Lê um arquivo enviado. O papel vem de quem envia ou é deduzido do nome."""
    role = role or detect_role(filename)
    parsed = ParsedFile(filename=PurePath(str(filename)).name, role=role)
    if role not in ROLES:
        parsed.role = None
        parsed.error("Não reconheci o tipo deste arquivo; escolha o papel dele.")
        return parsed
    name = parsed.filename.lower()
    gzipped = name.endswith(".gz")
    suffix = PurePath(name[:-3] if gzipped else name).suffix
    allowed = _ROLE_EXTENSIONS.get(role) or _EXTENSIONS[str(ROLES[role]["kind"])]
    if suffix not in allowed and not (gzipped and ".gz" in allowed and suffix in ("", ".csv")):
        parsed.error("Formato {} não serve para {}.".format(suffix or "sem extensão", ROLES[role]["label"]))
        return parsed
    run_id = run_id_from_path(rel_path)
    if run_id:
        parsed.meta["run_id"] = run_id
    try:
        if role in PIPELINE_ROLES:
            _parse_pipeline(parsed, source)
        elif role == "inventario":
            _parse_inventory(parsed, source)
        elif role == "manifesto":
            _parse_manifest(parsed, source)
        elif role == "campo_qualidade":
            _parse_field_log(parsed, source)
        elif role in BASE_LIMPA_ROLES:
            _parse_base_limpa(parsed, source)
        elif role == "perfil":
            _parse_profile(parsed, source)
        else:
            _parse_file(parsed, source)
    except Exception as error:  # o conteúdo nunca vai para a mensagem
        parsed.table = None
        parsed.error("Não consegui ler o arquivo ({}).".format(type(error).__name__))
    if parsed.table is not None:
        parsed.meta["resumo"] = table_summary(parsed.table)
    return parsed
