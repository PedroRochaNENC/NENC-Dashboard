"""
WhatsApp Sync Manager — Processamento de sincronização em segundo plano.

Executa o download de resultados da API, parse de JSON/CSV, verificação de qualidade,
análise de IA e upload para o Vector Store/KB em uma thread separada para não travar
a interface do Streamlit.
"""

import io
import json
import logging
import threading
from datetime import datetime
from typing import Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# Cache global de status dos jobs em segundo plano por project_id
_SYNC_JOBS: Dict[int, Dict] = {}
_SYNC_LOCK = threading.Lock()


def get_sync_job_status(project_id: int) -> Optional[Dict]:
    """Retorna o estado do job de sincronização de um projeto."""
    with _SYNC_LOCK:
        job = _SYNC_JOBS.get(project_id)
        return dict(job) if job else None


def is_sync_running(project_id: int) -> bool:
    """Verifica se há sincronização em andamento para o projeto."""
    job = get_sync_job_status(project_id)
    return bool(job and job.get("status") == "running")


def start_background_sync(project_id: int, organization_id: int) -> bool:
    """Inicia a sincronização do WhatsApp em uma thread de segundo plano."""
    with _SYNC_LOCK:
        job = _SYNC_JOBS.get(project_id)
        if job and job.get("status") == "running":
            return False

        _SYNC_JOBS[project_id] = {
            "status": "running",
            "progress": "Iniciando busca de áudios na API...",
            "completed": 0,
            "total": 0,
            "error": None,
            "started_at": datetime.now(),
            "finished_at": None,
        }

    thread = threading.Thread(
        target=_run_sync_worker,
        args=(project_id, organization_id),
        daemon=True,
    )
    thread.start()
    return True


def _run_sync_worker(project_id: int, organization_id: int) -> None:
    from utils.prosodia_db import (
        get_project,
        get_project_questions,
        get_audios,
        create_audio,
        update_audio_content,
        save_quality_check,
        save_analysis,
        update_audio_openai_ids,
    )
    from utils.whatsapp_api_client import (
        get_project_audios,
        get_audio_status,
        get_audio_result,
        map_api_result_to_all_formats,
        get_campaign_contacts,
        get_all_audios,
    )
    from utils.prosodia_quality import (
        run_quality_checks,
        check_question_coverage_keywords,
        check_question_coverage_ai,
        merge_coverage,
        compute_overall_status,
    )
    from utils.prosodia_prompts import PROSODIA_SYSTEM_PROMPT, build_prosodia_user_prompt
    from utils.ai_provider import (
        get_openai_client,
        get_prosodia_vector_store_id,
        upload_file_to_vector_store,
        create_analysis as ai_create_analysis,
    )
    from utils.prosodia_loader import load_prosodia_from_uploads

    class _BytesFile:
        def __init__(self, data: bytes, name: str):
            self._buf = io.BytesIO(data)
            self.name = name

        def read(self):
            return self._buf.read()

        def seek(self, pos):
            return self._buf.seek(pos)

    def _update_progress(msg: str, completed: int = 0, total: int = 0):
        with _SYNC_LOCK:
            if project_id in _SYNC_JOBS:
                _SYNC_JOBS[project_id]["progress"] = msg
                _SYNC_JOBS[project_id]["completed"] = completed
                _SYNC_JOBS[project_id]["total"] = total

    try:
        project = get_project(project_id, organization_id=organization_id)
        if not project:
            raise ValueError(f"Projeto #{project_id} não encontrado.")

        campaign_id = project.get("whatsapp_campaign_id")
        api_project_id = project.get("api_project_id")

        if api_project_id:
            api_audios_raw = get_project_audios(api_project_id, organization_id=organization_id)
        elif campaign_id:
            contacts = get_campaign_contacts(campaign_id)
            target_phones = {
                "".join(filter(str.isdigit, str(c.get("phone") or "")))
                for c in contacts
                if c.get("phone")
            }
            audios_by_id = {}
            for phone in target_phones:
                for audio in get_all_audios(phone=phone):
                    audios_by_id[str(audio.get("id"))] = audio
            api_audios_raw = list(audios_by_id.values())
        else:
            raise ValueError("Associe este projeto a uma campanha ou projeto da API antes de sincronizar.")

        local_audios = get_audios(project_id, organization_id=organization_id)
        local_audios_by_sid = {a.get("session_id"): a for a in local_audios if a.get("session_id")}

        audios_to_process = []
        for a in api_audios_raw:
            a_id = a["id"]
            wa_msg_id = a.get("whatsapp_message_id")
            phone = a.get("contact_phone", "desconhecido")
            cand_sid = f"wa_{phone}_{a_id}" if wa_msg_id else f"wa_upload_{a_id}"
            qr_name = a.get("qr_code_name") or a.get("qr_code_code")

            if cand_sid not in local_audios_by_sid:
                try:
                    status_info = get_audio_status(a_id)
                    status = status_info.get("status")
                    if status == "done" and status_info.get("has_result_json"):
                        audios_to_process.append({
                            "api_audio": a,
                            "audio_id": None,
                            "is_new": True,
                            "session_id": cand_sid,
                        })
                    elif status in ("pending", "running", "processing"):
                        audio_id = create_audio(
                            project_id=project_id,
                            session_id=cand_sid,
                            prosodia_json=None,
                            transcricao_csv=None,
                            sincronizado_csv=None,
                            whatsapp_message_id=wa_msg_id,
                            qr_code_name=qr_name,
                            organization_id=organization_id,
                        )
                        save_quality_check(
                            audio_id=audio_id,
                            overall_status="processing",
                            checks=[],
                            coverage=[],
                            organization_id=organization_id,
                        )
                    elif status == "failed":
                        audio_id = create_audio(
                            project_id=project_id,
                            session_id=cand_sid,
                            prosodia_json=None,
                            transcricao_csv=None,
                            sincronizado_csv=None,
                            whatsapp_message_id=wa_msg_id,
                            qr_code_name=qr_name,
                            organization_id=organization_id,
                        )
                        save_quality_check(
                            audio_id=audio_id,
                            overall_status="failed",
                            checks=[],
                            coverage=[],
                            organization_id=organization_id,
                        )
                except Exception:
                    pass
            else:
                local_audio = local_audios_by_sid[cand_sid]
                local_status = local_audio.get("quality_status", "pending")
                if local_status in ("processing", "pending", "running", "failed"):
                    try:
                        status_info = get_audio_status(a_id)
                        status = status_info.get("status")
                        if status == "done" and status_info.get("has_result_json"):
                            audios_to_process.append({
                                "api_audio": a,
                                "audio_id": local_audio["id"],
                                "is_new": False,
                                "session_id": cand_sid,
                            })
                        elif status == "failed" and local_status != "failed":
                            save_quality_check(
                                audio_id=local_audio["id"],
                                overall_status="failed",
                                checks=[],
                                coverage=[],
                                organization_id=organization_id,
                            )
                    except Exception:
                        pass

        total_count = len(audios_to_process)
        if total_count == 0:
            with _SYNC_LOCK:
                _SYNC_JOBS[project_id]["status"] = "completed"
                _SYNC_JOBS[project_id]["progress"] = "Nenhum áudio novo pendente para sincronizar."
                _SYNC_JOBS[project_id]["completed"] = 0
                _SYNC_JOBS[project_id]["total"] = 0
                _SYNC_JOBS[project_id]["finished_at"] = datetime.now()
            return

        _update_progress(f"Processando 0/{total_count} áudios...", 0, total_count)

        questions = get_project_questions(project_id, organization_id=organization_id)
        thresholds = None
        if project.get("quality_thresholds"):
            try:
                thresholds = json.loads(project["quality_thresholds"])
            except Exception:
                pass

        openai_client = get_openai_client()
        vs_id = get_prosodia_vector_store_id(organization_id=organization_id)

        for idx, item in enumerate(audios_to_process):
            api_audio = item["api_audio"]
            audio_id = item["audio_id"]
            is_new = item["is_new"]
            session_id = item["session_id"]
            audio_api_id = api_audio["id"]
            wa_msg_id = api_audio.get("whatsapp_message_id")
            qr_name = api_audio.get("qr_code_name") or api_audio.get("qr_code_code")

            _update_progress(f"Baixando resultados de {session_id}...", idx, total_count)
            try:
                result_json = get_audio_result(audio_api_id)
            except Exception as e:
                logger.warning("Falha ao baixar resultado de %s: %s", session_id, e)
                continue

            if not result_json:
                continue

            json_bytes, csv_bytes, sinc_bytes = map_api_result_to_all_formats(result_json, session_id)

            if is_new:
                audio_id = create_audio(
                    project_id=project_id,
                    session_id=session_id,
                    prosodia_json=json_bytes,
                    transcricao_csv=csv_bytes,
                    sincronizado_csv=sinc_bytes,
                    whatsapp_message_id=wa_msg_id,
                    qr_code_name=qr_name,
                    organization_id=organization_id,
                )
            else:
                update_audio_content(
                    audio_id=audio_id,
                    prosodia_json=json_bytes,
                    transcricao_csv=csv_bytes,
                    sincronizado_csv=sinc_bytes,
                    organization_id=organization_id,
                )

            # Upload KB
            file_id_prosodia = None
            file_id_transcricao = None
            if openai_client:
                try:
                    if json_bytes:
                        file_id_prosodia = upload_file_to_vector_store(
                            f"Prosodia-{session_id}.json",
                            json_bytes,
                            mime_type="application/json",
                            vector_store_id=vs_id,
                        )
                    if csv_bytes:
                        file_id_transcricao = upload_file_to_vector_store(
                            f"Transcricao-{session_id}.csv",
                            csv_bytes,
                            mime_type="text/csv",
                            vector_store_id=vs_id,
                        )
                    update_audio_openai_ids(
                        audio_id,
                        file_id_prosodia,
                        file_id_transcricao,
                        organization_id=organization_id,
                    )
                except Exception as e:
                    logger.warning("[%s] Falha no upload para KB: %s", session_id, e)

            # Parse
            json_files = [_BytesFile(json_bytes, f"Prosodia-{session_id}.json")] if json_bytes else []
            csv_files = [_BytesFile(csv_bytes, f"Transcricao-{session_id}.csv")] if csv_bytes else []
            sinc_files = [_BytesFile(sinc_bytes, f"Sincronizado-{session_id}.csv")] if sinc_bytes else []

            parsed = load_prosodia_from_uploads(
                json_files=json_files,
                csv_files=csv_files,
                sincronizado_files=sinc_files,
            )
            vad_df = parsed.get("vad", pd.DataFrame())
            tr_df = parsed.get("transcricao", pd.DataFrame())
            sinc_df = pd.DataFrame()
            if sinc_bytes:
                try:
                    sinc_df = pd.read_csv(io.BytesIO(sinc_bytes))
                except Exception:
                    pass

            # AI Analysis
            _update_progress(f"Gerando análise de IA para {session_id}...", idx, total_count)
            proj_ctx = {
                "nome": project.get("name", ""),
                "especialidade": project.get("especialidade", ""),
                "historico": project.get("historico", ""),
                "problemas": project.get("problemas", ""),
            }

            tables_lines = []
            if not vad_df.empty and "duration" in vad_df.columns:
                total_s = vad_df["duration"].sum()
                n_segs = len(vad_df)
                tables_lines.append(f"VAD: {n_segs} segmentos, {total_s:.1f}s de fala total.")
            if not tr_df.empty and "SpeakerName" in tr_df.columns:
                by_spk = tr_df.groupby("SpeakerName").agg(msgs=("Text", "count"), words=("word_count", "sum")).reset_index()
                tables_lines.append("Participação por locutor:\n" + by_spk.to_string(index=False))

            tables_text = "\n\n".join(tables_lines)
            transcript_sample = " ".join(tr_df["Text"].fillna("").astype(str).tolist())[:3000] if not tr_df.empty and "Text" in tr_df.columns else ""

            analysis_result = {"text": "", "citations": []}
            try:
                if openai_client:
                    user_prompt = build_prosodia_user_prompt(tables_text, proj_ctx, transcript_sample)
                    analysis_result = ai_create_analysis(
                        system_prompt=PROSODIA_SYSTEM_PROMPT,
                        user_prompt=user_prompt,
                        model="gpt-4.1-mini",
                        vector_store_id=vs_id,
                        temperature=0.5,
                        max_tokens=3000,
                    )
            except Exception as e:
                logger.warning("[%s] Falha na análise de IA: %s", session_id, e)

            if analysis_result.get("text"):
                save_analysis(
                    audio_id=audio_id,
                    model="gpt-4.1-mini",
                    analysis_text=analysis_result["text"],
                    citations=analysis_result["citations"],
                    organization_id=organization_id,
                )

            # Quality Check
            quality_checks = run_quality_checks(vad_df, tr_df, sinc_df, thresholds)
            coverage_kw = check_question_coverage_keywords(tr_df, questions)
            coverage_ai = []
            if openai_client and questions and transcript_sample:
                try:
                    coverage_ai = check_question_coverage_ai(transcript_sample, questions, openai_client, model="gpt-4.1-mini")
                except Exception:
                    pass

            coverage_merged = merge_coverage(coverage_kw, coverage_ai) if coverage_ai else coverage_kw
            overall = compute_overall_status(quality_checks)

            save_quality_check(
                audio_id=audio_id,
                overall_status=overall,
                checks=quality_checks,
                coverage=coverage_merged,
                organization_id=organization_id,
            )

            _update_progress(f"{session_id} concluído.", idx + 1, total_count)

        with _SYNC_LOCK:
            _SYNC_JOBS[project_id]["status"] = "completed"
            _SYNC_JOBS[project_id]["progress"] = f"Sincronização concluída! {total_count} áudio(s) processado(s)."
            _SYNC_JOBS[project_id]["completed"] = total_count
            _SYNC_JOBS[project_id]["total"] = total_count
            _SYNC_JOBS[project_id]["finished_at"] = datetime.now()

    except Exception as exc:
        logger.exception("Erro na sincronização em segundo plano do projeto #%d: %s", project_id, exc)
        with _SYNC_LOCK:
            _SYNC_JOBS[project_id]["status"] = "failed"
            _SYNC_JOBS[project_id]["progress"] = f"Falha na sincronização: {exc}"
            _SYNC_JOBS[project_id]["error"] = str(exc)
            _SYNC_JOBS[project_id]["finished_at"] = datetime.now()
