"""
Prosódia — Áudios.

Tela principal de consulta dos áudios de um projeto:
- tabela robusta com busca e filtros
- métricas de qualidade e cobertura por áudio
- ações de timeline, análise, download e exclusão
"""

import io
import logging
import zipfile

import pandas as pd
import streamlit as st
from utils import auth

_user = auth.require_module("prosodia")
pode_editar = auth.can_write(_user)

from utils import prosodia_overview as overview
from utils import ui
from utils.icons import page_title
from utils.prosodia_db import (
    init_db,
    get_project,
    get_audio,
    get_audios_for_interviews,
    attach_audio_blobs,
    delete_audio,
    mark_project_synced,
)
from utils.prosodia_indice import FAIXAS as FAIXAS_INDICE
from utils.prosodia_indice import atualizar_sem_derrubar as atualizar_indices_sem_derrubar
from utils.prosodia_indice import faixa as faixa_indice
from utils.organization_data import claim_external_resource, list_external_resources
from utils.prosodia_quality import thresholds_for_project

_LOGGER = logging.getLogger(__name__)

init_db()


_STATUS_LABEL = {
    "pass": "OK",
    "warn": "Atenção",
    "fail": "Problema",
    "pending": "Pendente",
    "processing": "Processando",
    "failed": "Falhou",
}


def _status_text(status: str) -> str:
    return _STATUS_LABEL.get(status or "pending", status or "pending")


def _normalize_phone(value) -> str:
    return "".join(filter(str.isdigit, str(value or "")))


def _owned_contact_phones() -> set[str]:
    return {
        _normalize_phone(resource["metadata"].get("phone"))
        for resource in list_external_resources("whatsapp_contact")
        if _normalize_phone(resource["metadata"].get("phone"))
    }


# O seletor de projeto ativo vive em `app.py`, na barra lateral: ele vale
# para todas as paginas do projeto, nao so para esta.
ui.inject_theme()

# ------------------------------------------------------------------
# Verificar projeto selecionado
# ------------------------------------------------------------------
project_id = st.session_state.get("pros_project_id")
if not project_id:
    st.warning("Nenhum projeto selecionado. Volte à lista de projetos.")
    if st.button("← Projetos"):
        st.switch_page("modules/prosodia/projetos.py")
    st.stop()

project = get_project(project_id)
if not project:
    st.session_state.pop("pros_project_id", None)
    st.error("Projeto não encontrado.")
    if st.button("← Projetos"):
        st.switch_page("modules/prosodia/projetos.py")
    st.stop()

# O tipo do projeto escolhe o prompt da IA e os limiares padrão de qualidade.
tipo_projeto = project.get("tipo_projeto")
thresholds = thresholds_for_project(project)

# ------------------------------------------------------------------
# Cabecalho
# ------------------------------------------------------------------
st.page_link("modules/prosodia/resumo.py", label="Voltar para Resumo", icon=":material/arrow_back:")
ui.breadcrumb_nav(
    ("NencBoost", "modules/prosodia/projetos.py"),
    (project["name"], "modules/prosodia/resumo.py"),
    ("Áudios", None),
)
from utils.whatsapp_api_client import is_configured as wa_configured

_no_projeto = get_audios_for_interviews(project_id)
_processando = sum(1 for a in _no_projeto if a.get("quality_status") in ("pending", "processing", "running"))
campaign_id = project.get("whatsapp_campaign_id")
api_project_id = project.get("api_project_id")
sync_clicked = refresh_clicked = False

c_titulo, c_sync = st.columns([3, 2], vertical_alignment="bottom")
with c_titulo:
    page_title("list-bullets", "Áudios", "{} no projeto · {} em processamento na API".format(
        len(_no_projeto), _processando))

# ------------------------------------------------------------------
# Sincronização com WhatsApp API: estado + botão dividido
#
# Os áudios na API chegam via webhook do WhatsApp e NÃO estão
# obrigatoriamente vinculados a uma campanha. A sincronização exige:
# - Um projeto externo pertencente à organização, ou
# - Telefones de contatos registrados em uma campanha pertencente à organização.
# ------------------------------------------------------------------
if wa_configured():
    with c_sync:
        st.markdown(
            '<div style="display:flex;justify-content:flex-end;align-items:center;gap:.45rem;'
            'font-size:.75rem;color:var(--nenc-muted);margin-bottom:.35rem">{}</div>'.format(
                overview.sync_status_html(project.get("last_sync_at"), api_project_id, campaign_id)
            ),
            unsafe_allow_html=True,
        )
        with st.container(horizontal=True, horizontal_alignment="right", gap="xxsmall"):
            sync_clicked = st.button(
                "Sincronizar agora", key="wa_sync_btn", icon=":material/sync:",
                disabled=not (api_project_id or campaign_id),
                help=None if (api_project_id or campaign_id)
                else "Associe uma campanha ou um projeto da API em Dados do Projeto.",
            )
            with st.popover("", icon=":material/expand_more:", help="Mais opções de sincronização"):
                refresh_clicked = st.button(
                    "Atualizar dados da API (sem nova análise)",
                    key="wa_refresh_btn",
                    disabled=not pode_editar,
                    help=(
                        "Baixa de novo o resultado dos áudios já importados e refaz os CSVs: "
                        "alinhamento da fala pelo tempo e sentimento do texto calculado pela API. "
                        "Não reprocessa na API, não gera análise de IA nem refaz a verificação "
                        "de qualidade."
                    ),
                )

if wa_configured():

    if refresh_clicked:
        from utils.prosodia_db import get_audios
        from utils.whatsapp_api_client import atualizar_conteudo_audios_importados

        with st.spinner("Atualizando os áudios importados da API…"):
            resumo = atualizar_conteudo_audios_importados(get_audios(project_id))
            if resumo["atualizados"]:
                atualizar_indices_sem_derrubar(project_id)
        st.cache_data.clear()
        st.success(
            f"{resumo['atualizados']} áudio(s) atualizado(s); "
            f"{resumo['ignorados']} sem resultado novo na API ou fora da sincronização."
        )
        if resumo["falhas"]:
            st.warning(
                "Não foi possível atualizar:\n"
                + "\n".join(f"- {sid}: {erro}" for sid, erro in resumo["falhas"])
            )
        st.caption(
            "As verificações de qualidade e as análises de IA já salvas não mudam; "
            "use Reverificar ou Regenerar na análise do áudio para refazê-las."
        )

    if sync_clicked:
        from utils.whatsapp_api_client import (
            get_audio_result,
            map_api_result_to_all_formats,
            transcricao_para_base_conhecimento,
        )

        try:
            with st.spinner("Buscando áudios na API..."):
                from utils.whatsapp_api_client import get_project_audios, get_audio_status, get_all_audios
                
                if api_project_id:
                    claim_external_resource(
                        "whatsapp_api_project",
                        api_project_id,
                        {"project_id": project_id},
                    )
                    api_audios_raw = get_project_audios(api_project_id)
                elif campaign_id:
                    claim_external_resource(
                        "whatsapp_campaign",
                        campaign_id,
                        {"project_id": project_id},
                    )
                    from utils.whatsapp_api_client import get_campaign_contacts

                    owned_contact_phones = _owned_contact_phones()
                    contacts = get_campaign_contacts(campaign_id)
                    target_phones = {
                        _normalize_phone(contact.get("phone"))
                        for contact in contacts
                        if _normalize_phone(contact.get("phone")) in owned_contact_phones
                    }
                    audios_by_id = {}
                    for phone in target_phones:
                        for audio in get_all_audios(phone=phone):
                            audios_by_id[str(audio.get("id"))] = audio
                    api_audios_raw = list(audios_by_id.values())
                else:
                    raise ValueError(
                        "Associe este projeto a uma campanha ou projeto da API antes de sincronizar."
                    )

                from utils.prosodia_db import get_audios
                local_audios = get_audios(project_id)
                local_audios_by_sid = {a.get("session_id"): a for a in local_audios if a.get("session_id")}

                # Coletar áudios a serem processados (download de resultados e análise)
                audios_to_process = []
                processing_new_count = 0

                for a in api_audios_raw:
                    a_id = a["id"]
                    wa_msg_id = a.get("whatsapp_message_id")
                    if wa_msg_id:
                        phone = a.get("contact_phone", "desconhecido")
                        cand_sid = f"wa_{phone}_{a_id}"
                    else:
                        cand_sid = f"wa_upload_{a_id}"
                    
                    if cand_sid not in local_audios_by_sid:
                        try:
                            status_info = get_audio_status(a_id)
                            status = status_info.get("status")
                            if status == "done" and status_info.get("has_result_json"):
                                audios_to_process.append({
                                    "api_audio": a,
                                    "audio_id": None,
                                    "is_new": True,
                                    "session_id": cand_sid
                                })
                            elif status in ("pending", "running", "processing"):
                                from utils.prosodia_db import create_audio, save_quality_check
                                audio_id = create_audio(
                                    project_id=project_id,
                                    session_id=cand_sid,
                                    prosodia_json=None,
                                    transcricao_csv=None,
                                    sincronizado_csv=None,
                                    whatsapp_message_id=wa_msg_id,
                                    qr_code_name=a.get("qr_code_name") or a.get("qr_code_code"),
                                    received_at=a.get("received_at"),
                                )
                                save_quality_check(
                                    audio_id=audio_id,
                                    overall_status="processing",
                                    checks=[],
                                    coverage=[],
                                )
                                processing_new_count += 1
                            elif status == "failed":
                                from utils.prosodia_db import create_audio, save_quality_check
                                audio_id = create_audio(
                                    project_id=project_id,
                                    session_id=cand_sid,
                                    prosodia_json=None,
                                    transcricao_csv=None,
                                    sincronizado_csv=None,
                                    whatsapp_message_id=wa_msg_id,
                                    qr_code_name=a.get("qr_code_name") or a.get("qr_code_code"),
                                    received_at=a.get("received_at"),
                                )
                                save_quality_check(
                                    audio_id=audio_id,
                                    overall_status="failed",
                                    checks=[],
                                    coverage=[],
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
                                        "session_id": cand_sid
                                    })
                                elif status == "failed" and local_status != "failed":
                                    from utils.prosodia_db import save_quality_check
                                    save_quality_check(
                                        audio_id=local_audio["id"],
                                        overall_status="failed",
                                        checks=[],
                                        coverage=[],
                                    )
                            except Exception:
                                pass

            if not audios_to_process:
                mark_project_synced(project_id)
                if processing_new_count > 0:
                    st.success(f"Sincronizado: {processing_new_count} novo(s) áudio(s) em processamento foram registrados na tabela!")
                    st.rerun()
                else:
                    st.info("Nenhum áudio novo ou concluído para sincronizar.")
            else:
                # Importações necessárias para análise automática
                from utils.prosodia_db import (
                    create_audio,
                    update_audio_content,
                    get_project_questions,
                    save_analysis,
                    save_quality_check,
                    update_audio_openai_ids,
                )
                from utils.prosodia_quality import (
                    run_quality_checks,
                    check_question_coverage_keywords,
                    check_question_coverage_ai,
                    merge_coverage,
                    compute_overall_status,
                )
                from utils.prosodia_prompts import (
                    get_prosodia_system_prompt,
                    build_prosodia_user_prompt,
                )
                from utils.ai_provider import (
                    add_document_to_vector_store,
                    get_openai_client,
                    get_prosodia_vector_store_id,
                    create_analysis as ai_create_analysis,
                )
                from utils.kb_attributes import build_kb_filter, project_document
                import io as _io

                questions = get_project_questions(project_id)
                openai_client = get_openai_client()
                vs_id = get_prosodia_vector_store_id()

                total = len(audios_to_process)
                synced = 0
                progress = st.progress(0, text=f"Sincronizando 0/{total}…")

                for idx, item in enumerate(audios_to_process):
                    api_audio = item["api_audio"]
                    audio_id = item["audio_id"]
                    is_new = item["is_new"]
                    session_id = item["session_id"]
                    audio_api_id = api_audio["id"]
                    wa_msg_id = api_audio.get("whatsapp_message_id")

                    progress.progress(idx / total, text=f"Processando {session_id}…")

                    # Baixar resultado (DevAIce + Whisper)
                    try:
                        result_json = get_audio_result(audio_api_id)
                    except Exception as e:
                        st.warning(f"[{session_id}] Falha ao baixar resultado: {e}")
                        continue

                    if not result_json:
                        st.warning(f"[{session_id}] Resultado vazio, pulando.")
                        continue

                    # Converter para JSON de Prosódia, CSV de Transcrição e Sincronizado
                    json_bytes, csv_bytes, sinc_bytes = map_api_result_to_all_formats(result_json, session_id)

                    # Salvar no banco
                    if is_new:
                        audio_id = create_audio(
                            project_id=project_id,
                            session_id=session_id,
                            prosodia_json=json_bytes,
                            transcricao_csv=csv_bytes,
                            sincronizado_csv=sinc_bytes,
                            whatsapp_message_id=wa_msg_id,
                            qr_code_name=(
                                api_audio.get("qr_code_name") or api_audio.get("qr_code_code")
                            ),
                            received_at=api_audio.get("received_at"),
                        )
                    else:
                        update_audio_content(
                            audio_id=audio_id,
                            prosodia_json=json_bytes,
                            transcricao_csv=csv_bytes,
                            sincronizado_csv=sinc_bytes,
                        )

                    # -- Upload OpenAI KB --
                    file_id_prosodia = None
                    file_id_transcricao = None
                    if openai_client and vs_id:
                        try:
                            if json_bytes:
                                documento = add_document_to_vector_store(
                                    vs_id,
                                    f"Prosodia-{session_id}.json",
                                    json_bytes,
                                    project_document(
                                        "prosodia",
                                        project_id,
                                        session_id=session_id,
                                        tipo="prosodia",
                                    ),
                                    wait=False,
                                )
                                file_id_prosodia = documento.id
                            if csv_bytes:
                                # O file_search da OpenAI nao indexa .csv: sobe a transcricao como texto puro.
                                # Sem as colunas de sentimento: a base cita a fala, nao a inferencia sobre ela.
                                documento = add_document_to_vector_store(
                                    vs_id,
                                    f"Transcricao-{session_id}.txt",
                                    transcricao_para_base_conhecimento(csv_bytes),
                                    project_document(
                                        "prosodia",
                                        project_id,
                                        session_id=session_id,
                                        tipo="transcricao",
                                    ),
                                    wait=False,
                                )
                                file_id_transcricao = documento.id
                            update_audio_openai_ids(audio_id, file_id_prosodia, file_id_transcricao)
                        except Exception as e:
                            st.warning(f"[{session_id}] Falha no upload para KB: {e}")

                    # Parse dos dados para análise usando o loader padrão
                    class _BytesFile:
                        def __init__(self, data: bytes, name: str):
                            self._buf = _io.BytesIO(data)
                            self.name = name

                        def read(self):
                            return self._buf.read()

                        def seek(self, pos):
                            return self._buf.seek(pos)

                    from utils.prosodia_loader import load_prosodia_from_uploads, normalizar_sincronizado
                    from utils.prosodia_signals import montar_evidencias_audio
                    json_files = [_BytesFile(json_bytes, f"Prosodia-{session_id}.json")] if json_bytes else []
                    csv_files = [_BytesFile(csv_bytes, f"Transcricao-{session_id}.csv")] if csv_bytes else []
                    sinc_files = [_BytesFile(sinc_bytes, f"Sincronizado-{session_id}.csv")] if sinc_bytes else []

                    parsed = load_prosodia_from_uploads(
                        json_files=json_files,
                        csv_files=csv_files,
                        sincronizado_files=sinc_files,
                    )
                    vad_df: pd.DataFrame = parsed.get("vad", pd.DataFrame())
                    tr_df: pd.DataFrame = parsed.get("transcricao", pd.DataFrame())

                    sinc_df = pd.DataFrame()
                    if sinc_bytes:
                        try:
                            sinc_df = normalizar_sincronizado(pd.read_csv(_io.BytesIO(sinc_bytes)), session_id)
                        except Exception:
                            # A importacao continua, mas a analise automatica
                            # deste audio nasce sem as metricas acusticas.
                            _LOGGER.exception(
                                "Sincronizado ilegivel na importacao do audio %s.", session_id
                            )
                            st.warning(
                                f"[{session_id}] NencBoost ilegível: a análise "
                                "automática sai sem as métricas acústicas."
                            )

                    # -- Análise automática de IA --
                    proj_ctx = {
                        "nome": project.get("name", ""),
                        "especialidade": project.get("especialidade", ""),
                        "historico": project.get("historico", ""),
                        "problemas": project.get("problemas", ""),
                    }

                    evidencias = montar_evidencias_audio(vad_df, tr_df, sinc_df)
                    transcript_sample = (
                        " ".join(tr_df["Text"].fillna("").astype(str).tolist())[:3000]
                        if not tr_df.empty and "Text" in tr_df.columns
                        else ""
                    )

                    analysis_result = {"text": "", "citations": []}
                    try:
                        if openai_client:
                            user_prompt = build_prosodia_user_prompt(
                                evidencias.tabelas,
                                proj_ctx,
                                transcript_sample,
                                sentimento_texto=evidencias.sentimento_texto,
                                divergencias=evidencias.divergencias,
                            )
                            analysis_result = ai_create_analysis(
                                system_prompt=get_prosodia_system_prompt(tipo_projeto),
                                user_prompt=user_prompt,
                                model="gpt-4.1-mini",
                                vector_store_id=vs_id,
                                kb_filter=build_kb_filter(project_id),
                                temperature=0.5,
                                max_tokens=3000,
                            )
                    except Exception as e:
                        st.warning(f"[{session_id}] Falha na análise de IA: {e}")

                    if analysis_result["text"]:
                        save_analysis(
                            audio_id=audio_id,
                            model="gpt-4.1-mini",
                            analysis_text=analysis_result["text"],
                            citations=analysis_result["citations"],
                        )

                    # -- Verificação de qualidade --
                    quality_checks = run_quality_checks(
                        vad_df, tr_df, sinc_df, thresholds, tipo_projeto=tipo_projeto
                    )
                    coverage_kw = check_question_coverage_keywords(tr_df, questions)
                    coverage_ai = []
                    if openai_client and questions and transcript_sample:
                        try:
                            coverage_ai = check_question_coverage_ai(
                                transcript_sample,
                                questions,
                                openai_client,
                                model="gpt-4.1-mini",
                            )
                        except Exception:
                            pass

                    coverage_merged = (
                        merge_coverage(coverage_kw, coverage_ai)
                        if coverage_ai
                        else coverage_kw
                    )
                    overall = compute_overall_status(quality_checks)

                    save_quality_check(
                        audio_id=audio_id,
                        overall_status=overall,
                        checks=quality_checks,
                        coverage=coverage_merged,
                    )

                    synced += 1
                    progress.progress((idx + 1) / total, text=f"{session_id} concluído.")

                progress.empty()
                if synced:
                    atualizar_indices_sem_derrubar(project_id)
                mark_project_synced(project_id)
                st.success(f"{synced} áudio(s) sincronizado(s) com sucesso!")
                st.rerun()

        except Exception as e:
            st.error(f"Erro durante sincronização: {e}")

# ------------------------------------------------------------------
# Dados
# ------------------------------------------------------------------
audios = get_audios_for_interviews(project_id)

if not audios:
    st.info("Nenhum áudio carregado ainda. Faça upload dos arquivos para começar.")
    if pode_editar and st.button("Ir para Uploads", type="primary"):
        st.switch_page("modules/prosodia/audios.py")
    st.stop()

# A coluna Recebido mostra a chegada na API; a ordem segue ela, não a da
# importação, que junta num horário só tudo o que entrou num mesmo sync.
audios = sorted(audios, key=lambda a: overview.entry_time(a) or pd.Timestamp.min, reverse=True)

# ------------------------------------------------------------------
# Filtros
# ------------------------------------------------------------------
_QUALIDADE = (
    ("todos", "Todos"), ("pass", "Aprovados"), ("warn", "Com alerta"), ("fail", "Com problema"),
    ("processing", "Processando"), ("failed", "Falharam"), ("sem_analise", "Sem análise"),
)


def _estado(audio: dict) -> str:
    status = audio.get("quality_status", "pending")
    return "processing" if status in ("pending", "processing", "running") else status


contagem = {chave: 0 for chave, _ in _QUALIDADE}
for audio in audios:
    contagem["todos"] += 1
    contagem[_estado(audio)] = contagem.get(_estado(audio), 0) + 1
    if _estado(audio) in ("pass", "warn", "fail") and not int(audio.get("n_analyses", 0)):
        contagem["sem_analise"] += 1

min_date, max_date = overview.period_bounds(audios)
f_busca, f_qr, f_periodo, f_indice, f_mais = st.columns([2.4, 1.7, 1.9, 1.6, 1.35], vertical_alignment="bottom")
search = f_busca.text_input(
    "Buscar", placeholder="Número do áudio, sessão ou telefone", key="en_search"
).strip().lower()
filtro_qr = f_qr.selectbox("QR code", ["Todos"] + overview.qr_options(audios), key="en_qr")
periodo = overview.as_period(f_periodo.date_input(
    "Recebido", value=(min_date, max_date), min_value=min_date, max_value=max_date,
    format="DD/MM/YYYY", key="en_date_filter",
))
filtro_indice = f_indice.selectbox(
    "Índice combinado", ("Todos",) + FAIXAS_INDICE + ("Sem índice",), key="en_indice",
    help="Favorável ≥ +0,2; Desfavorável ≤ −0,2; Divergente quando texto e voz passam de 0,2 "
         "em sentidos opostos. A voz é medida em relação a todos os áudios do projeto.",
)
with f_mais:
    with st.popover("Mais filtros", width="stretch"):
        ai_range = st.slider("Cobertura IA (%)", 0, 100, (0, 100), key="en_ai_range")
        kw_range = st.slider("Cobertura keywords (%)", 0, 100, (0, 100), key="en_kw_range")

filtro_qualidade = st.pills(
    "Qualidade",
    [chave for chave, _ in _QUALIDADE],
    format_func=lambda chave: "{} {}".format(dict(_QUALIDADE)[chave], contagem.get(chave, 0)),
    default="todos",
    key="en_quality",
    label_visibility="collapsed",
) or "todos"

filtered = []
for audio in audios:
    sid = str(audio.get("session_id", ""))
    if search and not any(search in campo for campo in (
        sid.lower(), str(audio.get("id", "")), str(overview.api_audio_id(sid) or ""),
    )):
        continue
    if filtro_qr != "Todos" and overview.qr_label(audio) != filtro_qr:
        continue
    if filtro_indice != "Todos":
        faixa = faixa_indice(audio.get("indice_combinado"), audio.get("indice_texto"), audio.get("indice_voz"))
        if (faixa or "Sem índice") != filtro_indice:
            continue
    if periodo:
        dia = overview.entry_date(audio)
        if dia is None or dia < periodo[0] or dia > periodo[1]:
            continue
    if filtro_qualidade == "sem_analise":
        if _estado(audio) not in ("pass", "warn", "fail") or int(audio.get("n_analyses", 0)):
            continue
    elif filtro_qualidade != "todos" and _estado(audio) != filtro_qualidade:
        continue
    if not (ai_range[0] <= float(audio.get("coverage_ai_pct", 0.0)) <= ai_range[1]):
        continue
    if not (kw_range[0] <= float(audio.get("coverage_kw_pct", 0.0)) <= kw_range[1]):
        continue
    filtered.append(audio)

chosen = []
# As setas ‹ › da página do áudio seguem esta ordem, com os filtros atuais.
st.session_state["en_filtered_ids"] = [a["id"] for a in filtered]
st.caption(f"{len(filtered)} áudio(s) encontrado(s) de {len(audios)} no projeto.")

# ------------------------------------------------------------------
# Tabela
# ------------------------------------------------------------------
selected_audio = None

if not filtered:
    st.warning("Nenhum áudio atende aos filtros selecionados.")
else:
    rows = []
    for a in filtered:
        status = a.get("quality_status", "pending")
        is_processing = status in ("pending", "processing", "running")
        is_failed = status == "failed"
        # Os tres contadores de checks viram um selo unico de qualidade; o
        # detalhe continua na aba Qualidade do audio.
        quality = _STATUS_LABEL.get(status, status)
        if not (is_processing or is_failed):
            warn = int(a.get("checks_warn", 0))
            fail = int(a.get("checks_fail", 0))
            quality = (
                "{} problema(s)".format(fail) if fail
                else "{} alerta(s)".format(warn) if warn
                else "Aprovado"
            )
        session_id = str(a.get("session_id", ""))
        pronto = not (is_processing or is_failed)
        rows.append({
            "Áudio": "#{}".format(overview.api_audio_id(session_id) or a.get("id")),
            "Telefone": overview.masked_phone(session_id),
            "QR code": overview.qr_label(a),
            "Recebido": overview.entry_text(a),
            "Duração": a.get("duration_str", "00:00") if pronto else None,
            "Índice": a.get("indice_combinado") if pronto else None,
            "Qualidade": quality,
            "Cobertura": float(a.get("coverage_ai_pct", 0.0)) / 100 if pronto else None,
            "Análise": ("feita" if int(a.get("n_analyses", 0)) else "pendente") if pronto else "—",
        })

    table_event = st.dataframe(
        pd.DataFrame(rows),
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row",
        key="en_interviews_table",
        column_config={
            "Índice": st.column_config.NumberColumn(
                "Índice", format="%+.2f",
                help="Índice combinado do áudio, de −1 a +1: metade o texto, metade a voz "
                     "em relação a todos os áudios do projeto.",
            ),
            "Cobertura": st.column_config.ProgressColumn(
                "Cobertura", format="percent", min_value=0, max_value=1,
                help="Perguntas do roteiro cobertas, pela IA. Palavras-chave em Mais filtros.",
            ),
        },
    )

    selection = getattr(table_event, "selection", None) if table_event else None
    selected_rows = (
        selection.get("rows", []) if isinstance(selection, dict)
        else (getattr(selection, "rows", []) or []) if selection is not None
        else []
    )
    chosen = [filtered[int(i)] for i in selected_rows if 0 <= int(i) < len(filtered)]
    if len(chosen) == 1:
        selected_audio = chosen[0]
    elif len(chosen) > 1:

        def _pacote(escolhidos: list) -> bytes:
            """Os arquivos de cada áudio, com os nomes que o loader reconhece."""
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as pacote:
                for item in attach_audio_blobs(project_id, escolhidos):
                    sid = item.get("session_id", "sessao")
                    for campo, nome in (
                        ("prosodia_json", "NencLex-{}.json"),
                        ("transcricao_csv", "Transcricao-{}.csv"),
                        ("sincronizado_csv", "Sincronizado-{}.csv"),
                    ):
                        if item.get(campo):
                            pacote.writestr(nome.format(sid), item[campo])
            return buffer.getvalue()

        with st.container(border=True):
            b_info, b_zip = st.columns([3, 1.4], vertical_alignment="center")
            b_info.markdown("**{} áudios selecionados**".format(len(chosen)))
            b_zip.download_button(
                "Baixar arquivos (.zip)", data=_pacote(chosen),
                file_name="audios_{}.zip".format(project_id), mime="application/zip",
                width="stretch", key="en_bulk_zip",
            )
            st.caption("Abrir, reprocessar e excluir valem para um áudio por vez: deixe só um selecionado.")

# ------------------------------------------------------------------
# Ações da linha selecionada
# ------------------------------------------------------------------
st.markdown("")

if not selected_audio:
    if len(chosen) < 2:
        st.info("Selecione um áudio na tabela para abrir, reprocessar ou excluir.")
else:
    selected_id = selected_audio["id"]

    st.caption(
        f"Áudio selecionado: {selected_audio.get('session_id', '')} "
        f"({str(selected_audio.get('created_at', ''))[:10]})"
    )

    is_wa = str(selected_audio.get("session_id", "")).startswith("wa_")
    if is_wa:
        ac1, ac3, ac4 = st.columns(3)
    else:
        ac1, ac4 = st.columns(2)
        ac3 = None

    status = selected_audio.get("quality_status", "pending")
    is_processing = status in ("pending", "processing", "running")
    is_failed = status == "failed"

    with ac1:
        # O st.dataframe não navega pelo clique na linha: com uma linha
        # selecionada, abrir o áudio é a primeira ação.
        if st.button(
            "Abrir áudio", type="primary", width="stretch", key=f"en_open_{selected_id}",
            disabled=is_processing or is_failed,
            help="O áudio abre assim que o processamento for concluído." if (is_processing or is_failed) else None,
        ):
            st.session_state["pros_audio_id"] = selected_id
            st.session_state.pop("pros_timeline_focus", None)
            # Cruza para o nivel do audio: a pagina so entra no menu no rerun
            # seguinte, entao o salto passa por `_navigate_to`.
            st.session_state["_navigate_to"] = "modules/prosodia/audio.py"
            st.rerun()
            
    if is_wa and ac3 is not None:
        with ac3:
            if st.button("Reprocessar", width="stretch", key=f"en_reproc_{selected_id}", help="Solicitar reprocessamento da transcrição e NencBoost via WhatsApp API"):
                parts = selected_audio.get("session_id", "").split("_")
                if len(parts) >= 3:
                    try:
                        audio_api_id = int(parts[-1])
                        
                        # 1. Enviar requisição de reprocessamento
                        with st.spinner("Solicitando reprocessamento na API..."):
                            from utils.whatsapp_api_client import reprocess_audio as api_reprocess_audio, get_audio_status, get_audio_result, map_api_result_to_all_formats
                            api_reprocess_audio(audio_api_id)
                        
                        # 2. Polling status
                        import time
                        status_container = st.empty()
                        
                        start_time = time.time()
                        timeout = 300  # 5 minutos
                        success = False
                        
                        while time.time() - start_time < timeout:
                            status_info = get_audio_status(audio_api_id)
                            job_status = status_info.get("status", "pending")
                            
                            if job_status == "done":
                                success = True
                                break
                            elif job_status == "failed":
                                st.error(f"Erro no processamento da API: {status_info.get('error_msg') or 'Falha desconhecida'}")
                                break
                            
                            status_container.info(f"Processando na API (status: {job_status.upper()}). Por favor, aguarde...")
                            time.sleep(3)
                        
                        if success:
                            status_container.success("Processamento na API concluído! Atualizando dados locais...")
                            
                            # 3. Baixar resultados
                            result_json = get_audio_result(audio_api_id)
                            if result_json:
                                json_bytes, csv_bytes, sinc_bytes = map_api_result_to_all_formats(result_json, selected_audio["session_id"])
                                
                                # 4. Atualizar blobs locais no SQLite
                                from utils.prosodia_db import update_audio_content
                                update_audio_content(selected_id, json_bytes, csv_bytes, sinc_bytes)
                                atualizar_indices_sem_derrubar(project_id)

                                # 5. Limpar cache do Streamlit
                                st.cache_data.clear()
                                
                                # 6. Recarregar dados locais
                                import pandas as pd
                                import io
                                from utils.prosodia_loader import load_prosodia_from_uploads, normalizar_sincronizado
                                from utils.prosodia_signals import montar_evidencias_audio
                                class _BF:
                                    def __init__(self, data, name):
                                        self._buf = io.BytesIO(data)
                                        self.name = name
                                    def read(self): return self._buf.read()
                                    def seek(self, p): return self._buf.seek(p)
                                
                                parsed_new = load_prosodia_from_uploads(
                                    json_files=[_BF(json_bytes, f"Prosodia-{selected_audio['session_id']}.json")] if json_bytes else [],
                                    csv_files=[_BF(csv_bytes, f"Transcricao-{selected_audio['session_id']}.csv")] if csv_bytes else [],
                                    sincronizado_files=[_BF(sinc_bytes, f"Sincronizado-{selected_audio['session_id']}.csv")] if sinc_bytes else [],
                                )
                                new_vad_df = parsed_new.get("vad", pd.DataFrame())
                                new_tr_df = parsed_new.get("transcricao", pd.DataFrame())
                                new_sinc_df = pd.DataFrame()
                                if sinc_bytes:
                                    try:
                                        new_sinc_df = normalizar_sincronizado(
                                            pd.read_csv(io.BytesIO(sinc_bytes)), selected_audio["session_id"]
                                        )
                                    except Exception:
                                        _LOGGER.exception(
                                            "Sincronizado ilegivel no reprocessamento do audio %s.",
                                            selected_audio["session_id"],
                                        )
                                        st.warning(
                                            "O NencBoost reprocessado veio ilegível; a "
                                            "análise sai sem as métricas acústicas."
                                        )
                                
                                new_transcript_text = " ".join(new_tr_df["Text"].fillna("").astype(str).tolist()) if not new_tr_df.empty and "Text" in new_tr_df.columns else ""
                                
                                # 7. Atualizar Qualidade
                                status_container.info("Atualizando verificação de qualidade...")
                                from utils.prosodia_db import get_project_questions, save_quality_check, save_analysis
                                from utils.prosodia_quality import run_quality_checks, check_question_coverage_keywords, check_question_coverage_ai, merge_coverage, compute_overall_status
                                from utils.prosodia_prompts import get_prosodia_system_prompt, build_prosodia_user_prompt
                                from utils.ai_provider import (
                                    add_document_to_vector_store,
                                    get_openai_client,
                                    get_prosodia_vector_store_id,
                                    create_analysis as ai_create_analysis,
                                )
                                from utils.kb_attributes import (
                                    build_kb_filter,
                                    project_document,
                                )
                                
                                questions = get_project_questions(project_id)
                                openai_client = get_openai_client()
                                vs_id = get_prosodia_vector_store_id()
                                
                                new_checks = run_quality_checks(new_vad_df, new_tr_df, new_sinc_df if not new_sinc_df.empty else None, thresholds, tipo_projeto=tipo_projeto)
                                cov_kw = check_question_coverage_keywords(new_tr_df, questions)
                                cov_ai = []
                                if openai_client and questions and new_transcript_text:
                                    cov_ai = check_question_coverage_ai(new_transcript_text, questions, openai_client, model="gpt-4.1-mini")
                                cov_merged = merge_coverage(cov_kw, cov_ai) if cov_ai else cov_kw
                                new_overall = compute_overall_status(new_checks)
                                save_quality_check(selected_id, new_overall, new_checks, cov_merged)
                                
                                # Enviar nova qualidade para KB
                                if openai_client and vs_id:
                                    try:
                                        n_pass = sum(1 for c in new_checks if c.get("status") == "pass")
                                        n_warn = sum(1 for c in new_checks if c.get("status") == "warn")
                                        n_fail = sum(1 for c in new_checks if c.get("status") == "fail")
                                        
                                        quality_md = (
                                            f"# Verificação de Qualidade — Prosódia\n\n"
                                            f"- Sessão: {selected_audio['session_id']}\n"
                                            f"- Projeto: {project.get('name', '')}\n"
                                            f"- Status geral: {new_overall}\n\n"
                                            f"## Resumo\n\n"
                                            f"- Checks OK: {n_pass}\n- Alertas: {n_warn}\n- Problemas: {n_fail}\n"
                                        )
                                        q_name = f"qualidade_entrevista_{selected_audio['session_id']}.md"
                                        add_document_to_vector_store(
                                            vs_id,
                                            q_name,
                                            quality_md.encode("utf-8"),
                                            project_document(
                                                "prosodia",
                                                project_id,
                                                escopo="analise",
                                                session_id=selected_audio["session_id"],
                                                tipo="qualidade",
                                            ),
                                            wait=False,
                                        )
                                    except Exception:
                                        pass
                                
                                # 8. Atualizar Análise de IA
                                status_container.info("Atualizando análise de IA...")
                                # Antes o texto de participação sobrescrevia o do VAD e a
                                # IA recebia só uma das tabelas.
                                new_evidencias = montar_evidencias_audio(new_vad_df, new_tr_df, new_sinc_df)
                                
                                proj_ctx = {
                                    "nome": project.get("name", ""),
                                    "especialidade": project.get("especialidade", ""),
                                    "historico": project.get("historico", ""),
                                    "problemas": project.get("problemas", ""),
                                }
                                user_prompt = build_prosodia_user_prompt(
                                    new_evidencias.tabelas,
                                    proj_ctx,
                                    new_transcript_text[:3000],
                                    sentimento_texto=new_evidencias.sentimento_texto,
                                    divergencias=new_evidencias.divergencias,
                                )
                                
                                # Chamar IA
                                result_ai = ai_create_analysis(
                                    system_prompt=get_prosodia_system_prompt(tipo_projeto),
                                    user_prompt=user_prompt,
                                    model="gpt-4.1-mini",
                                    vector_store_id=vs_id,
                                    kb_filter=build_kb_filter(project_id),
                                    temperature=0.5,
                                    max_tokens=3000,
                                )
                                save_analysis(selected_id, "gpt-4.1-mini", result_ai["text"], result_ai.get("citations", []))
                                
                                # Enviar nova análise para KB
                                if openai_client and vs_id:
                                    try:
                                        analysis_md = (
                                            f"# Análise de IA — NencBoost\n\n"
                                            f"- Sessão: {selected_audio['session_id']}\n"
                                            f"- Projeto: {project.get('name', '')}\n"
                                            f"- Modelo: gpt-4.1-mini\n\n"
                                            f"## Resultado\n\n{result_ai['text']}"
                                        )
                                        a_name = f"analise_ia_{selected_audio['session_id']}.md"
                                        add_document_to_vector_store(
                                            vs_id,
                                            a_name,
                                            analysis_md.encode("utf-8"),
                                            project_document(
                                                "prosodia",
                                                project_id,
                                                escopo="analise",
                                                session_id=selected_audio["session_id"],
                                                tipo="analise_ia",
                                            ),
                                            wait=False,
                                        )
                                    except Exception:
                                        pass
                                
                                status_container.success("Áudio, transcrição, NencBoost e análise reprocessados com sucesso!")
                                time.sleep(2)
                                st.rerun()
                            else:
                                st.error("Erro ao baixar o resultado do processamento da API.")
                    except Exception as e:
                        st.error(f"Ocorreu um erro no reprocessamento: {e}")
                        
    with ac4:
        if pode_editar and st.button("Excluir", width="stretch", key=f"en_del_{selected_id}"):
            st.session_state[f"confirm_del_interview_{selected_id}"] = True

    if st.session_state.get(f"confirm_del_interview_{selected_id}"):
        from utils.prosodia_db import get_audio_deletion_reference
        from utils.whatsapp_api_client import (
            ApiAudioDeletion,
            api_audio_id_from_session,
            delete_api_audio,
            plan_api_audio_deletion,
        )

        sessao = selected_audio.get("session_id", "")
        # O plano sai antes da confirmacao, para a tela dizer se o audio na API
        # vai junto. Sem resposta da API nao ha confirmacao: excluir so a
        # entrevista a deixaria voltar na proxima sincronizacao.
        plano = ApiAudioDeletion(None, False)
        erro_api = None
        referencia = get_audio_deletion_reference(
            selected_id, api_audio_id_from_session(sessao)
        )
        if referencia is not None:
            try:
                plano = plan_api_audio_deletion(referencia, project.get("api_project_id"))
            except Exception as e:
                erro_api = e

        if erro_api is not None:
            st.error(
                f"Não foi possível consultar o áudio na API ({erro_api}). "
                "O áudio não será excluído daqui sem saber se a gravação na API sai junto; tente de novo."
            )
        elif plano.delete_in_api:
            st.warning(
                f"Excluir o áudio **{sessao}** daqui e também da API "
                "(gravação, transcrição e resultado da análise)? Esta ação não pode ser desfeita."
            )
        else:
            aviso = f"Excluir o áudio **{sessao}**? Esta ação não pode ser desfeita."
            if plano.api_audio_id is not None:
                aviso += f"\n\nA gravação na API não será excluída: {plano.reason}"
            st.warning(aviso)
        dc1, dc2 = st.columns(2)
        with dc1:
            if erro_api is None and st.button(
                "Confirmar exclusão", width="stretch", key=f"en_del_yes_{selected_id}"
            ):
                try:
                    if plano.delete_in_api:
                        delete_api_audio(plano.api_audio_id)
                except Exception as e:
                    st.error(f"Falha ao excluir a gravação na API ({e}). O áudio não foi excluído.")
                else:
                    delete_audio(selected_id)
                    atualizar_indices_sem_derrubar(project_id)
                    st.session_state.pop(f"confirm_del_interview_{selected_id}", None)
                    st.session_state.pop("en_interviews_table", None)
                    if st.session_state.get("pros_audio_id") == selected_id:
                        st.session_state.pop("pros_audio_id", None)
                    st.rerun()
        with dc2:
            if st.button("Cancelar", width="stretch", key=f"en_del_no_{selected_id}"):
                st.session_state.pop(f"confirm_del_interview_{selected_id}", None)
                st.rerun()

    st.divider()
    # A tabela carrega so metadado; o conteudo vem do audio selecionado.
    selected_content = get_audio(selected_id) or {}
    d1, d2 = st.columns(2)
    with d1:
        if selected_content.get("prosodia_json"):
            st.download_button(
                "Baixar NencBoost (JSON)",
                data=selected_content["prosodia_json"],
                file_name=f"NencLex-{selected_audio.get('session_id', 'sessao')}.json",
                mime="application/json",
                width="stretch",
                key=f"en_dl_json_{selected_id}",
            )
    with d2:
        if selected_content.get("transcricao_csv"):
            st.download_button(
                "Baixar transcrição (CSV)",
                data=selected_content["transcricao_csv"],
                file_name=f"Transcricao-{selected_audio.get('session_id', 'sessao')}.csv",
                mime="text/csv",
                width="stretch",
                key=f"en_dl_csv_{selected_id}",
            )
