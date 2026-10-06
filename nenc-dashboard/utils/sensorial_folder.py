"""
Leitura da pasta de um projeto NENC para o Teste Sensorial.

Usado pelo script `scripts/nenc_enviar.py --modulo teste_sensorial`, no
computador de quem envia. Classifica cada arquivo pela estrutura padrão dos
projetos NENC, pelo caminho e pelo nome; a leitura de verdade acontece no
servidor, ao gravar a importação (`utils/sensorial_ingest.py`).

Estrutura padrão (nomes comparados sem acento e sem caixa):

- `2.DADOS/2.2 .../<modalidade>/run_<data>/`: saídas do pipeline. Vai só a
  rodada mais nova de cada modalidade que não falhou; as anteriores, a pasta
  `_execucoes_antigas`, as cópias em xlsx das tabelas e os logs ficam;
- `2.DADOS/2.2 .../_inventarios/`: o inventário mais recente das sessões;
- `2.DADOS/2.3 ...`: só a planilha com a aba BASE LIMPA, e dela só as
  colunas-chave (extraídas no envio). O resto do 2.3 é saída do SPSS, que o
  app recalcula a partir do pipeline;
- `1.GESTAO_PROJETOS/1.4 ...`: o registro de campo da qualidade do sinal
  (planilha com o participante e os canais);
- `1.GESTAO_PROJETOS/1.1 ...`: briefing; `3.DRAFTS.../3.2` e
  `5.RELATORIOS/5.2`: relatório final, só a versão mais recente;
- `1.2 Estímulos` e `6.ESTIMULOS`: estímulos; `Artigos`: literatura.

Dado pessoal nunca entra e é a primeira regra: recrutamento, fotos de
participantes, registros e vídeos das coletas, e as cópias dos dados brutos
(2.0 e 2.1). Prosódia e transcrições ficam fora deste módulo. Exceções à
estrutura ficam num `sensorial_import.toml` opcional na raiz (ver
`load_config`).
"""

import fnmatch
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Sequence, Tuple

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - o Docker e a .venv usam 3.11+
    tomllib = None

from utils import sensorial_ingest, xlsx_keys
from utils.jornada_folder import Entry, keep_latest_reports
from utils.jornada_taxonomy import fold

CONFIG_NAME = "sensorial_import.toml"

ROLE_LABELS = dict({role: str(spec["label"]) for role, spec in sensorial_ingest.ROLES.items()},
                   ignorado="Ficam de fora")
# Os pequenos primeiro: se o envio parar, o que define a análise já chegou.
SEND_ORDER = (
    "manifesto", "inventario", "campo_qualidade", "perfil", "base_limpa_associacao", "base_limpa_perifericos",
    "base_limpa_eeg", "eeg_qualidade",
    "perifericos_qualidade", "associacao_tentativas", "eeg_psd_medio", "perifericos_metricas",
    "eeg_indicadores", "eeg_topomapa", "documento", "estimulo", "literatura", "eeg_psd",
)
# Papéis que o sensorial_import.toml pode apontar para pastas fora do padrão.
CONFIG_ROLES = ("perfil", "campo_qualidade", "documento", "literatura", "estimulo")

_MB = 1024 * 1024
# O que a API de importação aceita. O PSD por janela passa de 150 MB em CSV
# (o script comprime as tabelas grandes antes de enviar).
FILE_LIMITS = {
    "eeg_psd": 1024 * _MB,
    "eeg_indicadores": 512 * _MB,
    "perifericos_metricas": 512 * _MB,
    "base_limpa_eeg": 256 * _MB,
    "base_limpa_perifericos": 256 * _MB,
    "base_limpa_associacao": 256 * _MB,
    "documento": 100 * _MB,
    "literatura": 100 * _MB,
}
DEFAULT_FILE_LIMIT = 100 * _MB
IMAGE_FILE_LIMIT = 25 * _MB

DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt", ".md"}
IMAGE_EXTENSIONS = set(sensorial_ingest.IMAGE_EXTENSIONS)
_JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
_JUNK_EXTENSIONS = {".db", ".tmp", ".lnk", ".ini", ".part", ".log", ".spv", ".sav", ".sps"}

# Pastas que nunca entram, com o motivo que a prévia mostra. Dado pessoal vem
# primeiro, para nunca ser confundido com outra regra.
_IGNORED_FOLDERS: Tuple[Tuple[str, str], ...] = (
    ("fotos participantes", "fotos de participantes são dado pessoal e não saem do computador"),
    ("recrutamento", "planilhas de recrutamento são dado pessoal e não saem do computador"),
    ("registros de coleta", "fotos e registros das coletas são dado pessoal e não saem do computador"),
    ("videos coleta", "vídeos das coletas são dado pessoal e não saem do computador"),
    ("dados originais", "backup dos dados brutos, com o nome dos participantes"),
    ("dados para trabalho", "cópia de trabalho dos dados brutos, com o nome dos participantes"),
    ("_excluidos", "sessões excluídas da cópia de trabalho"),
    ("_extracoes", "extrações intermediárias do pipeline"),
    ("_dados testes", "dados de teste do equipamento"),
    ("work_manipulacao", "pasta de trabalho (scripts e temporários)"),
    ("_execucoes_antigas", "rodada antiga do pipeline"),
    ("experimentos eventide", "experimento do EventIDE (o pipeline já leu)"),
)
_OUT_OF_MODULE = ("audio", "transcri", "prosodia", "eyetracking")
_RUN = re.compile(r"^run_\d{8}-\d{6}$")
_PIPELINE_REASONS = (
    (re.compile(r"^error_log\."), "registro de erros da rodada"),
    (re.compile(r"^observacoes\."), "observações da rodada (com nome de arquivo)"),
    (re.compile(r"^iat_(?!consolidado).+\.csv$"), "parte do IAT_consolidado"),
    (re.compile(r"^outros_"), "lista de sessões, sem medida"),
    (re.compile(r"^perifericos_(batimentos|fc_segundo)\."), "detalhe por batimento ou segundo, não usado no app"),
)


@dataclass
class FolderConfig:
    """Exceções à estrutura padrão, lidas do `sensorial_import.toml`."""

    pastas: Dict[str, List[str]] = field(default_factory=dict)
    ignorar: List[str] = field(default_factory=list)
    aba_base_limpa: str = sensorial_ingest.BASE_LIMPA_SHEET


def load_config(root: Path) -> FolderConfig:
    """Lê o `sensorial_import.toml` da raiz do projeto, se existir.

    Exemplo::

        ignorar = ["**/rascunho*"]
        aba_base_limpa = "BASE LIMPA"

        [pastas]         # pastas extras por papel, relativas à raiz
        perfil = ["1.GESTAO_PROJETOS/1.4.Campo/Perfil"]
        literatura = ["Referencias"]
    """
    path = Path(root) / CONFIG_NAME
    if not path.is_file():
        return FolderConfig()
    if tomllib is None:  # pragma: no cover
        raise RuntimeError("Python 3.11 ou mais novo é necessário para ler o {}.".format(CONFIG_NAME))
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    pastas = {}
    for role, folders in (data.get("pastas") or {}).items():
        if role not in CONFIG_ROLES:
            raise ValueError("Papel desconhecido em [pastas] do {}: {}.".format(CONFIG_NAME, role))
        pastas[role] = [str(folder).replace("\\", "/").strip("/") for folder in folders or []]
    return FolderConfig(
        pastas=pastas,
        ignorar=[str(pattern) for pattern in data.get("ignorar") or []],
        aba_base_limpa=str(data.get("aba_base_limpa") or sensorial_ingest.BASE_LIMPA_SHEET),
    )


def file_limit(role: str) -> int:
    """Maior arquivo que a importação aceita para o papel, em bytes."""
    if sensorial_ingest.ROLES.get(role, {}).get("kind") == "image":
        return IMAGE_FILE_LIMIT
    return FILE_LIMITS.get(role, DEFAULT_FILE_LIMIT)


# ---------------------------------------------------------------------------
# Classificação
# ---------------------------------------------------------------------------

def _run_index(parts: Sequence[str]) -> int:
    for index, part in enumerate(parts[:-1]):
        if _RUN.match(part):
            return index
    return -1


def _document(role: str, extension: str, doc_type: str = "") -> Tuple[str, Dict[str, object], str]:
    if extension not in DOCUMENT_EXTENSIONS and not (role == "estimulo" and extension in IMAGE_EXTENSIONS):
        return "ignorado", {}, "tipo de arquivo não lido como documento"
    return role, ({"doc_type": doc_type} if doc_type else {}), ""


def _config_role(rel: PurePosixPath, config: FolderConfig, extension: str) -> Optional[Tuple[str, Dict, str]]:
    folded_rel = fold(rel.as_posix())
    for role, folders in config.pastas.items():
        for folder in folders:
            if folded_rel.startswith(fold(folder).rstrip("/") + "/"):
                if role in ("perfil", "campo_qualidade"):
                    if extension in (".xlsx", ".xls", ".csv"):
                        return role, {}, ""
                    return "ignorado", {}, "planilha de {} precisa ser .xlsx ou .csv".format(role)
                return _document(role, extension, "briefing" if role == "documento" else "")
    return None


def _classify(rel: PurePosixPath, config: FolderConfig) -> Tuple[str, Dict[str, object], str]:
    parts = list(rel.parts)
    folded_parts = [fold(part) for part in parts]
    folded_dirs = folded_parts[:-1]
    folded_name = folded_parts[-1]
    extension = rel.suffix.lower()

    if rel.name.startswith("~$"):
        return "ignorado", {}, "trava temporária do Office"
    if folded_name in _JUNK_NAMES or extension in _JUNK_EXTENSIONS:
        return "ignorado", {}, "arquivo de sistema, log ou do SPSS"
    for pattern in config.ignorar:
        if fnmatch.fnmatch(fold(rel.as_posix()), fold(pattern)):
            return "ignorado", {}, "excluído pelo {}".format(CONFIG_NAME)
    for folder in folded_dirs:
        for key, reason in _IGNORED_FOLDERS:
            if key in folder:
                return "ignorado", {}, reason

    configured = _config_role(rel, config, extension)
    if configured is not None:
        return configured

    in_22 = any(part.startswith("2.2") for part in folded_dirs)
    in_23 = any(part.startswith("2.3") for part in folded_dirs)
    in_management = any(part.startswith("1.gestao") for part in folded_dirs)
    in_reports = any(part.startswith("3.drafts") or part.startswith("5.relatorio") for part in folded_dirs)

    if in_22:
        if any(part.startswith("_inventario") for part in folded_dirs):
            if folded_name.startswith("inventario") and extension == ".xlsx":
                return "inventario", {}, ""
            return "ignorado", {}, "arquivo de inventário sem regra"
        run = _run_index(parts)
        modality = folded_parts[run - 1] if run > 0 else ""
        if any(word in modality for word in _OUT_OF_MODULE):
            return "ignorado", {}, "prosódia, transcrição e eye tracking ficam fora do Teste Sensorial"
        if run < 0:
            return "ignorado", {}, "fora de uma rodada do pipeline (run_...)"
        if "brutos" in folded_dirs[run + 1:]:
            return "ignorado", {}, "arquivos brutos da rodada"
        role = sensorial_ingest.detect_role(rel.name)
        if role is not None and extension == ".xlsx" and role != "inventario":
            return "ignorado", {}, "cópia em xlsx de uma tabela (vai o .csv)"
        if role is not None:
            return role, {"run_id": parts[run], "modalidade": parts[run - 1] if run > 0 else ""}, ""
        for pattern, reason in _PIPELINE_REASONS:
            if pattern.search(folded_name):
                return "ignorado", {}, reason
        return "ignorado", {}, "saída do pipeline sem uso no app"
    if in_23:
        if any(word in part for part in folded_dirs for word in _OUT_OF_MODULE):
            return "ignorado", {}, "prosódia, transcrição e eye tracking ficam fora do Teste Sensorial"
        if any("antigo" in part or part in ("old", "backup") for part in folded_dirs):
            return "ignorado", {}, "versão antiga do consolidado"
        if extension == ".xlsx":
            return "base_limpa", {"candidata": True}, ""
        return "ignorado", {}, "consolidado do SPSS: o app recalcula a partir do pipeline"
    if in_management:
        if any(part.startswith("1.1") for part in folded_dirs):
            return _document("documento", extension, "briefing")
        if any(part.startswith("1.2") for part in folded_dirs):
            return _document("estimulo", extension)
        if any(part.startswith("1.4") for part in folded_dirs) and extension in (".xlsx", ".xls"):
            return "campo_qualidade", {"candidata": True}, ""
        return "ignorado", {}, "planilha de gestão, sem dado de análise"
    if in_reports:
        if any(part.startswith("3.2") or part.startswith("5.2") for part in folded_dirs) \
                and extension in (".pptx", ".docx", ".pdf"):
            return "documento", {"doc_type": "relatorio"}, ""
        return "ignorado", {}, "rascunho ou material de análise"
    if any("estimulo" in part for part in folded_dirs):
        return _document("estimulo", extension)
    if any("artigo" in part for part in folded_dirs):
        return _document("literatura", extension)
    return "ignorado", {}, "fora das pastas reconhecidas"


# ---------------------------------------------------------------------------
# Escolhas entre arquivos
# ---------------------------------------------------------------------------

def _drop(entry: Entry, reason: str) -> None:
    entry.role, entry.meta, entry.reason = "ignorado", {}, reason


def _run_failed(root: Path, run_dir: PurePosixPath) -> bool:
    """Rodada que o próprio pipeline marcou como interrompida ou com falha no manifesto."""
    manifest = root / run_dir / "manifest.json"
    if not manifest.is_file():
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return True
    return bool(data.get("falha") or data.get("interrompido"))


def keep_latest_runs(entries: List[Entry], root: Path) -> None:
    """De cada modalidade, só a rodada mais nova que não falhou."""
    runs: Dict[PurePosixPath, Dict[str, List[Entry]]] = {}
    for entry in entries:
        rel = PurePosixPath(entry.rel_path)
        index = _run_index(rel.parts)
        if entry.role == "ignorado" or index < 1:
            continue
        modality = PurePosixPath(*rel.parts[:index])
        runs.setdefault(modality, {}).setdefault(rel.parts[index], []).append(entry)
    for modality, by_run in runs.items():
        ordered = sorted(by_run, reverse=True)
        chosen = next((run for run in ordered if not _run_failed(root, modality / run)), None)
        for run in ordered:
            if run == chosen:
                continue
            reason = ("rodada que o pipeline marcou como falha" if _run_failed(root, modality / run)
                      else "rodada anterior do pipeline (vai a {})".format(chosen))
            for entry in by_run[run]:
                _drop(entry, reason)


def _keep_latest_inventory(entries: List[Entry]) -> None:
    inventories = sorted((e for e in entries if e.role == "inventario"), key=lambda e: e.rel_path, reverse=True)
    for entry in inventories[1:]:
        _drop(entry, "inventário anterior (vai o mais recente)")


_LAYER_FOLDERS = (("perif", "base_limpa_perifericos"), ("iat", "base_limpa_associacao"),
                  ("associa", "base_limpa_associacao"), ("claim", "base_limpa_associacao"), ("eeg", "base_limpa_eeg"))


def _base_limpa_role(rel_path: str, path: Path, sheet: str) -> Optional[str]:
    """A camada da BASE LIMPA: pela pasta do consolidado ou, sem ela, pelas colunas da aba."""
    folders = [fold(part) for part in PurePosixPath(rel_path).parts[:-1]]
    below = next((index + 1 for index, folder in enumerate(folders) if folder.startswith("2.3")), 0)
    for folder in folders[below:]:
        for key, role in _LAYER_FOLDERS:
            if key in folder:
                return role
    try:
        columns = {fold(name) for name in xlsx_keys.header_row(path, sheet, "sessao_id")}
    except Exception:
        return None
    if "trial number" in columns:
        return "base_limpa_associacao"
    if "bpm" in columns or "gsr_cal_mean" in columns:
        return "base_limpa_perifericos"
    if any(name.endswith("_alpha") for name in columns) or "fai" in columns:
        return "base_limpa_eeg"
    return None


def _choose_base_limpa(entries: List[Entry], root: Path, sheet: str) -> None:
    """Uma planilha com BASE LIMPA por camada: a mais recente de cada uma."""
    by_role: Dict[str, List[Entry]] = {}
    for entry in entries:
        if entry.role != "base_limpa" or not entry.meta.get("candidata"):
            continue
        path = root / entry.rel_path
        try:
            names = xlsx_keys.sheet_names(path)
        except Exception:
            names = []
        if not any(fold(name) == fold(sheet) for name in names):
            _drop(entry, "consolidado do SPSS: o app recalcula a partir do pipeline")
            continue
        role = _base_limpa_role(entry.rel_path, path, sheet)
        if role is None:
            _drop(entry, "aba {} de uma camada que o app não reconhece".format(sheet))
            continue
        by_role.setdefault(role, []).append(entry)
    for role, candidates in by_role.items():
        candidates.sort(key=lambda e: (root / e.rel_path).stat().st_mtime, reverse=True)
        for entry in candidates[1:]:
            _drop(entry, "outra planilha com a {} da mesma camada (vai a mais recente)".format(sheet))
        entry = candidates[0]
        entry.role, entry.meta = role, {"aba": sheet}


def _is_field_log(path: Path) -> bool:
    import pandas as pd

    try:
        head = pd.read_excel(path, header=None, nrows=15, dtype=str)
    except Exception:
        return False
    cells = [fold(value) for value in head.fillna("").values.ravel().tolist() if str(value).strip()]
    return any("participante" in cell for cell in cells) and any(cell.startswith("canal") for cell in cells)


def _check_field_logs(entries: List[Entry], root: Path) -> None:
    for entry in entries:
        if entry.role == "campo_qualidade" and entry.meta.get("candidata"):
            if _is_field_log(root / entry.rel_path):
                entry.meta = {}
            else:
                _drop(entry, "planilha de campo sem o participante e os canais")


def scan_project(root: Path, config: Optional[FolderConfig] = None) -> List[Entry]:
    """Todos os arquivos da pasta do projeto, cada um com papel e motivo."""
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError("Pasta do projeto não encontrada: {}".format(root))
    config = config if config is not None else load_config(root)
    entries = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = PurePosixPath(path.relative_to(root).as_posix())
        if rel.name == CONFIG_NAME:
            continue
        role, meta, reason = _classify(rel, config)
        entries.append(Entry(rel.as_posix(), path.stat().st_size, role, meta, reason))
    keep_latest_runs(entries, root)
    _keep_latest_inventory(entries)
    _choose_base_limpa(entries, root, config.aba_base_limpa)
    _check_field_logs(entries, root)
    keep_latest_reports(entries)
    return entries


def summarize(entries: Sequence[Entry]) -> Dict[str, Dict[str, int]]:
    """Quantidade e bytes por papel, na ordem de envio."""
    summary: Dict[str, Dict[str, int]] = {}
    for role in SEND_ORDER + ("ignorado",):
        chosen = [entry for entry in entries if entry.role == role]
        if chosen:
            summary[role] = {"files": len(chosen), "bytes": sum(entry.size for entry in chosen)}
    return summary
