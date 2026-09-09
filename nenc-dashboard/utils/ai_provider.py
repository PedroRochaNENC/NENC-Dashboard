"""
AI Provider — OpenAI client + Responses API with optional file_search.
"""

import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

from utils.organization_data import (
    get_vector_store_id as get_organization_vector_store_id,
    save_vector_store_id as save_organization_vector_store_id,
)

# Load .env — search nenc-dashboard/ first, then workspace root
_ENV_PATH = next(
    (p for p in [
        Path(__file__).resolve().parent.parent / ".env",          # nenc-dashboard/.env
        Path(__file__).resolve().parent.parent.parent / ".env",   # workspace root/.env
    ] if p.exists()),
    Path(__file__).resolve().parent.parent / ".env",
)
load_dotenv(_ENV_PATH)


@st.cache_resource(show_spinner=False)
def get_openai_client() -> OpenAI | None:
    """Return an OpenAI client if the API key is configured, else None.

    Cached because every page and every analysis asks for one, and each call
    used to build a fresh client (and a fresh connection pool) on a framework
    that reruns the whole script at each click. The key is read at startup, so
    changing it in `.env` still requires restarting the app, as the error
    message on the screens already says.
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key or api_key == "sk-proj-YOUR_KEY_HERE":
        return None
    return OpenAI(api_key=api_key)


def get_vector_store_id() -> str | None:
    """Return the Jornada vector store owned by the active organization."""

    return get_organization_vector_store_id(
        "jornada_compra", legacy_environment_key="VECTOR_STORE_ID"
    )


def get_prosodia_vector_store_id() -> str | None:
    """Return the Prosodia vector store owned by the active organization."""

    return get_organization_vector_store_id(
        "prosodia", legacy_environment_key="PROSODIA_VECTOR_STORE_ID"
    )


def save_vector_store_id(vs_id: str) -> None:
    """Persist the Jornada vector store for the active organization."""

    save_organization_vector_store_id("jornada_compra", vs_id)


def save_prosodia_vector_store_id(vs_id: str) -> None:
    """Persist the Prosodia vector store for the active organization."""

    save_organization_vector_store_id("prosodia", vs_id)


# A OpenAI aceita ate 16 atributos por documento, com valor de texto de ate
# 512 caracteres. Sao eles que permitem filtrar a busca depois.
_MAX_DOCUMENT_ATTRIBUTES = 16
_MAX_ATTRIBUTE_LENGTH = 512


def clean_document_attributes(attributes: dict | None) -> dict:
    """Deixa os atributos no formato que a API aceita.

    Campo vazio e descartado: nao filtra nada e ainda ocupa uma das 16 chaves.
    """

    cleaned: dict = {}
    for key, value in (attributes or {}).items():
        if value is None or value == "":
            continue
        if isinstance(value, bool) or isinstance(value, (int, float)):
            cleaned[key] = value
        else:
            trimmed = str(value).strip()
            if not trimmed:
                continue
            cleaned[key] = trimmed[:_MAX_ATTRIBUTE_LENGTH]
        if len(cleaned) == _MAX_DOCUMENT_ATTRIBUTES:
            break
    return cleaned


def add_document_to_vector_store(
    vector_store_id: str,
    filename: str,
    content: bytes,
    attributes: dict | None = None,
    wait: bool = True,
):
    """Envia um documento para a base e espera a indexacao terminar.

    Em duas etapas de proposito: quando o anexo falha, o arquivo recem-criado e
    apagado. Antes ele ficava na conta da OpenAI sem pertencer a base nenhuma —
    invisivel na tela e cobrado do mesmo jeito.

    Devolve o `VectorStoreFile`, ja com o status final da indexacao, em vez de
    deixar a tela dizendo "Processando..." ate alguem recarregar a pagina. Com
    `wait=False` a funcao volta assim que o arquivo entra na fila: e o que os
    fluxos em lote querem, para nao esperar a indexacao de cada entrevista.
    """

    client = get_openai_client()
    if client is None:
        raise RuntimeError("OpenAI API key not configured. Set OPENAI_API_KEY in .env")

    uploaded = client.files.create(file=(filename, content), purpose="assistants")
    attach = (
        client.vector_stores.files.create_and_poll
        if wait
        else client.vector_stores.files.create
    )
    try:
        return attach(
            file_id=uploaded.id,
            vector_store_id=vector_store_id,
            attributes=clean_document_attributes(attributes) or None,
        )
    except Exception:
        try:
            client.files.delete(uploaded.id)
        except Exception:
            pass
        raise


@st.cache_data(ttl=60, show_spinner=False)
def list_vector_store_documents(vector_store_id: str) -> list[dict]:
    """Documentos do vector store, com nome e tamanho ja resolvidos.

    O Streamlit reexecuta a pagina inteira a cada clique, e a listagem antiga
    gastava um `files.retrieve` por documento em cada rerun. Aqui os nomes saem
    de uma unica listagem de arquivos, e o resultado fica em cache: a chave e o
    id do vector store, que ja e por organizacao, entao nada atravessa de uma
    organizacao para outra.

    Chame `list_vector_store_documents.clear()` depois de enviar ou remover um
    documento, senao a tela mostra a base como estava antes da alteracao.
    """

    client = get_openai_client()
    if client is None or not vector_store_id:
        return []

    vector_store_files = list(
        client.vector_stores.files.list(vector_store_id=vector_store_id)
    )
    if not vector_store_files:
        return []

    # Uma listagem resolve o nome de todos: o `files.retrieve` por documento era
    # o gargalo. Se ela falhar, o id ainda identifica a linha na tela.
    try:
        files_by_id = {
            openai_file.id: openai_file
            for openai_file in client.files.list(purpose="assistants", limit=10000)
        }
    except Exception:
        files_by_id = {}

    documents = []
    for vector_store_file in vector_store_files:
        openai_file = files_by_id.get(vector_store_file.id)
        size_bytes = getattr(openai_file, "bytes", None) or 0
        documents.append(
            {
                "id": vector_store_file.id,
                "filename": getattr(openai_file, "filename", "")
                or vector_store_file.id,
                "size_kb": size_bytes / 1024,
                "status": vector_store_file.status,
                "attributes": getattr(vector_store_file, "attributes", None) or {},
            }
        )
    return documents


def create_analysis(
    system_prompt: str,
    user_prompt: str,
    model: str = "gpt-4.1-mini",
    vector_store_id: str | None = None,
    temperature: float = 0.5,
    max_tokens: int = 4000,
    max_search_results: int = 10,
    score_threshold: float = 0.0,
    kb_filter: dict | None = None,
) -> dict:
    """Call OpenAI Responses API with optional file_search.

    Returns:
        dict with keys:
            "text": the generated text
            "citations": one entry per cited document, deduplicated by file id
                ("file_id", "filename", "quote", "score", "index"). "quote" is
                the excerpt the search actually returned, which lives in the
                search result and never in the annotation.
            "search": what the file_search tool did ("searched", "queries",
                "status", "excerpts"). Without it there is no way to tell a
                search that found nothing from a search that never ran.
    """
    client = get_openai_client()
    if client is None:
        raise RuntimeError("OpenAI API key not configured. Set OPENAI_API_KEY in .env")

    request: dict = {
        "model": model,
        "instructions": system_prompt,
        "input": user_prompt,
        "temperature": temperature,
        "max_output_tokens": max_tokens,
    }

    if vector_store_id:
        file_search: dict = {
            "type": "file_search",
            "vector_store_ids": [vector_store_id],
            "max_num_results": max_search_results,
        }
        if score_threshold:
            file_search["ranking_options"] = {"score_threshold": score_threshold}
        if kb_filter:
            # Sem filtro, a base da organizacao inteira responde: a analise de um
            # projeto alcanca a entrevista de outro. Ver utils/kb_attributes.py.
            file_search["filters"] = kb_filter
        request["tools"] = [file_search]
        # Consultar a base e um pedido explicito de quem ligou o botao na tela.
        # No automatico o modelo costuma ignorar a tool quando o prompt de dados
        # e grande, e a analise sai sem nenhuma fonte.
        request["tool_choice"] = {"type": "file_search"}
        # Sem isto a resposta traz apenas o nome do arquivo citado: o trecho e o
        # score vivem no resultado da busca, nao na anotacao do texto.
        request["include"] = ["file_search_call.results"]

    response = client.responses.create(**request)

    # "available" separa "a base estava desligada" de "o modelo ignorou a
    # base": a primeira e uma escolha de quem gerou, a segunda e um defeito.
    search = {
        "available": bool(vector_store_id),
        "searched": False,
        "queries": [],
        "status": "",
        "excerpts": 0,
    }
    excerpt_by_file: dict = {}

    for item in response.output:
        if item.type != "file_search_call":
            continue
        search["searched"] = True
        search["status"] = getattr(item, "status", "")
        for query in getattr(item, "queries", None) or []:
            if query not in search["queries"]:
                search["queries"].append(query)
        for result in getattr(item, "results", None) or []:
            search["excerpts"] += 1
            score = result.score or 0.0
            best = excerpt_by_file.get(result.file_id)
            if best is None or score > best["score"]:
                excerpt_by_file[result.file_id] = {
                    "filename": result.filename or "",
                    "quote": (result.text or "").strip(),
                    "score": score,
                }

    full_text = ""
    citations = []
    cited_files = set()

    for item in response.output:
        if item.type != "message":
            continue
        for content_block in item.content:
            if content_block.type != "output_text":
                continue
            full_text += content_block.text
            for annotation in getattr(content_block, "annotations", None) or []:
                if annotation.type != "file_citation":
                    continue
                file_id = getattr(annotation, "file_id", "")
                # O mesmo documento costuma ser citado varias vezes; a lista de
                # referencias mostra o documento, nao cada ocorrencia dele.
                if file_id in cited_files:
                    continue
                cited_files.add(file_id)
                excerpt = excerpt_by_file.get(file_id, {})
                citations.append(
                    {
                        "file_id": file_id,
                        "index": getattr(annotation, "index", 0),
                        "filename": getattr(annotation, "filename", "")
                        or excerpt.get("filename", ""),
                        "quote": excerpt.get("quote", ""),
                        "score": excerpt.get("score", 0.0),
                    }
                )

    return {"text": full_text, "citations": citations, "search": search}
