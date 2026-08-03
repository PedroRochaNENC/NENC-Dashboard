"""
Prosódia — Dados do Projeto.

Formulário de criação/edição de um projeto: nome, contexto e perguntas da entrevista.
As perguntas serão usadas na verificação automática de qualidade de cada áudio.
"""

import io
import zipfile
import xml.etree.ElementTree as ET
import json
from datetime import datetime

import streamlit as st
from utils import auth

user = auth.require_module("prosodia")

from utils.prosodia_db import (
    init_db,
    create_project,
    get_project,
    update_project,
)
from utils.ai_provider import get_openai_client, get_prosodia_vector_store_id
from utils.organization_data import claim_external_resource, list_external_resources

init_db()


def _decode_text_bytes(data: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return data.decode(enc)
        except Exception:
            continue
    return data.decode("utf-8", errors="ignore")


def _extract_text_from_docx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml_bytes = zf.read("word/document.xml")

    root = ET.fromstring(xml_bytes)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = []
    for p in root.findall(".//w:p", ns):
        texts = [t.text for t in p.findall(".//w:t", ns) if t.text]
        if texts:
            paragraphs.append("".join(texts))
    return "\n".join(paragraphs)


def _extract_briefing_text(uploaded_file) -> tuple[str, str]:
    """
    Retorna (texto_extraido, erro). Em caso de sucesso, erro="".
    """
    if not uploaded_file:
        return "", ""

    filename = str(uploaded_file.name or "briefing")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    data = uploaded_file.getvalue()
    if not data:
        return "", "O arquivo de briefing está vazio."

    try:
        if ext in {"txt", "md", "csv", "json"}:
            return _decode_text_bytes(data).strip(), ""

        if ext == "docx":
            text = _extract_text_from_docx(data).strip()
            if not text:
                return "", "Não foi possível extrair texto do .docx informado."
            return text, ""

        return "", "Formato não suportado. Use .txt, .md, .csv, .json ou .docx."
    except Exception as e:
        return "", f"Erro ao processar briefing: {e}"


def _cap_briefing(text: str, max_chars: int = 20000) -> str:
    text = str(text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[briefing truncado no armazenamento]"


def _slugify(text: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(text or ""))
    return safe.strip("_")[:80] or "projeto"


def _upload_briefing_to_kb(filename: str, content: bytes) -> tuple[bool, str]:
    client = get_openai_client()
    prosodia_vs_id = get_prosodia_vector_store_id()

    if not client:
        return False, "OpenAI não configurado para envio à base de conhecimento."
    if not prosodia_vs_id:
        return False, "A base de conhecimento do NencLex nao esta configurada para a organizacao ativa."

    try:
        uploaded = client.files.create(
            file=(filename, content),
            purpose="assistants",
        )
        client.vector_stores.files.create(
            vector_store_id=prosodia_vs_id,
            file_id=uploaded.id,
        )
        return True, filename
    except Exception as e:
        return False, str(e)

# ------------------------------------------------------------------
# Modo edição vs criação
# ------------------------------------------------------------------
project_id = st.session_state.get("pros_project_id")
editing = project_id is not None
project = get_project(project_id) if editing else {}
if editing and project is None:
    st.session_state.pop("pros_project_id", None)
    editing = False
    project = {}

st.title("✏️ Editar Projeto" if editing else "➕ Novo Projeto do NencLex")

# Navegação
nav_col, _ = st.columns([2, 6])
with nav_col:
    if st.button("← Projetos", width='stretch'):
        st.switch_page("modules/prosodia/projetos.py")

st.divider()

# ==================================================================
# Formulário
# ==================================================================
with st.container():
    st.subheader("📋 Informações do Projeto")

    nome = st.text_input(
        "Nome do Projeto *",
        value=project.get("name", ""),
        placeholder="Ex: Kynetec — Mão de Obra Rural 2026",
    )

    col_a, col_b = st.columns(2)

    with col_a:
        especialidade = st.text_area(
            "Contexto / Área do estudo",
            value=project.get("especialidade", ""),
            placeholder=(
                "Descreva o objetivo da pesquisa, público-alvo "
                "e condições de coleta..."
            ),
            height=120,
        )
        historico = st.text_area(
            "Histórico / Informações adicionais",
            value=project.get("historico", ""),
            placeholder=(
                "Informações sobre a empresa, produto "
                "ou contexto da pesquisa..."
            ),
            height=120,
        )

    with col_b:
        problemas = st.text_area(
            "Problemas / Hipóteses centrais",
            value=project.get("problemas", ""),
            placeholder=(
                "Quais questões centrais devem ser respondidas pela análise?"
            ),
            height=120,
        )

    st.divider()

    st.subheader("❓ Perguntas da Entrevista")
    st.markdown(
        "Liste as perguntas que **devem ser abordadas** em cada entrevista. "
        "O sistema verificará automaticamente a cobertura ao carregar os uploads. "
        "**Uma pergunta por linha.**"
    )

    questions_raw = st.text_area(
        "Perguntas",
        value=project.get("questions", ""),
        placeholder=(
            "Ex:\n"
            "Como você avalia a qualidade do produto?\n"
            "Quais são suas principais dificuldades no campo?\n"
            "Você recomendaria este serviço para outros produtores?"
        ),
        height=200,
        label_visibility="collapsed",
    )

    st.caption("_Deixe em branco para pular a verificação de cobertura de perguntas._")

    st.divider()
    st.subheader("🎯 Entidades prioritárias da análise")
    st.markdown(
        "Liste candidatos, marcas, produtos ou pessoas que devem receber análise "
        "individual no relatório. Use **uma entidade por linha**, opcionalmente "
        "com o tipo antes de dois-pontos."
    )
    entities = st.text_area(
        "Entidades prioritárias",
        value=project.get("entities", ""),
        placeholder=(
            "Candidato: Ana Silva\n"
            "Marca: Campo Forte\n"
            "Produto: Sementes Premium"
        ),
        height=130,
        help=(
            "Esses nomes são buscados literalmente nas transcrições. "
            "Inclua grafias e apelidos relevantes em linhas separadas."
        ),
    )

    st.divider()
    st.subheader("🧾 Briefing do Projeto (contexto para análises)")
    st.markdown(
        "Adicione um documento de **briefing** para enriquecer o contexto das análises de IA "
        "(individual e geral)."
    )

    current_briefing_filename = project.get("briefing_filename", "")
    current_briefing_text = project.get("briefing_text", "")

    briefing_file = st.file_uploader(
        "Documento de Briefing",
        type=["txt", "md", "csv", "json", "docx"],
        help="Formatos aceitos: .txt, .md, .csv, .json, .docx",
    )

    remove_briefing = st.checkbox(
        "Remover briefing atual",
        value=False,
        disabled=not bool(current_briefing_text),
    )

    if current_briefing_filename:
        st.caption(f"Briefing atual: {current_briefing_filename}")
    if current_briefing_text:
        with st.expander("Prévia do briefing atual"):
            preview = current_briefing_text[:1500]
            if len(current_briefing_text) > 1500:
                preview += "\n...[prévia truncada]"
            st.text(preview)

    # ------------------------------------------------------------------
    # Vincular Campanha do WhatsApp (Opcional)
    # ------------------------------------------------------------------
    st.divider()
    st.subheader("📱 Campanha do WhatsApp (Opcional)")
    st.markdown(
        "Vincule este projeto a uma campanha da API de WhatsApp para "
        "sincronizar áudios automaticamente."
    )

    from utils.whatsapp_api_client import is_configured, get_campaigns

    campaign_options = {}  # id -> display label
    current_campaign_id = project.get("whatsapp_campaign_id")
    current_api_project_id = project.get("api_project_id")
    try:
        if current_campaign_id is not None:
            claim_external_resource(
                "whatsapp_campaign",
                current_campaign_id,
                {"project_id": project.get("id")},
            )
        if current_api_project_id is not None:
            claim_external_resource(
                "whatsapp_api_project",
                current_api_project_id,
                {"project_id": project.get("id")},
            )
    except auth.AuthorizationError:
        st.warning("Um recurso externo vinculado pertence a outra organizacao.")
        current_campaign_id = None
        current_api_project_id = None

    if is_configured():
        try:
            owned_campaign_ids = {
                resource["id"]
                for resource in list_external_resources("whatsapp_campaign")
            }
            campaigns = [
                campaign
                for campaign in get_campaigns()
                if str(campaign.get("id")) in owned_campaign_ids
            ]
            campaign_options = {
                c["id"]: f"{c['name']} (ID {c['id']} — {c['status']})"
                for c in campaigns
            }
        except Exception as e:
            if "403" in str(e):
                st.warning("⚠️ Não foi possível buscar campanhas: Chave de API do WhatsApp (X-API-Key) recusada pelo servidor (HTTP 403 Forbidden). Verifique as credenciais nas Configurações da WhatsApp API.")
            else:
                st.warning(f"Não foi possível buscar campanhas: {e}")
    else:
        st.caption(
            "⚠️ API de WhatsApp não configurada. "
            "Configure URL e chave na tela de Projetos para habilitar."
        )

    if campaign_options:
        options_list = [None] + list(campaign_options.keys())
        labels = ["— Nenhuma —"] + list(campaign_options.values())
        default_idx = 0
        if current_campaign_id in campaign_options:
            default_idx = options_list.index(current_campaign_id)

        selected_campaign = st.selectbox(
            "Campanha vinculada",
            options=options_list,
            index=default_idx,
            format_func=lambda x: labels[options_list.index(x)],
            key="prep_campaign_select",
        )
    else:
        selected_campaign = current_campaign_id
        if current_campaign_id:
            st.caption(f"Campanha vinculada atual: ID {current_campaign_id}")

    # ------------------------------------------------------------------
    # Sincronização do Projeto com a API (Opcional)
    # ------------------------------------------------------------------
    st.divider()
    st.subheader("🔗 Sincronização de Projeto na API")
    st.markdown(
        "Vincule este projeto local a um projeto na API para agrupar áudios, contatos e campanhas."
    )

    sincronizar_api = False
    desvincular_api = False
    api_organization = user.organization_name

    if is_configured():
        if current_api_project_id:
            st.success(f"🔗 Projeto vinculado à API: ID #{current_api_project_id}")
            desvincular_api = st.checkbox("Desvincular este projeto da API", value=False)
        else:
            sincronizar_api = st.checkbox("Sincronizar este projeto com a API", value=False)
            if sincronizar_api:
                api_organization = st.text_input(
                    "Organização da API (Organization) *",
                    value=user.organization_name,
                    placeholder="Ex: NENC / Empresa Cliente",
                    help="Nome da organização a ser informada no projeto da API."
                )
    else:
        st.caption(
            "⚠️ API de WhatsApp não configurada. "
            "Configure URL e chave na tela de Projetos para habilitar a sincronização de projetos."
        )

    # ------------------------------------------------------------------
    # Limiares de Qualidade Objetiva
    # ------------------------------------------------------------------
    st.divider()
    st.subheader("⚙️ Parâmetros dos Checks Objetivos")
    st.markdown(
        "Ajuste os parâmetros que determinam os alertas e erros das verificações de qualidade "
        "deste projeto."
    )

    # Carregar thresholds salvos ou defaults
    saved_thresholds_json = project.get("quality_thresholds") if editing else None
    saved_thresholds = None
    if saved_thresholds_json:
        try:
            saved_thresholds = json.loads(saved_thresholds_json)
        except Exception:
            pass

    from utils.prosodia_quality import DEFAULT_THRESHOLDS
    using_default = saved_thresholds is None
    display_thresholds = saved_thresholds if saved_thresholds else DEFAULT_THRESHOLDS

    usar_padrao = st.checkbox(
        "Usar valores padrão do sistema",
        value=using_default,
        help="Se marcado, o sistema utilizará os valores padrão recomendados. Desmarque para personalizar os limites.",
    )

    if not usar_padrao:
        t_col1, t_col2 = st.columns(2)

        with t_col1:
            st.markdown("**🎙️ Fala & Transcrição**")
            val_dur_fail = st.number_input(
                "Duração de fala mínima (Erro - seg)",
                min_value=0,
                value=int(display_thresholds.get("duration_fail_s", DEFAULT_THRESHOLDS["duration_fail_s"])),
                help="Duração total de fala em segundos abaixo da qual o check falhará."
            )
            val_dur_warn = st.number_input(
                "Duração de fala recomendada (Alerta - seg)",
                min_value=0,
                value=int(display_thresholds.get("duration_warn_s", DEFAULT_THRESHOLDS["duration_warn_s"])),
                help="Duração recomendada de fala em segundos. Abaixo disso, gera um alerta."
            )
            val_words_fail = st.number_input(
                "Contagem mínima de palavras (Erro)",
                min_value=0,
                value=int(display_thresholds.get("words_fail", DEFAULT_THRESHOLDS["words_fail"])),
                help="Mínimo de palavras na transcrição. Abaixo disso, o check falhará."
            )
            val_words_warn = st.number_input(
                "Contagem recomendada de palavras (Alerta)",
                min_value=0,
                value=int(display_thresholds.get("words_warn", DEFAULT_THRESHOLDS["words_warn"])),
                help="Mínimo recomendado de palavras. Abaixo disso, gera um alerta."
            )

            st.markdown("**💬 Inteligibilidade & Diálogo**")
            init_unint_warn_pct = float(display_thresholds.get("unintelligible_warn_pct", DEFAULT_THRESHOLDS["unintelligible_warn_pct"]))
            val_unint_warn = st.slider(
                "Alerta de ininteligibilidade (%)",
                min_value=0,
                max_value=100,
                value=int(init_unint_warn_pct * 100),
                help="Proporção limite de turnos com marcadores de ininteligibilidade para gerar um alerta."
            ) / 100.0

            init_unint_fail_pct = float(display_thresholds.get("unintelligible_fail_pct", DEFAULT_THRESHOLDS["unintelligible_fail_pct"]))
            val_unint_fail = st.slider(
                "Erro de ininteligibilidade (%)",
                min_value=0,
                max_value=100,
                value=int(init_unint_fail_pct * 100),
                help="Proporção limite de turnos com marcadores de ininteligibilidade para falhar o check."
            ) / 100.0

            init_silence_ratio_warn = float(display_thresholds.get("silence_ratio_warn", DEFAULT_THRESHOLDS["silence_ratio_warn"]))
            val_silence = st.slider(
                "Alerta de silêncio excessivo (%)",
                min_value=0,
                max_value=100,
                value=int(init_silence_ratio_warn * 100),
                help="Proporção de silêncio acima da qual gera um alerta."
            ) / 100.0

            init_speaker_dom = float(display_thresholds.get("speaker_dominance_warn_pct", DEFAULT_THRESHOLDS["speaker_dominance_warn_pct"]))
            val_speaker_dom = st.slider(
                "Alerta de dominância de locutor (%)",
                min_value=0,
                max_value=100,
                value=int(init_speaker_dom * 100),
                help="Limite de dominância de um único locutor (em número de palavras) para gerar alerta."
            ) / 100.0

        with t_col2:
            st.markdown("**🔊 Ritmo & Acústica**")
            val_min_vad = st.number_input(
                "Mínimo de segmentos VAD (Alerta)",
                min_value=1,
                value=int(display_thresholds.get("min_vad_segments_warn", DEFAULT_THRESHOLDS["min_vad_segments_warn"])),
                help="Quantidade mínima esperada de segmentos VAD. Abaixo disso, gera um alerta."
            )
            val_wpm_low = st.number_input(
                "Taxa de fala mínima (Alerta - WPM)",
                min_value=0,
                value=int(display_thresholds.get("wpm_low_warn", DEFAULT_THRESHOLDS["wpm_low_warn"])),
                help="Taxa de fala em palavras por minuto (WPM) abaixo da qual gera alerta de lentidão."
            )
            val_wpm_high = st.number_input(
                "Taxa de fala máxima (Alerta - WPM)",
                min_value=0,
                value=int(display_thresholds.get("wpm_high_warn", DEFAULT_THRESHOLDS["wpm_high_warn"])),
                help="Taxa de fala em palavras por minuto (WPM) acima da qual gera alerta de rapidez excessiva."
            )
            val_loudness = st.number_input(
                "Volume mínimo (Alerta - Loudness dB)",
                value=float(display_thresholds.get("loudness_low_warn", DEFAULT_THRESHOLDS["loudness_low_warn"])),
                help="Loudness média mínima em dB. Abaixo disso gera alerta de volume baixo."
            )

            init_f0_zero = float(display_thresholds.get("f0_zero_ratio_warn", DEFAULT_THRESHOLDS["f0_zero_ratio_warn"]))
            val_f0_zero = st.slider(
                "Alerta de F0 zerado / Falta de voz (%)",
                min_value=0,
                max_value=100,
                value=int(init_f0_zero * 100),
                help="Proporção limite de frames com F0 zerado para gerar um alerta."
            ) / 100.0

            init_neutral = float(display_thresholds.get("emotion_neutral_warn", DEFAULT_THRESHOLDS["emotion_neutral_warn"]))
            val_neutral = st.slider(
                "Alerta de neutralidade emocional (%)",
                min_value=0,
                max_value=100,
                value=int(init_neutral * 100),
                help="Média de probabilidade da emoção 'neutral' acima da qual gera alerta de monotonia prosódica."
            ) / 100.0

    submitted = st.button(
        "💾 Salvar e ir para Entrevistas",
        type="primary",
        width='stretch',
    )

if submitted:
    if not nome.strip():
        st.error("O **Nome do Projeto** é obrigatório.")
    else:
        briefing_filename = current_briefing_filename
        briefing_text = current_briefing_text
        uploaded_briefing_name = ""
        uploaded_briefing_bytes = b""

        if remove_briefing:
            briefing_filename = ""
            briefing_text = ""

        if briefing_file:
            extracted_text, err = _extract_briefing_text(briefing_file)
            if err:
                st.error(err)
                st.stop()
            briefing_filename = briefing_file.name
            briefing_text = _cap_briefing(extracted_text)
            uploaded_briefing_name = briefing_file.name
            uploaded_briefing_bytes = briefing_file.getvalue()

        saved_project_id = project_id

        # Construir JSON de thresholds
        if usar_padrao:
            quality_thresholds_json = None
        else:
            custom_t = {
                "duration_fail_s": float(val_dur_fail),
                "duration_warn_s": float(val_dur_warn),
                "words_fail": int(val_words_fail),
                "words_warn": int(val_words_warn),
                "unintelligible_fail_pct": float(val_unint_fail),
                "unintelligible_warn_pct": float(val_unint_warn),
                "silence_ratio_warn": float(val_silence),
                "speaker_dominance_warn_pct": float(val_speaker_dom),
                "min_vad_segments_warn": int(val_min_vad),
                "wpm_low_warn": int(val_wpm_low),
                "wpm_high_warn": int(val_wpm_high),
                "f0_zero_ratio_warn": float(val_f0_zero),
                "emotion_neutral_warn": float(val_neutral),
                "loudness_low_warn": float(val_loudness),
            }
            quality_thresholds_json = json.dumps(custom_t)

        # Criar ou desvincular projeto na API se configurado
        api_project_id_to_save = current_api_project_id
        if is_configured():
            if current_api_project_id:
                if desvincular_api:
                    api_project_id_to_save = None
            else:
                if sincronizar_api:
                    if not api_organization.strip():
                        st.error("O campo **Organização da API** é obrigatório para sincronizar.")
                        st.stop()
                    try:
                        with st.spinner("Criando projeto na API..."):
                            from utils.whatsapp_api_client import create_api_project
                            api_proj_resp = create_api_project(nome.strip(), api_organization.strip())
                            api_project_id_to_save = api_proj_resp.get("id")
                    except Exception as e:
                        st.error(f"Falha ao criar projeto na API: {e}")
                        st.stop()

        if editing:
            update_project(
                project_id,
                name=nome.strip(),
                especialidade=especialidade.strip(),
                historico=historico.strip(),
                problemas=problemas.strip(),
                questions=questions_raw.strip(),
                entities=entities.strip(),
                briefing_filename=briefing_filename,
                briefing_text=briefing_text,
                whatsapp_campaign_id=selected_campaign,
                quality_thresholds=quality_thresholds_json,
                api_project_id=api_project_id_to_save,
            )
            st.success("Projeto atualizado!")
        else:
            new_id = create_project(
                name=nome.strip(),
                especialidade=especialidade.strip(),
                historico=historico.strip(),
                problemas=problemas.strip(),
                questions=questions_raw.strip(),
                entities=entities.strip(),
                briefing_filename=briefing_filename,
                briefing_text=briefing_text,
                whatsapp_campaign_id=selected_campaign,
                quality_thresholds=quality_thresholds_json,
                api_project_id=api_project_id_to_save,
            )
            st.session_state["pros_project_id"] = new_id
            saved_project_id = new_id
            st.success("Projeto criado!")

        if uploaded_briefing_name and uploaded_briefing_bytes:
            now_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
            kb_filename = f"briefing_{_slugify(nome.strip())}_{now_tag}_{uploaded_briefing_name}"
            kb_ok, kb_msg = _upload_briefing_to_kb(kb_filename, uploaded_briefing_bytes)
            if kb_ok:
                st.caption(f"Briefing enviado para a base de conhecimento: {kb_msg}")
            else:
                st.warning(
                    f"Projeto salvo, mas não foi possível enviar o briefing para a base: {kb_msg}"
                )
                st.stop()

        if saved_project_id:
            st.session_state["pros_project_id"] = saved_project_id

        st.switch_page("modules/prosodia/entrevistas.py")
