"""Exporta projetos NencLex/Prosódia em tabelas relacionáveis para o Power BI."""

import hashlib
import io
import json
import re
import unicodedata
from typing import Any

import pandas as pd

from utils import prosodia_db
from utils.prosodia_loader import load_prosodia_from_uploads


EXCEL_SAFE_MAX_DATA_ROWS = 1_000_000
EXCEL_SHEET_NAME_MAX_LENGTH = 31
_INVALID_EXCEL_CHARACTERS = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")

_PROJECT_COLUMNS = [
    "id",
    "organization_id",
    "name",
    "especialidade",
    "historico",
    "problemas",
    "questions",
    "entities",
    "briefing_filename",
    "briefing_text",
    "whatsapp_campaign_id",
    "api_project_id",
    "quality_thresholds",
    "created_at",
]
_QUESTION_COLUMNS = ["project_id", "question_index", "question_text"]
_INTERVIEW_COLUMNS = [
    "id",
    "project_id",
    "organization_id",
    "session_id",
    "created_at",
    "openai_file_id_prosodia",
    "openai_file_id_transcricao",
    "whatsapp_message_id",
    "qr_code_name",
    "duration_seconds",
    "quality_status",
    "checks_ok",
    "checks_warn",
    "checks_fail",
    "coverage_total",
    "coverage_ai_found",
    "coverage_kw_found",
    "coverage_ai_pct",
    "coverage_kw_pct",
    "n_analyses",
]
_VAD_COLUMNS = ["audio_id", "project_id", "session_id", "start", "end", "duration"]
_TRANSCRIPT_COLUMNS = [
    "audio_id",
    "project_id",
    "session_id",
    "SpeakerName",
    "Timestamp",
    "seconds",
    "word_count",
    "Text",
]
_INTERVIEW_ANALYSIS_COLUMNS = [
    "id",
    "audio_id",
    "project_id",
    "model",
    "analysis_text",
    "created_at",
]
_INTERVIEW_CITATION_COLUMNS = [
    "analysis_id",
    "audio_id",
    "project_id",
    "citation_index",
    "filename",
    "quote",
    "topic",
    "timestamp",
    "speaker",
    "line_ref",
    "justification",
    "citation_json",
]
_PROJECT_ANALYSIS_COLUMNS = ["id", "project_id", "model", "analysis_text", "created_at"]
_PROJECT_CITATION_COLUMNS = [
    "project_analysis_id",
    "project_id",
    "citation_index",
    "filename",
    "quote",
    "topic",
    "timestamp",
    "speaker",
    "line_ref",
    "justification",
    "citation_json",
]
_QUALITY_COLUMNS = ["id", "audio_id", "project_id", "overall_status", "created_at"]
_QUALITY_CHECK_COLUMNS = [
    "quality_check_id",
    "audio_id",
    "project_id",
    "check_id",
    "title",
    "category",
    "status",
    "message",
    "value_str",
    "details_json",
]
_COVERAGE_COLUMNS = [
    "quality_check_id",
    "audio_id",
    "project_id",
    "question_index",
    "question_text",
    "covered_ai",
    "covered_keywords",
    "confidence",
    "evidence_quote",
    "timestamp",
    "details_json",
]
_ACTIVATION_COLUMNS = [
    "high_activation_id",
    "audio_id",
    "project_id",
    "moment_index",
    "timestamp",
    "timestamp_end",
    "speaker",
    "text",
    "score",
    "reason",
    "created_at",
    "moment_json",
]
_RAW_ARTIFACT_COLUMNS = [
    "audio_id",
    "project_id",
    "session_id",
    "artifact_type",
    "artifact_name",
    "content_size_bytes",
    "content_sha256",
    "content_preview",
    "content_truncated",
    "chunk_count",
]
_RAW_ARTIFACT_CHUNK_COLUMNS = [
    "audio_id",
    "project_id",
    "session_id",
    "artifact_type",
    "artifact_name",
    "chunk_index",
    "chunk_text",
]


class _BytesFile:
    """Adaptador de BLOBs para o carregador existente."""

    def __init__(self, data: bytes, name: str) -> None:
        self._buffer = io.BytesIO(data)
        self.name = name

    def read(self) -> bytes:
        return self._buffer.read()

    def seek(self, position: int) -> int:
        return self._buffer.seek(position)


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _first_value(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return None


def _safe_slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_value).strip("_")
    return slug[:80] or "projeto"


def _frame(rows: list[dict[str, Any]], columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=columns)


def _concat_frames(
    frames: list[pd.DataFrame],
    required_columns: list[str],
) -> pd.DataFrame:
    if not frames:
        return _frame([], required_columns)

    result = pd.concat(frames, ignore_index=True)
    for column in required_columns:
        if column not in result.columns:
            result[column] = None
    additional_columns = [
        column for column in result.columns if column not in required_columns
    ]
    return result[[*required_columns, *additional_columns]]


def _with_identifiers(
    frame: pd.DataFrame,
    audio_id: int,
    project_id: int,
    session_id: str,
) -> pd.DataFrame:
    result = frame.copy()
    identifiers = {
        "audio_id": audio_id,
        "project_id": project_id,
        "session_id": session_id,
    }

    for column in identifiers:
        if column in result.columns:
            source_column = f"source_{column}"
            while source_column in result.columns:
                source_column = f"source_{source_column}"
            result = result.rename(columns={column: source_column})

    for column, value in identifiers.items():
        result[column] = value

    remaining_columns = [
        column for column in result.columns if column not in identifiers
    ]
    return result[[*identifiers, *remaining_columns]]


def _duration_seconds(
    vad_frame: pd.DataFrame,
    transcript_frame: pd.DataFrame,
    synchronized_frame: pd.DataFrame,
) -> float:
    candidates: list[float] = []

    for frame, column in (
        (vad_frame, "end"),
        (transcript_frame, "seconds"),
        (synchronized_frame, "end_s"),
        (synchronized_frame, "seconds"),
        (synchronized_frame, "start_s"),
    ):
        if frame.empty or column not in frame.columns:
            continue
        values = pd.to_numeric(frame[column], errors="coerce").dropna()
        if not values.empty:
            candidates.append(float(values.max()))

    return max(candidates, default=0.0)


def _load_audio_frames(
    audio: dict[str, Any],
    project_id: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    session_id = str(audio.get("session_id") or "")
    parsed = load_prosodia_from_uploads(
        json_files=[
            _BytesFile(audio["prosodia_json"], f"Prosodia-{session_id}.json")
        ]
        if audio.get("prosodia_json")
        else [],
        csv_files=[
            _BytesFile(audio["transcricao_csv"], f"Transcricao-{session_id}.csv")
        ]
        if audio.get("transcricao_csv")
        else [],
        sincronizado_files=[
            _BytesFile(audio["sincronizado_csv"], f"Sincronizado-{session_id}.csv")
        ]
        if audio.get("sincronizado_csv")
        else [],
    )
    parsing_errors = [
        error
        for error in parsed.get("_errors", [])
        if "Erro ao ler" in error or "Erro ao parsear" in error
    ]
    if parsing_errors:
        raise ValueError(
            f"Não foi possível interpretar os dados da entrevista '{session_id}': "
            + " | ".join(parsing_errors)
        )

    vad_frame = _with_identifiers(
        parsed.get("vad", pd.DataFrame()),
        audio["id"],
        project_id,
        session_id,
    )
    transcript_frame = _with_identifiers(
        parsed.get("transcricao", pd.DataFrame()),
        audio["id"],
        project_id,
        session_id,
    )

    synchronized_frame = pd.DataFrame()
    if audio.get("sincronizado_csv"):
        try:
            synchronized_frame = pd.read_csv(
                io.BytesIO(audio["sincronizado_csv"])
            )
        except pd.errors.EmptyDataError:
            synchronized_frame = pd.DataFrame()
        except (pd.errors.ParserError, UnicodeDecodeError) as error:
            raise ValueError(
                f"Não foi possível ler os dados sincronizados da entrevista "
                f"'{session_id}'."
            ) from error

    synchronized_frame = _with_identifiers(
        synchronized_frame,
        audio["id"],
        project_id,
        session_id,
    )
    return vad_frame, transcript_frame, synchronized_frame


def _citations_rows(
    citations: list[Any],
    parent_id_field: str,
    parent_id: int,
    audio_id: int | None,
    project_id: int,
) -> list[dict[str, Any]]:
    rows = []
    for index, citation in enumerate(citations, start=1):
        citation_data = citation if isinstance(citation, dict) else {"quote": str(citation)}
        row = {
            parent_id_field: parent_id,
            "project_id": project_id,
            "citation_index": index,
            "filename": _first_value(citation_data, "filename", "file_name", "source"),
            "quote": _first_value(citation_data, "quote", "text", "content"),
            "topic": _first_value(citation_data, "topic"),
            "timestamp": _first_value(citation_data, "timestamp", "time"),
            "speaker": _first_value(citation_data, "speaker", "SpeakerName"),
            "line_ref": _first_value(citation_data, "line_ref", "line", "reference"),
            "justification": _first_value(citation_data, "justification", "reason"),
            "citation_json": _json_text(citation_data),
        }
        if audio_id is not None:
            row["audio_id"] = audio_id
        rows.append(row)
    return rows


def _quality_detail_rows(
    history: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    quality_rows = []
    check_rows = []
    coverage_rows = []

    for quality in history:
        quality_id = quality["id"]
        audio_id = quality["audio_id"]
        project_id = quality["project_id"]
        quality_rows.append(
            {
                "id": quality_id,
                "audio_id": audio_id,
                "project_id": project_id,
                "overall_status": quality.get("overall_status"),
                "created_at": quality.get("created_at"),
            }
        )

        for check in quality.get("checks", []):
            check_data = check if isinstance(check, dict) else {"message": str(check)}
            raw_value = _first_value(check_data, "value", "value_str")
            check_rows.append(
                {
                    "quality_check_id": quality_id,
                    "audio_id": audio_id,
                    "project_id": project_id,
                    "check_id": _first_value(check_data, "id", "check_id"),
                    "title": _first_value(check_data, "title", "label"),
                    "category": _first_value(check_data, "category"),
                    "status": _first_value(check_data, "status"),
                    "message": _first_value(check_data, "message", "detail"),
                    "value_str": None if raw_value is None else str(raw_value),
                    "details_json": _json_text(check_data),
                }
            )

        for index, coverage in enumerate(quality.get("coverage", []), start=1):
            coverage_data = (
                coverage if isinstance(coverage, dict) else {"question": str(coverage)}
            )
            coverage_rows.append(
                {
                    "quality_check_id": quality_id,
                    "audio_id": audio_id,
                    "project_id": project_id,
                    "question_index": index,
                    "question_text": _first_value(coverage_data, "question", "question_text"),
                    "covered_ai": _first_value(coverage_data, "covered_ai", "covered"),
                    "covered_keywords": _first_value(coverage_data, "covered_keywords"),
                    "confidence": _first_value(coverage_data, "confidence"),
                    "evidence_quote": _first_value(
                        coverage_data, "evidence_quote", "evidence", "quote"
                    ),
                    "timestamp": _first_value(coverage_data, "timestamp", "time"),
                    "details_json": _json_text(coverage_data),
                }
            )

    return quality_rows, check_rows, coverage_rows


def _activation_rows(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for activation in history:
        for index, moment in enumerate(activation.get("moments", []), start=1):
            moment_data = moment if isinstance(moment, dict) else {"text": str(moment)}
            rows.append(
                {
                    "high_activation_id": activation["id"],
                    "audio_id": activation["audio_id"],
                    "project_id": activation["project_id"],
                    "moment_index": index,
                    "timestamp": _first_value(
                        moment_data, "timestamp", "Timestamp", "start", "start_s"
                    ),
                    "timestamp_end": _first_value(
                        moment_data, "timestamp_end", "end", "end_s"
                    ),
                    "speaker": _first_value(
                        moment_data, "speaker", "SpeakerName", "speakers"
                    ),
                    "text": _first_value(
                        moment_data, "text", "Text", "texto_transcricao"
                    ),
                    "score": _first_value(
                        moment_data, "score", "activation_score", "dim_arousal"
                    ),
                    "reason": _first_value(moment_data, "reason", "topic"),
                    "created_at": activation.get("created_at"),
                    "moment_json": _json_text(moment_data),
                }
            )
    return rows


def _interview_quality_metrics(
    quality: dict[str, Any] | None,
) -> dict[str, Any]:
    if quality is None:
        return {
            "quality_status": "pending",
            "checks_ok": 0,
            "checks_warn": 0,
            "checks_fail": 0,
            "coverage_total": 0,
            "coverage_ai_found": 0,
            "coverage_kw_found": 0,
            "coverage_ai_pct": 0.0,
            "coverage_kw_pct": 0.0,
        }

    checks = quality.get("checks", [])
    coverage = quality.get("coverage", [])
    checks_ok = sum(
        check.get("status") == "pass"
        for check in checks
        if isinstance(check, dict)
    )
    checks_warn = sum(
        check.get("status") == "warn"
        for check in checks
        if isinstance(check, dict)
    )
    checks_fail = sum(
        check.get("status") == "fail"
        for check in checks
        if isinstance(check, dict)
    )
    coverage_ai_found = sum(
        item.get("covered_ai") is True for item in coverage if isinstance(item, dict)
    )
    coverage_kw_found = sum(
        item.get("covered_keywords") is True
        for item in coverage
        if isinstance(item, dict)
    )
    coverage_total = len(coverage)
    return {
        "quality_status": quality.get("overall_status") or "pending",
        "checks_ok": checks_ok,
        "checks_warn": checks_warn,
        "checks_fail": checks_fail,
        "coverage_total": coverage_total,
        "coverage_ai_found": coverage_ai_found,
        "coverage_kw_found": coverage_kw_found,
        "coverage_ai_pct": (
            coverage_ai_found / coverage_total * 100.0 if coverage_total else 0.0
        ),
        "coverage_kw_pct": (
            coverage_kw_found / coverage_total * 100.0 if coverage_total else 0.0
        ),
    }


def _table_sheet_name(base_name: str, part: int) -> str:
    if part == 1:
        return base_name[:EXCEL_SHEET_NAME_MAX_LENGTH]
    suffix = f"_{part}"
    return base_name[: EXCEL_SHEET_NAME_MAX_LENGTH - len(suffix)] + suffix


def _excel_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return _json_text(value)
    if isinstance(value, str):
        clean_value = _INVALID_EXCEL_CHARACTERS.sub("", value)
        if clean_value.startswith(("=", "+", "-", "@")):
            return "'" + clean_value
        return clean_value
    return value


def _coerce_bytes(payload: Any) -> bytes:
    if payload is None:
        return b""
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    return str(payload).encode("utf-8", errors="replace")


def _chunk_text(text: str, *, chunk_size: int = 30_000) -> list[str]:
    if not text:
        return []
    return [text[index : index + chunk_size] for index in range(0, len(text), chunk_size)]


def _build_raw_audio_artifact_rows(
    audio: dict[str, Any],
    project_id: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    audio_id = audio["id"]
    session_id = str(audio.get("session_id") or "")
    artifact_specs = [
        ("prosodia_json", "prosodia.json", audio.get("prosodia_json")),
        ("transcricao_csv", "transcricao.csv", audio.get("transcricao_csv")),
        ("sincronizado_csv", "sincronizado.csv", audio.get("sincronizado_csv")),
    ]

    artifact_rows = []
    chunk_rows = []
    for artifact_type, artifact_name, payload in artifact_specs:
        raw_bytes = _coerce_bytes(payload)
        if not raw_bytes:
            continue

        text = raw_bytes.decode("utf-8", errors="replace")
        preview = text[:1000]
        chunks = _chunk_text(text)
        artifact_rows.append(
            {
                "audio_id": audio_id,
                "project_id": project_id,
                "session_id": session_id,
                "artifact_type": artifact_type,
                "artifact_name": artifact_name,
                "content_size_bytes": len(raw_bytes),
                "content_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                "content_preview": preview,
                "content_truncated": len(text) > len(preview),
                "chunk_count": len(chunks),
            }
        )
        for chunk_index, chunk_text in enumerate(chunks, start=1):
            chunk_rows.append(
                {
                    "audio_id": audio_id,
                    "project_id": project_id,
                    "session_id": session_id,
                    "artifact_type": artifact_type,
                    "artifact_name": artifact_name,
                    "chunk_index": chunk_index,
                    "chunk_text": chunk_text,
                }
            )

    return artifact_rows, chunk_rows


def _write_workbook(
    tables: dict[str, pd.DataFrame],
    max_rows_per_sheet: int,
) -> bytes:
    if max_rows_per_sheet < 1:
        raise ValueError("O limite de linhas por aba deve ser maior que zero.")

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for table_name, frame in tables.items():
            safe_frame = frame.copy()
            for column in safe_frame.columns:
                safe_frame[column] = safe_frame[column].map(_excel_value)

            row_count = len(safe_frame)
            part_count = max(1, (row_count + max_rows_per_sheet - 1) // max_rows_per_sheet)
            for part in range(1, part_count + 1):
                start = (part - 1) * max_rows_per_sheet
                end = start + max_rows_per_sheet
                safe_frame.iloc[start:end].to_excel(
                    writer,
                    sheet_name=_table_sheet_name(table_name, part),
                    index=False,
                )
    return output.getvalue()


def export_project_to_powerbi_excel(
    project_id: int,
    *,
    max_rows_per_sheet: int = EXCEL_SAFE_MAX_DATA_ROWS,
) -> tuple[bytes, str]:
    """Gera um workbook Power BI com todo o conteúdo visível do projeto."""
    project = prosodia_db.get_project(project_id)
    if project is None:
        raise ValueError("Projeto não encontrado para a organização ativa.")

    audios = prosodia_db.get_audios(project_id)
    quality_history = prosodia_db.get_quality_checks_history_for_project(project_id)
    activation_history = prosodia_db.get_high_activations_history_for_project(project_id)
    latest_quality_by_audio = {
        quality["audio_id"]: quality for quality in quality_history
    }

    vad_frames = []
    transcript_frames = []
    synchronized_frames = []
    interview_rows = []
    interview_analysis_rows = []
    interview_citation_rows = []
    artifact_rows = []
    artifact_chunk_rows = []

    for audio in audios:
        audio_id = audio["id"]
        vad_frame, transcript_frame, synchronized_frame = _load_audio_frames(
            audio,
            project_id,
        )
        vad_frames.append(vad_frame)
        transcript_frames.append(transcript_frame)
        synchronized_frames.append(synchronized_frame)

        artifact_rows_for_audio, artifact_chunk_rows_for_audio = (
            _build_raw_audio_artifact_rows(audio, project_id)
        )
        artifact_rows.extend(artifact_rows_for_audio)
        artifact_chunk_rows.extend(artifact_chunk_rows_for_audio)

        analyses = prosodia_db.get_analyses(audio_id)
        for analysis in analyses:
            interview_analysis_rows.append(
                {
                    "id": analysis["id"],
                    "audio_id": audio_id,
                    "project_id": project_id,
                    "model": analysis.get("model"),
                    "analysis_text": analysis.get("analysis_text"),
                    "created_at": analysis.get("created_at"),
                }
            )
            interview_citation_rows.extend(
                _citations_rows(
                    analysis.get("citations", []),
                    "analysis_id",
                    analysis["id"],
                    audio_id,
                    project_id,
                )
            )

        interview_row = {
            "id": audio_id,
            "project_id": project_id,
            "organization_id": audio.get("organization_id"),
            "session_id": audio.get("session_id"),
            "created_at": audio.get("created_at"),
            "openai_file_id_prosodia": audio.get("openai_file_id_prosodia"),
            "openai_file_id_transcricao": audio.get("openai_file_id_transcricao"),
            "whatsapp_message_id": audio.get("whatsapp_message_id"),
            "qr_code_name": audio.get("qr_code_name"),
            "duration_seconds": _duration_seconds(
                vad_frame,
                transcript_frame,
                synchronized_frame,
            ),
            "n_analyses": len(analyses),
        }
        interview_row.update(
            _interview_quality_metrics(latest_quality_by_audio.get(audio_id))
        )
        interview_rows.append(interview_row)

    project_analysis_rows = []
    project_citation_rows = []
    for analysis in prosodia_db.get_project_analyses(project_id):
        project_analysis_rows.append(
            {
                "id": analysis["id"],
                "project_id": project_id,
                "model": analysis.get("model"),
                "analysis_text": analysis.get("analysis_text"),
                "created_at": analysis.get("created_at"),
            }
        )
        project_citation_rows.extend(
            _citations_rows(
                analysis.get("citations", []),
                "project_analysis_id",
                analysis["id"],
                None,
                project_id,
            )
        )

    quality_rows, quality_check_rows, coverage_rows = _quality_detail_rows(
        quality_history
    )
    questions_rows = [
        {
            "project_id": project_id,
            "question_index": index,
            "question_text": question,
        }
        for index, question in enumerate(
            prosodia_db.get_project_questions(project_id),
            start=1,
        )
    ]

    tables = {
        "Projeto": _frame([{column: project.get(column) for column in _PROJECT_COLUMNS}], _PROJECT_COLUMNS),
        "Perguntas_Projeto": _frame(questions_rows, _QUESTION_COLUMNS),
        "Entrevistas": _frame(interview_rows, _INTERVIEW_COLUMNS),
        "Segmentos_VAD": _concat_frames(vad_frames, _VAD_COLUMNS),
        "Transcricoes": _concat_frames(transcript_frames, _TRANSCRIPT_COLUMNS),
        "Dados_Sincronizados": _concat_frames(
            synchronized_frames,
            ["audio_id", "project_id", "session_id"],
        ),
        "Analises_Entrevista": _frame(
            interview_analysis_rows,
            _INTERVIEW_ANALYSIS_COLUMNS,
        ),
        "Citacoes_Analise_Entrevista": _frame(
            interview_citation_rows,
            _INTERVIEW_CITATION_COLUMNS,
        ),
        "Analises_Projeto": _frame(
            project_analysis_rows,
            _PROJECT_ANALYSIS_COLUMNS,
        ),
        "Citacoes_Analise_Projeto": _frame(
            project_citation_rows,
            _PROJECT_CITATION_COLUMNS,
        ),
        "Verificacoes_Qualidade": _frame(quality_rows, _QUALITY_COLUMNS),
        "Checks_Qualidade": _frame(quality_check_rows, _QUALITY_CHECK_COLUMNS),
        "Cobertura_Perguntas": _frame(coverage_rows, _COVERAGE_COLUMNS),
        "Momentos_Alta_Ativacao": _frame(
            _activation_rows(activation_history),
            _ACTIVATION_COLUMNS,
        ),
        "Dados_Brutos_Entrevistas": _frame(artifact_rows, _RAW_ARTIFACT_COLUMNS),
        "Chunks_Dados_Brutos_Entrevistas": _frame(
            artifact_chunk_rows,
            _RAW_ARTIFACT_CHUNK_COLUMNS,
        ),
    }

    return (
        _write_workbook(tables, max_rows_per_sheet),
        f"nenclex_powerbi_{_safe_slug(project.get('name', 'projeto'))}_{project_id}.xlsx",
    )
