"""
Leitura da pasta de um projeto NENC para a Jornada de Compra.

Usado pelo script `scripts/jornada_enviar.py`, no computador de quem envia.
Classifica cada arquivo pela estrutura padrão dos projetos NENC e deduz loja,
marca, tarefa e vista de **pasta + nome**, sem abrir o conteúdo: a leitura de
verdade acontece no servidor, na prévia da importação.

Estrutura padrão (nomes comparados sem acento e sem caixa):

- `2.DADOS/2.3 ...`: exports consolidados (csv, xlsx) e heatmaps de loja;
- `2.DADOS/2.2 .../Eyetracking/**`: quadros (`.csv`) e vídeos de cena;
- `2.DADOS/2.2 .../Videos Processados Heatmap/**`: vídeos de heatmap;
- `**/Relação Coletas*.xlsx`: registro de campo (escolha, tempo de compra);
- `1.GESTAO_PROJETOS/1.1 ...` e o fluxo experimental: documentos de briefing;
- `3.DRAFTS RELATÓRIOS/3.2 ...`: relatório final, só a versão mais recente;
- `4.ARQUIVOS AUXILIARES/Fotos Gôndolas/<loja>` e `Fotos pacotes/<marca>`.

Fotos de participantes e planilhas de recrutamento nunca entram: são dado
pessoal. Exceções à estrutura ficam num `jornada_import.toml` opcional na raiz
do projeto (ver `load_config`).
"""

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Sequence, Tuple

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - o Docker e a .venv usam 3.11+
    tomllib = None

from utils.jornada_ingest import parse_recording_filename, store_key
from utils.jornada_taxonomy import fold

CONFIG_NAME = "jornada_import.toml"

ROLES = (
    "dados",
    "quadros",
    "video_cena",
    "video_heatmap",
    "imagem",
    "registro_campo",
    "documento",
    "ignorado",
)
ROLE_LABELS = {
    "dados": "Exports de eye tracking",
    "quadros": "Quadros do rastreador",
    "video_cena": "Vídeos de cena",
    "video_heatmap": "Vídeos de heatmap",
    "imagem": "Imagens",
    "registro_campo": "Registro de campo",
    "documento": "Documentos",
    "ignorado": "Ficam de fora",
}
# Papéis que o jornada_import.toml pode apontar para pastas fora do padrão.
CONFIG_ROLES = (
    "dados",
    "quadros",
    "video_cena",
    "video_heatmap",
    "fotos_gondola",
    "fotos_embalagem",
    "registro_campo",
    "documento",
)

# O que a API de importação aceita (o script confere antes de enviar). O padrão
# é o mesmo do upload pela tela, `jornada_db.MAX_FILE_BYTES`.
CHUNK_BYTES = 8 * 1024 * 1024
FILE_LIMITS = {
    "video_cena": 500 * 1024 * 1024,
    "video_heatmap": 500 * 1024 * 1024,
    "documento": 5 * 1024 * 1024,  # o texto extraído, não o arquivo original
}
DEFAULT_FILE_LIMIT = 25 * 1024 * 1024

DATA_EXTENSIONS = {".csv", ".tsv", ".txt", ".xlsx", ".xls"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov"}
DOCUMENT_EXTENSIONS = {".docx", ".pptx", ".pdf", ".txt", ".md"}

_JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
_JUNK_EXTENSIONS = {".bsproj", ".dat", ".tmp", ".lnk", ".ini", ".db"}

# Pastas que nunca entram, com o motivo que a prévia mostra. A ordem importa:
# dado pessoal vem primeiro, para nunca ser confundido com outra regra.
_IGNORED_FOLDERS: Tuple[Tuple[str, str], ...] = (
    ("fotos participantes", "fotos de participantes são dado pessoal e não saem do computador"),
    ("recrutamento", "planilhas de recrutamento podem ter dados pessoais"),
    ("dados originais", "backup dos dados originais (cópia dos vídeos e exports)"),
    ("dados para trabalho", "cópia de trabalho dos dados brutos"),
    ("work_manipulacao", "pasta de trabalho (scripts e temporários)"),
    ("_dados testes", "dados de teste do equipamento"),
    ("material de apoio", "material de apoio da análise, que duplica os exports"),
    ("experimentos eventide", "experimento do EventIDE, fora da Jornada"),
    ("estimulos", "estímulos do experimento"),
)
_VIEWS = (("frente", "frente"), ("verso", "verso"), ("lateral", "lateral"), ("topo", "topo"),
          ("fundo", "fundo"), ("base", "fundo"))
_VERSION = re.compile(r"[\s_\-]*v(\d+)$")


@dataclass
class Entry:
    """Um arquivo da pasta: caminho relativo, papel e o que se sabe dele."""

    rel_path: str
    size: int
    role: str
    meta: Dict[str, object] = field(default_factory=dict)
    reason: str = ""


@dataclass
class FolderConfig:
    """Exceções à estrutura padrão, lidas do `jornada_import.toml`."""

    lojas: Dict[str, str] = field(default_factory=dict)
    marcas: Dict[str, str] = field(default_factory=dict)
    pastas: Dict[str, List[str]] = field(default_factory=dict)
    ignorar: List[str] = field(default_factory=list)

    def store_for(self, label: str) -> str:
        for key, value in self.lojas.items():
            if fold(key) == fold(label):
                return store_key(value)
        return store_key(label)

    def brand_for(self, folder: str) -> str:
        for key, value in self.marcas.items():
            if fold(key) == fold(folder):
                return str(value).strip()
        return str(folder).strip()


def load_config(root: Path) -> FolderConfig:
    """Lê o `jornada_import.toml` da raiz do projeto, se existir.

    Exemplo::

        ignorar = ["**/rascunho*"]

        [lojas]          # subpasta ou rótulo -> código da loja
        "DSP-2250" = "2250"

        [marcas]         # subpasta de fotos de embalagem -> marca
        "GL" = "Gama Livre"

        [pastas]         # pastas extras por papel, relativas à raiz
        dados = ["2.DADOS/Outros exports"]
        fotos_gondola = ["Fotos lojas"]
    """
    path = Path(root) / CONFIG_NAME
    if not path.is_file():
        return FolderConfig()
    if tomllib is None:  # pragma: no cover
        raise RuntimeError("Python 3.11 ou mais novo é necessário para ler o jornada_import.toml.")
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    pastas = {}
    for role, folders in (data.get("pastas") or {}).items():
        if role not in CONFIG_ROLES:
            raise ValueError("Papel desconhecido em [pastas] do {}: {}.".format(CONFIG_NAME, role))
        pastas[role] = [str(folder).replace("\\", "/").strip("/") for folder in folders or []]
    return FolderConfig(
        lojas={str(k): str(v) for k, v in (data.get("lojas") or {}).items()},
        marcas={str(k): str(v) for k, v in (data.get("marcas") or {}).items()},
        pastas=pastas,
        ignorar=[str(pattern) for pattern in data.get("ignorar") or []],
    )


# ---------------------------------------------------------------------------
# Classificação
# ---------------------------------------------------------------------------

def _recording_meta(name: str) -> Optional[Dict[str, object]]:
    info = parse_recording_filename(name)
    if not info:
        return None
    return {"participant": info["participant"], "task": info["task"], "store": info["store"],
            "store_label": info.get("store_label", "")}


def _view(folded_name: str) -> str:
    for token, view in _VIEWS:
        if token in folded_name:
            return view
    return ""


def _child_after(parts: Sequence[str], folded_parts: Sequence[str], marker: str) -> str:
    """Nome da pasta logo abaixo da que contém `marker` (ex.: a loja dentro de Fotos Gôndolas)."""
    for index, part in enumerate(folded_parts[:-1]):
        if marker in part and index + 1 < len(parts) - 1:
            return parts[index + 1]
    return ""


def _config_role(rel: PurePosixPath, config: FolderConfig) -> Tuple[str, str]:
    """Papel vindo de [pastas] do toml e a subpasta logo abaixo da pasta apontada."""
    folded_rel = fold(rel.as_posix())
    for role, folders in config.pastas.items():
        for folder in folders:
            prefix = fold(folder).rstrip("/") + "/"
            if folded_rel.startswith(prefix):
                rest = rel.as_posix()[len(prefix):].split("/")
                return role, rest[0] if len(rest) > 1 else ""
    return "", ""


def _image(category: str, **meta) -> Tuple[str, Dict[str, object], str]:
    return "imagem", dict({"category": category}, **{k: v for k, v in meta.items() if v not in ("", None)}), ""


def _classify(rel: PurePosixPath, config: FolderConfig) -> Tuple[str, Dict[str, object], str]:
    parts = list(rel.parts)
    folded_parts = [fold(part) for part in parts]
    folded_dirs = folded_parts[:-1]
    name = rel.name
    folded_name = folded_parts[-1]
    extension = rel.suffix.lower()

    if name.startswith("~$"):
        return "ignorado", {}, "trava temporária do Office"
    if folded_name in _JUNK_NAMES or extension in _JUNK_EXTENSIONS:
        return "ignorado", {}, "arquivo de sistema ou de ferramenta (Blickshift, Windows)"
    for pattern in config.ignorar:
        if fnmatch.fnmatch(fold(rel.as_posix()), fold(pattern)):
            return "ignorado", {}, "excluído pelo {}".format(CONFIG_NAME)
    for folder in folded_dirs:
        for key, reason in _IGNORED_FOLDERS:
            if key in folder:
                return "ignorado", {}, reason

    if extension in (".xlsx", ".xls") and ("relacao coleta" in folded_name or "relacao de coleta" in folded_name):
        return "registro_campo", {}, ""

    role, child = _config_role(rel, config)
    if role:
        return _apply_role(role, child, name, folded_name, extension, config)

    in_23 = any(part.startswith("2.3") for part in folded_dirs)
    in_22 = any(part.startswith("2.2") for part in folded_dirs)
    in_management = any(part.startswith("1.gestao") for part in folded_dirs)
    in_reports = any(part.startswith("3.drafts") for part in folded_dirs)
    in_auxiliary = any(part.startswith("4.arquivos auxiliares") for part in folded_dirs)

    if extension in IMAGE_EXTENSIONS and "heatmap" in folded_name and not in_auxiliary:
        return _image("heatmap", store=store_key(Path(name).stem))
    if in_23:
        if extension in DATA_EXTENSIONS:
            if "convertido" in folded_name:
                return "ignorado", {}, "planilha duplicada do individual (-convertido)"
            return "dados", {}, ""
        return "ignorado", {}, "tipo de arquivo não usado nos consolidados"
    if in_22:
        if any("heatmap" in part for part in folded_dirs):
            return _apply_role("video_heatmap", "", name, folded_name, extension, config)
        if any("eyetracking" in part for part in folded_dirs):
            role = "quadros" if extension == ".csv" else "video_cena"
            return _apply_role(role, "", name, folded_name, extension, config)
        return "ignorado", {}, "pasta de dados processados sem regra"
    if in_management:
        if any(part.startswith("1.1") for part in folded_dirs) and extension in DOCUMENT_EXTENSIONS:
            return "documento", {"doc_type": "briefing"}, ""
        return "ignorado", {}, "planilha de gestão, sem dado de análise"
    if in_reports:
        if any(part.startswith("3.2") for part in folded_dirs) and extension in (".pptx", ".docx", ".pdf"):
            return "documento", {"doc_type": "relatorio"}, ""
        return "ignorado", {}, "rascunho de relatório"
    if in_auxiliary:
        if "fluxo experimental" in folded_name and extension in (".xlsx", ".docx", ".pdf"):
            return "documento", {"doc_type": "briefing"}, ""
        if any("fotos gondola" in part for part in folded_dirs):
            store_folder = _child_after(parts, folded_parts, "fotos gondola")
            return _apply_role("fotos_gondola", store_folder, name, folded_name, extension, config)
        if any("fotos pacote" in part or "fotos embalage" in part for part in folded_dirs):
            marker = "fotos pacote" if any("fotos pacote" in p for p in folded_dirs) else "fotos embalage"
            brand_folder = _child_after(parts, folded_parts, marker)
            return _apply_role("fotos_embalagem", brand_folder, name, folded_name, extension, config)
        if extension in IMAGE_EXTENSIONS:
            return _image("outra")
        return "ignorado", {}, "arquivo auxiliar sem regra"
    return "ignorado", {}, "fora das pastas reconhecidas"


def _apply_role(role: str, child: str, name: str, folded_name: str, extension: str,
                config: FolderConfig) -> Tuple[str, Dict[str, object], str]:
    """Papel decidido (pela estrutura ou pelo toml): confere a extensão e deduz o que o nome diz."""
    if role == "dados":
        return ("dados", {}, "") if extension in DATA_EXTENSIONS else (
            "ignorado", {}, "tipo de arquivo não usado nos exports")
    if role == "registro_campo":
        return ("registro_campo", {}, "") if extension in (".xlsx", ".xls") else (
            "ignorado", {}, "registro de campo precisa ser planilha")
    if role == "documento":
        return ("documento", {"doc_type": "briefing"}, "") if extension in DOCUMENT_EXTENSIONS | {".xlsx"} else (
            "ignorado", {}, "tipo de documento não lido")
    if role in ("quadros", "video_cena", "video_heatmap"):
        wanted = {".csv"} if role == "quadros" else VIDEO_EXTENSIONS
        if extension not in wanted:
            return "ignorado", {}, "tipo de arquivo inesperado na pasta de {}".format(ROLE_LABELS[role].lower())
        meta = _recording_meta(name)
        if meta is None:
            return "ignorado", {}, "nome sem participante, tarefa e loja (ex.: Pt04-JEstimulada-ASSAI)"
        return role, meta, ""
    if role == "fotos_gondola":
        if extension not in IMAGE_EXTENSIONS:
            return "ignorado", {}, "arquivo que não é imagem na pasta de fotos"
        return _image("gondola", store=config.store_for(child) if child else "", store_label=child)
    if role == "fotos_embalagem":
        if extension not in IMAGE_EXTENSIONS:
            return "ignorado", {}, "arquivo que não é imagem na pasta de fotos"
        return _image("embalagem", brand=config.brand_for(child) if child else "", view=_view(folded_name),
                      edited=True if "edit" in folded_name else None)
    return "ignorado", {}, "papel desconhecido"


def _keep_latest_reports(entries: List[Entry]) -> None:
    """Do relatório final só vai a versão mais alta (V0, V1, ... no fim do nome)."""
    groups: Dict[str, List[Tuple[int, Entry]]] = {}
    for entry in entries:
        if entry.role != "documento" or entry.meta.get("doc_type") != "relatorio":
            continue
        stem = fold(PurePosixPath(entry.rel_path).stem)
        match = _VERSION.search(stem)
        if not match:
            continue
        base = stem[: match.start()]
        groups.setdefault(base, []).append((int(match.group(1)), entry))
    for versions in groups.values():
        newest = max(number for number, _ in versions)
        for number, entry in versions:
            if number < newest:
                entry.role, entry.meta = "ignorado", {}
                entry.reason = "versão anterior do relatório (V{}; vai a V{})".format(number, newest)


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
    _keep_latest_reports(entries)
    return entries


def file_limit(role: str) -> int:
    """Maior arquivo que a importação aceita para o papel, em bytes."""
    return FILE_LIMITS.get(role, DEFAULT_FILE_LIMIT)


def summarize(entries: Sequence[Entry]) -> Dict[str, Dict[str, int]]:
    """Quantidade e bytes por papel, na ordem de ROLES."""
    summary = {role: {"files": 0, "bytes": 0} for role in ROLES}
    for entry in entries:
        summary[entry.role]["files"] += 1
        summary[entry.role]["bytes"] += entry.size
    return {role: values for role, values in summary.items() if values["files"]}


def is_probably_template(text: str) -> bool:
    """Documento-modelo sem preencher: pouco texto ou quase só rótulos curtos.

    Calibrado nos modelos da NENC (termo de abertura: 374 caracteres, todas as
    linhas curtas; fluxo experimental: 755 caracteres, 98% de células curtas)
    contra um relatório real (40 mil caracteres, 42% de linhas curtas).
    """
    compact = str(text or "").strip()
    if len(compact) < 600:
        return True
    lines = [line.strip() for line in compact.splitlines() if line.strip()]
    if not lines:
        return True
    short = sum(len(line) <= 40 for line in lines) / len(lines)
    return short >= 0.9 and len(compact) < 3000
