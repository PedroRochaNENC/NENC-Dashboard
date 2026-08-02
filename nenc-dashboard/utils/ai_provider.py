"""
AI Provider — OpenAI client + Responses API with optional file_search.
"""

import os
from pathlib import Path

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


def get_openai_client() -> OpenAI | None:
    """Return an OpenAI client if the API key is configured, else None."""
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


def create_analysis(
    system_prompt: str,
    user_prompt: str,
    model: str = "gpt-4.1-mini",
    vector_store_id: str | None = None,
    temperature: float = 0.5,
    max_tokens: int = 4000,
) -> dict:
    """Call OpenAI Responses API with optional file_search.

    Returns:
        dict with keys:
            "text": the generated text (with inline citation markers replaced)
            "citations": list of dicts {"index", "filename", "quote"} if any
    """
    client = get_openai_client()
    if client is None:
        raise RuntimeError("OpenAI API key not configured. Set OPENAI_API_KEY in .env")

    # Build tool list
    tools = []
    if vector_store_id:
        tools.append({
            "type": "file_search",
            "vector_store_ids": [vector_store_id],
        })

    response = client.responses.create(
        model=model,
        instructions=system_prompt,
        input=user_prompt,
        tools=tools if tools else None,
        temperature=temperature,
        max_output_tokens=max_tokens,
    )

    # Extract text and citations from response
    full_text = ""
    citations = []

    for item in response.output:
        if item.type == "message":
            for content_block in item.content:
                if content_block.type == "output_text":
                    full_text += content_block.text
                    # Collect file citations from annotations
                    for ann in getattr(content_block, "annotations", []):
                        if ann.type == "file_citation":
                            citations.append({
                                "index": ann.index,
                                "filename": getattr(ann, "filename", ""),
                                "quote": getattr(ann, "file_citation", {}).get("quote", "") if isinstance(getattr(ann, "file_citation", None), dict) else "",
                            })

    return {"text": full_text, "citations": citations}
