"""
Envia a pasta de um projeto NENC para um módulo do NENC Insights.

Roda no computador de quem tem a pasta (ex.: X:\\ALS\\1234-Estudo), com a
.venv do repositório. Lê a estrutura padrão do módulo, mostra o que vai e o
que fica de fora e envia para a API de importação. No app, o que chegou
aparece em <módulo> → Uploads → Importações pendentes, para revisar e gravar.

Módulos (`--modulo`):

- `jornada_compra` (padrão, o envio de antes; utils/jornada_folder.py):
  exports, quadros, vídeos (compactados com o ffmpeg, quando ele está
  instalado), fotos, registro de campo e o texto dos documentos;
- `teste_sensorial` (utils/sensorial_folder.py): a rodada mais nova de cada
  modalidade do pipeline, o inventário, o registro de campo, as chaves da
  BASE LIMPA (extraídas da planilha aqui, sem as colunas com nome),
  documentos, estímulos e literatura. Tabela CSV acima de 5 MB vai em gzip.

Uso, dentro de nenc-dashboard:

    ..\\.venv\\Scripts\\python scripts\\nenc_enviar.py "X:\\Cliente\\1234 - Estudo" --modulo teste_sensorial --simular
    ..\\.venv\\Scripts\\python scripts\\nenc_enviar.py "X:\\Cliente\\1234 - Estudo" --modulo teste_sensorial

O projeto de destino é o de mesmo nome da pasta, ou o de --projeto (nome ou
id), e precisa existir no app. O token da organização vem da variável
NENC_IMPORT_TOKEN ou de um arquivo (--token-arquivo); nunca o coloque no
repositório nem na linha de comando.

Rodar de novo envia só o que o projeto ainda não tem, e um envio interrompido
continua de onde parou. Os hashes e os arquivos preparados (vídeos
compactados, tabelas comprimidas, chaves da BASE LIMPA) ficam em cache em
%LOCALAPPDATA%\\nenc\\<módulo>_cache.

Opções:
    --modulo MODULO       jornada_compra (padrão) ou teste_sensorial;
    --simular             só mostra o que seria enviado (sem token e sem rede);
    --sem-heatmap         Jornada: não envia os vídeos de heatmap;
    --sem-videos          Jornada: não envia vídeo nenhum;
    --sem-compactar       Jornada: envia os vídeos originais, sem ffmpeg;
    --ffmpeg CAMINHO      Jornada: ffmpeg fora do PATH;
    --url URL             outro servidor (padrão: https://insights.nenc.in);
    -y, --sem-confirmar   não pergunta antes de enviar.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from utils import jornada_folder, sensorial_folder, xlsx_keys  # noqa: E402
from utils.briefing import extract_briefing_text  # noqa: E402
from utils.jornada_folder import Entry, file_limit, is_probably_template, scan_project  # noqa: E402
from utils.jornada_taxonomy import fold  # noqa: E402
from utils.sensorial_ingest import BASE_LIMPA_KEYS, BASE_LIMPA_ROLES  # noqa: E402

DEFAULT_URL = "https://insights.nenc.in"
RETRIES = 3
CLIENT_VERSION = "2"
CHUNK_BYTES = jornada_folder.CHUNK_BYTES
VIDEO_ROLES = ("video_cena", "video_heatmap")
# Os pequenos primeiro: se o envio parar, o que pesa na análise já chegou.
SEND_ORDER = ("registro_campo", "dados", "documento", "imagem", "quadros", "video_cena", "video_heatmap")
DOC_TYPE_LABELS = {"briefing": "briefing", "relatorio": "relatório"}
# Tabela CSV acima disto vai comprimida: o PSD por janela de um estudo passa de 150 MB.
GZIP_MIN_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class Module:
    """Um módulo de destino: rotas da API, papéis da pasta e limites."""

    key: str
    label: str
    api_prefix: str
    role_labels: Dict[str, str]
    send_order: Tuple[str, ...]
    file_limit: Callable[[str], int]
    cache_name: str


JORNADA = Module("jornada_compra", "Jornada de Compra", "/api/jornada", jornada_folder.ROLE_LABELS, SEND_ORDER,
                 jornada_folder.file_limit, "jornada_cache")
SENSORIAL = Module("teste_sensorial", "Teste Sensorial", "/api/importacao/teste_sensorial",
                   sensorial_folder.ROLE_LABELS, sensorial_folder.SEND_ORDER, sensorial_folder.file_limit,
                   "sensorial_cache")
MODULES = {module.key: module for module in (JORNADA, SENSORIAL)}
API_PREFIX = JORNADA.api_prefix


class SendError(RuntimeError):
    """Falha que encerra o envio, com mensagem para quem roda o script."""


@dataclass
class Item:
    """Um arquivo a enviar. O que vai pela rede pode ser outro: vídeo compactado, gzip, chaves ou texto."""

    entry: Entry
    path: Path
    payload: Optional[bytes] = None  # texto extraído de um documento
    payload_path: Optional[Path] = None  # arquivo preparado (vídeo compactado, gzip, chaves)
    meta: Dict[str, object] = field(default_factory=dict)
    send_name: Optional[str] = None  # caminho com que o arquivo preparado chega ao servidor
    prepare: Optional[Callable[[str, Path], Path]] = None  # (sha do original, cache) -> arquivo preparado
    prepare_label: str = ""

    @property
    def role(self) -> str:
        return self.entry.role

    @property
    def send_path(self) -> str:
        return self.send_name or self.entry.rel_path

    def size(self) -> int:
        if self.payload is not None:
            return len(self.payload)
        return (self.payload_path or self.path).stat().st_size


def _size(size: int) -> str:
    if size < 1e6:
        return "{:.0f} KB".format(max(size / 1e3, 1))
    if size < 1e9:
        return "{:.1f} MB".format(size / 1e6).replace(".", ",")
    return "{:.2f} GB".format(size / 1e9).replace(".", ",")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class HashCache:
    """sha256 por caminho, tamanho e data de modificação: reenviar não relê os 6 GB da pasta."""

    def __init__(self, path: Optional[Path]):
        self.path = path
        self.entries: Dict[str, list] = {}
        self.pending = 0
        if path is not None and path.exists():
            try:
                self.entries = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.entries = {}

    def sha256(self, file: Path) -> str:
        stat = file.stat()
        key = str(file.absolute())
        cached = self.entries.get(key)
        if cached and cached[0] == stat.st_size and cached[1] == stat.st_mtime_ns:
            return cached[2]
        value = sha256_file(file)
        self.entries[key] = [stat.st_size, stat.st_mtime_ns, value]
        self.pending += 1
        if self.pending >= 20:
            self.save()
        return value

    def save(self) -> None:
        if self.path is None or not self.pending:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.entries), encoding="utf-8")
        os.replace(temporary, self.path)
        self.pending = 0


def default_cache_dir(module: Module = JORNADA) -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    return Path(base) / "nenc" / module.cache_name


# ---------------------------------------------------------------------------
# O que vai: leitura da pasta, documentos e vídeos
# ---------------------------------------------------------------------------

def _dropped(entry: Entry, reason: str) -> Entry:
    return Entry(entry.rel_path, entry.size, "ignorado", {}, reason)


def _sheet_rows(path: Path) -> List[List[str]]:
    import pandas as pd

    rows = []
    for frame in pd.read_excel(path, sheet_name=None, header=None, dtype=str).values():
        for row in frame.fillna("").values.tolist():
            cells = [str(value).strip() for value in row if str(value).strip()]
            if cells:
                rows.append(cells)
    return rows


def document_text(path: Path) -> str:
    """Texto de um documento: docx, pptx, txt e md pelo extrator do app; planilha linha a linha."""
    extension = path.suffix.lower()
    if extension == ".pdf":
        raise ValueError("o app não extrai texto de PDF; deixe o .docx ou .pptx na pasta")
    if extension in (".xlsx", ".xls"):
        return "\n".join(" | ".join(cells) for cells in _sheet_rows(path))
    text, error = extract_briefing_text(path.name, path.read_bytes())
    if error:
        raise ValueError(error)
    return text


def looks_like_template(path: Path, text: str) -> bool:
    """Modelo sem preencher? Planilha é julgada célula a célula, como na calibração."""
    if path.suffix.lower() in (".xlsx", ".xls"):
        return is_probably_template("\n".join(cell for cells in _sheet_rows(path) for cell in cells))
    return is_probably_template(text)


def collect(root: Path, *, heatmaps: bool = True, videos: bool = True) -> Tuple[List[Item], List[Entry]]:
    """Jornada: itens a enviar e o que fica de fora (com motivo), sem rede e sem compactar nada."""
    items: List[Item] = []
    ignored: List[Entry] = []
    for entry in scan_project(root):
        path = root / entry.rel_path
        if entry.role == "ignorado":
            ignored.append(entry)
        elif entry.role in VIDEO_ROLES and not videos:
            ignored.append(_dropped(entry, "vídeos fora deste envio (--sem-videos)"))
        elif entry.role == "video_heatmap" and not heatmaps:
            ignored.append(_dropped(entry, "heatmaps fora deste envio (--sem-heatmap)"))
        elif entry.role == "documento":
            try:
                text = document_text(path)
            except Exception as error:
                ignored.append(_dropped(entry, "documento sem texto legível: {}".format(str(error)[:120])))
                continue
            if looks_like_template(path, text):
                ignored.append(_dropped(entry, "modelo de documento sem preencher"))
                continue
            payload = text.encode("utf-8")
            if len(payload) > file_limit("documento"):
                ignored.append(_dropped(entry, "texto maior que {} MB".format(file_limit("documento") // 1024 ** 2)))
                continue
            meta = dict(entry.meta, original_name=path.name, original_size=entry.size, text_chars=len(text))
            items.append(Item(entry, path, payload=payload, meta=meta))
        elif entry.role not in VIDEO_ROLES and entry.size > file_limit(entry.role):
            # Vídeo grande ainda pode caber depois de compactado: ele é conferido no envio.
            ignored.append(_dropped(entry, "maior que o limite de {} MB do app".format(
                file_limit(entry.role) // 1024 ** 2)))
        else:
            items.append(Item(entry, path, meta=dict(entry.meta)))
    items.sort(key=lambda item: (SEND_ORDER.index(item.role), item.entry.rel_path))
    return items, ignored


def gzip_file(path: Path, source_sha256: str, cache: Path) -> Path:
    """gzip determinístico (sem nome nem data no cabeçalho), em cache pelo hash do original.

    Os mesmos bytes a cada rodada: um envio retomado continua o arquivo do meio.
    """
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / "{}.gz".format(source_sha256)
    if not target.exists():
        temporary = cache / "{}.part".format(source_sha256)
        with Path(path).open("rb") as source, temporary.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6) as packed:
                shutil.copyfileobj(source, packed, 4 * 1024 * 1024)
        os.replace(temporary, target)
    return target


def base_limpa_keys(path: Path, source_sha256: str, cache: Path, sheet: str, role: str) -> Path:
    """Só as colunas-chave da aba BASE LIMPA, em CSV, em cache pelo hash da planilha.

    As colunas com o nome do participante nunca são lidas (`utils/xlsx_keys.py`).
    """
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / "{}.{}.csv".format(source_sha256, role)
    if not target.exists():
        frame = xlsx_keys.read_columns(path, sheet, BASE_LIMPA_KEYS[role])
        temporary = cache / "{}.part".format(source_sha256)
        frame.to_csv(temporary, index=False, lineterminator="\n")
        os.replace(temporary, target)
    return target


def collect_sensorial(root: Path) -> Tuple[List[Item], List[Entry]]:
    """Teste Sensorial: itens a enviar e o que fica de fora, sem rede e sem preparar nada."""
    items: List[Item] = []
    ignored: List[Entry] = []
    for entry in sensorial_folder.scan_project(root):
        path = root / entry.rel_path
        meta = dict(entry.meta)
        if entry.role == "ignorado":
            ignored.append(entry)
            continue
        if entry.role in BASE_LIMPA_ROLES:
            sheet = str(meta.get("aba") or "")
            stem = entry.rel_path[: -len(PurePosixPath(entry.rel_path).suffix)]
            items.append(Item(
                entry, path, meta=meta, send_name="{}.{}.csv".format(stem, entry.role), prepare_label="só as chaves",
                prepare=lambda sha, cache, p=path, s=sheet, r=entry.role: base_limpa_keys(
                    p, sha, cache / "base_limpa", s, r)))
            continue
        if entry.role == "documento" and path.suffix.lower() in (".docx", ".pptx", ".txt", ".md"):
            try:
                text = document_text(path)
            except Exception:
                text = None
            if text is not None and looks_like_template(path, text):
                ignored.append(_dropped(entry, "modelo de documento sem preencher"))
                continue
        if path.suffix.lower() == ".csv" and entry.size > GZIP_MIN_BYTES:
            items.append(Item(
                entry, path, meta=dict(meta, comprimido=True), send_name=entry.rel_path + ".gz",
                prepare_label="comprimida no envio",
                prepare=lambda sha, cache, p=path: gzip_file(p, sha, cache / "gzip")))
            continue
        limit = sensorial_folder.file_limit(entry.role)
        if entry.size > limit:
            ignored.append(_dropped(entry, "maior que o limite de {} MB do app".format(limit // 1024 ** 2)))
            continue
        items.append(Item(entry, path, meta=meta))
    order = sensorial_folder.SEND_ORDER
    items.sort(key=lambda item: (order.index(item.role), item.entry.rel_path))
    return items, ignored


def print_plan(items: Sequence[Item], ignored: Sequence[Entry], out=print, module: Module = JORNADA) -> None:
    out("")
    out("O que vai para o NENC Insights ({}):".format(module.label))
    total = 0
    width = max([26] + [len(module.role_labels[item.role]) for item in items])
    for role in module.send_order:
        chosen = [item for item in items if item.role == role]
        if chosen:
            size = sum(len(item.payload) if item.payload is not None else item.entry.size for item in chosen)
            total += size
            labels = sorted({item.prepare_label for item in chosen if item.prepare_label})
            note = "  (só o texto)" if role == "documento" and module is JORNADA else (
                "  ({})".format(", ".join(labels)) if labels else "")
            out("  {:<{}} {:>4} arquivo(s)  {:>10}{}".format(module.role_labels[role], width, len(chosen),
                                                             _size(size), note))
    before = "vídeos antes de compactar" if module is JORNADA else "antes de comprimir"
    out("  {:<{}} {:>4} arquivo(s)  {:>10}  ({})".format("Total", width, len(items), _size(total), before))
    for item in items:
        if item.role == "documento":
            detail = " ({} caracteres de texto)".format(item.meta["text_chars"]) if "text_chars" in item.meta else ""
            out("    {}: {}{}".format(DOC_TYPE_LABELS.get(item.meta.get("doc_type"), "documento"),
                                      PurePosixPath(item.entry.rel_path).name, detail))
    out("Fica de fora ({} arquivo(s)):".format(len(ignored)))
    reasons: Dict[str, int] = {}
    for entry in ignored:
        reasons[entry.reason] = reasons.get(entry.reason, 0) + 1
    for reason, count in sorted(reasons.items(), key=lambda pair: (-pair[1], pair[0])):
        out("  {:>4}  {}".format(count, reason))
    out("")


def ignored_payload(ignored: Sequence[Entry], module: Module) -> List[Dict[str, object]]:
    """O que fica de fora, como vai para o servidor.

    No Teste Sensorial vai só a pasta (dois níveis) e o motivo, com a contagem:
    as cópias dos dados brutos (2.0 e 2.1) têm o nome do participante no nome
    de cada arquivo, e o servidor nunca recebe nome.
    """
    if module is JORNADA:
        return [{"rel_path": e.rel_path, "reason": e.reason} for e in ignored]
    groups = Counter(("/".join(PurePosixPath(e.rel_path).parent.parts[:2]), e.reason) for e in ignored)
    return [{"rel_path": folder or "(raiz)", "reason": reason, "arquivos": count}
            for (folder, reason), count in sorted(groups.items())]


def find_ffmpeg(configured: Optional[str] = None) -> Optional[str]:
    if configured:
        return configured if Path(configured).exists() else shutil.which(configured)
    return shutil.which("ffmpeg")


def compress_video(path: Path, source_sha256: str, ffmpeg: str, cache: Path,
                   run: Optional[Callable] = None) -> Optional[Path]:
    """H.264 até 720p, com início rápido para o player; None quando não fica menor.

    A linha do tempo não muda (o salto para a primeira olhada depende dela). O
    resultado fica em cache pelo hash do original, para retomar sem recompactar.
    """
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / "{}.mp4".format(source_sha256)
    if not target.exists():
        temporary = cache / "{}.part.mp4".format(source_sha256)
        command = [
            ffmpeg, "-y", "-loglevel", "error", "-i", str(path),
            "-map", "0:v:0", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
            "-vf", "scale=-2:'min(720,trunc(ih/2)*2)'",
            "-c:a", "aac", "-b:a", "64k",
            "-movflags", "+faststart",
            str(temporary),
        ]
        result = (run or subprocess.run)(command, capture_output=True, text=True)
        if result.returncode != 0 or not temporary.exists():
            temporary.unlink(missing_ok=True)
            raise ValueError("ffmpeg falhou: {}".format((result.stderr or "").strip()[-300:]))
        os.replace(temporary, target)
    if target.stat().st_size >= path.stat().st_size:
        target.unlink(missing_ok=True)
        return None
    return target


# ---------------------------------------------------------------------------
# Rede
# ---------------------------------------------------------------------------

def _detail(response) -> str:
    try:
        return str(response.json().get("detail"))
    except ValueError:
        return response.text[:200]


def _check(response, action: str):
    if response.status_code == 401:
        raise SendError("O servidor recusou o token. Confira NENC_IMPORT_TOKEN (ou --token-arquivo).")
    if response.status_code >= 400:
        raise SendError("{}: {} ({}).".format(action, _detail(response), response.status_code))
    return response.json()


def _loose(text: str) -> str:
    return re.sub(r"[\W_]+", "", fold(text))


def find_project(client, name_or_id: str, prefix: str = API_PREFIX) -> Dict:
    """Projeto da organização do token: pelo id, pelo nome ou pelo nome sem pontuação."""
    projects = _check(client.get(prefix + "/projects"), "Listar os projetos")
    wanted = str(name_or_id).strip()
    for same in (lambda p: str(p["id"]) == wanted, lambda p: fold(p["name"]) == fold(wanted),
                 lambda p: _loose(p["name"]) == _loose(wanted)):
        found = [p for p in projects if same(p)]
        if len(found) == 1:
            return found[0]
    names = "; ".join("{} (id {})".format(p["name"], p["id"]) for p in projects) or "nenhum"
    raise SendError("Projeto \"{}\" não encontrado na organização do token. Crie-o no app ou use "
                    "--projeto com um destes: {}.".format(wanted, names))


def _headers(item: Item, sha256: str, size: int, offset: int, source_sha256: str) -> Dict[str, str]:
    headers = {
        "Content-Type": "application/octet-stream",
        "X-File-Path": quote(item.send_path),
        "X-File-Role": item.role,
        "X-File-Sha256": sha256,
        "X-File-Size": str(size),
        "X-Chunk-Offset": str(offset),
        "X-File-Meta": quote(json.dumps(item.meta, ensure_ascii=False, default=str)),
    }
    if source_sha256:
        headers["X-Source-Sha256"] = source_sha256
    return headers


def upload(client, batch_id: int, item: Item, sha256: str, *, source_sha256: str = "",
           sleep: Callable[[float], None] = time.sleep,
           progress: Optional[Callable[[int, int], None]] = None, prefix: str = API_PREFIX) -> None:
    """Envia um arquivo em blocos de 8 MB.

    Se o arquivo já estava pela metade (envio retomado), o servidor responde
    409 com o offset certo; um bloco repetido é aceito sem duplicar.
    """
    size = item.size()
    handle = None if item.payload is not None else (item.payload_path or item.path).open("rb")
    offset = stalled = 0
    try:
        while offset < size:
            if handle is None:
                chunk = item.payload[offset:offset + CHUNK_BYTES]
            else:
                handle.seek(offset)
                chunk = handle.read(CHUNK_BYTES)
            response = None
            for attempt in range(1, RETRIES + 1):
                try:
                    response = client.put(
                        "{}/imports/{}/files".format(prefix, batch_id), content=chunk,
                        headers=_headers(item, sha256, size, offset, source_sha256),
                    )
                except Exception as error:  # rede instável: espera e tenta de novo
                    if attempt == RETRIES:
                        raise SendError("Falha de rede em {}: {}".format(item.entry.rel_path, error))
                    sleep(2 * attempt)
                    continue
                if response.status_code >= 500 and attempt < RETRIES:
                    sleep(2 * attempt)
                    continue
                break
            previous = offset
            if response.status_code == 409:
                offset = int(response.json().get("expected_offset", offset))
            else:
                offset = int(_check(response, "Enviar {}".format(item.entry.rel_path))["received"])
            stalled = stalled + 1 if offset <= previous else 0
            if stalled >= RETRIES:
                raise SendError("O envio de {} não avança (offset {}).".format(item.entry.rel_path, offset))
            if progress is not None:
                progress(offset, size)
    finally:
        if handle is not None:
            handle.close()


def _http_client(base_url: str, token: str):
    import httpx

    # Um bloco de 8 MB numa conexão lenta passa de um minuto: a leitura espera até 2.
    return httpx.Client(base_url=base_url, headers={"Authorization": "Bearer " + token},
                        timeout=httpx.Timeout(120, connect=15))


# ---------------------------------------------------------------------------
# Comando
# ---------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nenc_enviar", allow_abbrev=False,
        description="Envia a pasta de um projeto NENC para um módulo do NENC Insights.")
    parser.add_argument("pasta", help="pasta do projeto, ex.: X:\\ALS\\1234-Estudo")
    parser.add_argument("--modulo", choices=sorted(MODULES), default=JORNADA.key,
                        help="módulo de destino (padrão: jornada_compra)")
    parser.add_argument("--projeto", help="nome ou id do projeto no app (padrão: o nome da pasta)")
    parser.add_argument("--url", default=os.environ.get("NENC_INSIGHTS_URL", DEFAULT_URL))
    parser.add_argument("--token-arquivo", help="arquivo com o token da organização")
    parser.add_argument("--simular", action="store_true", help="só mostra o que seria enviado")
    parser.add_argument("--sem-heatmap", action="store_true", help="Jornada: não envia os vídeos de heatmap")
    parser.add_argument("--sem-videos", action="store_true", help="Jornada: não envia vídeo nenhum")
    parser.add_argument("--sem-compactar", action="store_true", help="Jornada: envia os vídeos originais")
    parser.add_argument("--ffmpeg", help="Jornada: caminho do ffmpeg, se ele não estiver no PATH")
    parser.add_argument("--cache", help="pasta de cache (padrão: %%LOCALAPPDATA%%\\nenc\\<módulo>_cache)")
    parser.add_argument("-y", "--sem-confirmar", action="store_true", help="não pergunta antes de enviar")
    return parser


def _ask(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        return ""


def run(argv: Optional[Sequence[str]] = None, *, client_factory: Callable = _http_client,
        out: Callable[..., None] = print, ask: Callable[[str], str] = _ask) -> int:
    args = _parser().parse_args(argv)
    module = MODULES[args.modulo]
    root = Path(args.pasta)
    try:
        if module is SENSORIAL:
            items, ignored = collect_sensorial(root)
        else:
            items, ignored = collect(root, heatmaps=not args.sem_heatmap, videos=not args.sem_videos)
    except FileNotFoundError as error:
        out(str(error))
        return 2
    print_plan(items, ignored, out, module)
    if not items:
        out("Nada para enviar desta pasta.")
        return 1
    if args.simular:
        out("Simulação: nada foi enviado.")
        return 0

    token = os.environ.get("NENC_IMPORT_TOKEN", "").strip()
    if args.token_arquivo:
        token = Path(args.token_arquivo).read_text(encoding="utf-8").strip()
    if not token:
        out("Falta o token da organização: defina NENC_IMPORT_TOKEN ou use --token-arquivo.")
        return 2
    ffmpeg = None
    if not args.sem_compactar and any(item.role in VIDEO_ROLES for item in items):
        ffmpeg = find_ffmpeg(args.ffmpeg)
        if ffmpeg is None:
            out("ffmpeg não encontrado: os vídeos vão no tamanho original (veja --ffmpeg).")
    cache = Path(args.cache) if args.cache else default_cache_dir(module)
    hashes = HashCache(cache / "hashes.json")
    base_url = args.url.rstrip("/")
    interactive = out is print and sys.stdout.isatty()
    video_shas: List[str] = []  # para limpar o cache de compactados depois de fechar
    prepared: List[Path] = []  # tabelas comprimidas, idem

    try:
        with client_factory(base_url, token) as client:
            project = find_project(client, args.projeto or root.name, prefix=module.api_prefix)
            question = "Enviar para o projeto \"{}\" ({}) em {}? Só vai o que o projeto ainda não tem. [s/N] ".format(
                project["name"], module.label, base_url)
            if not args.sem_confirmar and ask(question).strip().lower() not in ("s", "sim", "y", "yes"):
                out("Nada foi enviado.")
                return 1
            opened = _check(client.post(module.api_prefix + "/imports", json={
                "project_id": project["id"], "source_label": str(root),
                "client": {"script": "nenc_enviar", "modulo": module.key, "versao": CLIENT_VERSION,
                           "ffmpeg": bool(ffmpeg)},
            }), "Abrir a importação")
            batch_id = int(opened["batch_id"])
            if opened.get("resumed"):
                out("Continuando o envio interrompido (importação {}).".format(batch_id))
            known = opened["known"]
            in_project = set(known.get("files", [])) | set(known.get("media", [])) | set(known.get("documents", []))
            pending = set(known.get("pending", []))

            sent = skipped = waiting = compressed = 0
            for index, item in enumerate(items, 1):
                label = "[{}/{}] {}".format(index, len(items), item.entry.rel_path)
                if item.payload is not None:
                    original_sha = sha256 = hashlib.sha256(item.payload).hexdigest()
                else:
                    original_sha = sha256 = hashes.sha256(item.path)
                if item.role in VIDEO_ROLES:
                    video_shas.append(original_sha)
                if original_sha in in_project:
                    skipped += 1
                    out("{}  já está no projeto".format(label))
                    continue
                if original_sha in pending:
                    waiting += 1
                    out("{}  já enviado, esperando revisão".format(label))
                    continue
                source_sha256 = ""
                if item.role in VIDEO_ROLES:
                    if ffmpeg:
                        out("{}  compactando {}...".format(label, _size(item.entry.size)))
                        try:
                            item.payload_path = compress_video(item.path, original_sha, ffmpeg, cache / "videos")
                        except ValueError as error:
                            out("{}  {}; vai o original".format(label, error))
                    if item.payload_path is not None:
                        sha256, source_sha256 = hashes.sha256(item.payload_path), original_sha
                        item.meta.update(compactado=True, original_size=item.entry.size)
                        compressed += 1
                    if item.size() > module.file_limit(item.role):
                        limit = module.file_limit(item.role) // 1024 ** 2
                        out("{}  fica de fora: {} passa do limite de {} MB".format(label, _size(item.size()), limit))
                        ignored.append(_dropped(item.entry, "vídeo maior que {} MB mesmo compactado".format(limit)))
                        continue
                elif item.prepare is not None:
                    out("{}  preparando ({})...".format(label, item.prepare_label))
                    try:
                        item.payload_path = item.prepare(original_sha, cache)
                    except Exception as error:  # a planilha mudou de formato, falta a aba...
                        out("{}  fica de fora: {}".format(label, error))
                        ignored.append(_dropped(item.entry, "não deu para preparar: {}".format(str(error)[:120])))
                        continue
                    sha256, source_sha256 = hashes.sha256(item.payload_path), original_sha
                    if item.meta.get("comprimido"):
                        prepared.append(item.payload_path)
                    if item.size() > module.file_limit(item.role):
                        limit = module.file_limit(item.role) // 1024 ** 2
                        out("{}  fica de fora: {} passa do limite de {} MB".format(label, _size(item.size()), limit))
                        ignored.append(_dropped(item.entry, "maior que {} MB mesmo preparado".format(limit)))
                        continue

                def progress(done: int, total: int, label: str = label) -> None:
                    if interactive and total > CHUNK_BYTES:
                        print("\r{}  {:>3.0f}%".format(label, 100 * done / total), end="", flush=True)

                upload(client, batch_id, item, sha256, source_sha256=source_sha256, progress=progress,
                       prefix=module.api_prefix)
                sent += 1
                out("{}{}  enviado ({})".format("\r" if interactive else "", label, _size(item.size())))

            summary = {
                "por_papel": dict(Counter(item.role for item in items)),
                "enviados": sent,
                "ja_no_projeto": skipped,
                "ja_pendentes": waiting,
                "videos_compactados": compressed,
                "retomada": bool(opened.get("resumed")),
            }
            closed = _check(client.post("{}/imports/{}/close".format(module.api_prefix, batch_id), json={
                "summary": summary,
                "ignored": ignored_payload(ignored, module),
            }), "Fechar a importação")
    except SendError as error:
        out("Erro: {}".format(error))
        out("Rode o mesmo comando de novo para continuar de onde parou.")
        return 1
    except KeyboardInterrupt:
        out("")
        out("Envio interrompido. Rode o mesmo comando de novo para continuar de onde parou.")
        return 130
    finally:
        hashes.save()

    # Já estão no servidor (inclusive os das rodadas interrompidas): o cache de
    # vídeos compactados e de tabelas comprimidas só servia para retomar.
    for sha in video_shas:
        for name in ("{}.mp4".format(sha), "{}.part.mp4".format(sha)):
            (cache / "videos" / name).unlink(missing_ok=True)
    for path in prepared:
        path.unlink(missing_ok=True)
    out("")
    if closed["status"] != "pronta":
        out("Nada novo: tudo o que a pasta tem já está no projeto ou esperando revisão.")
        return 0
    out("Pronto: {} arquivo(s) enviado(s) agora, {} já estavam no projeto{}.".format(
        sent, skipped, " e {} já tinham sido enviados antes".format(waiting) if waiting else ""))
    out("Revise e grave no app: {} > {} > projeto {} > Uploads > Importações pendentes".format(
        base_url, module.label, project["name"]))
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            # Linha a linha: com a saída num arquivo de log, o progresso aparece na hora.
            stream.reconfigure(errors="replace", line_buffering=True)
        except (AttributeError, ValueError):
            pass
    return run()


if __name__ == "__main__":
    sys.exit(main())
