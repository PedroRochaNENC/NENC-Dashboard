"""
Prosódia — Dados do Projeto.

Formulário de criação/edição de um projeto: nome, tipo, contexto e perguntas.
As perguntas e a faixa de duração esperada dos áudios alimentam a verificação
automática de qualidade de cada áudio.
"""

import json
from datetime import datetime

import streamlit as st
from utils import auth, ui
from utils.icons import page_title

user = auth.require_module("prosodia")
pode_editar = auth.can_write(user)

from utils.prosodia_db import (
    init_db,
    create_project,
    get_project,
    update_project,
    user_can_modify_project,
    DEFAULT_QR_VERIFICATION_TEXT,
)
from utils.ai_provider import (
    add_document_to_vector_store,
    get_openai_client,
    get_prosodia_vector_store_id,
)
from utils.briefing import cap_text, extract_briefing_text
from utils.kb_attributes import project_document
from utils.organization_data import claim_external_resource, list_external_resources
from utils.prosodia_project_types import (
    DURACAO_ESPERADA_LABELS,
    PESQUISA_OPINIAO,
    PROJECT_TYPE_LABELS,
    normalize_duracao_esperada,
    normalize_project_type,
)

init_db()


def _extract_briefing_text(uploaded_file) -> tuple[str, str]:
    """
    Retorna (texto_extraido, erro). Em caso de sucesso, erro="".
    """
    if not uploaded_file:
        return "", ""
    return extract_briefing_text(uploaded_file.name, uploaded_file.getvalue())


def _slugify(text: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(text or ""))
    return safe.strip("_")[:80] or "projeto"


def _upload_briefing_to_kb(
    filename: str, content: bytes, project_id=None
) -> tuple[bool, str]:
    client = get_openai_client()
    prosodia_vs_id = get_prosodia_vector_store_id()

    if not client:
        return False, "OpenAI não configurado para envio à base de conhecimento."
    if not prosodia_vs_id:
        return False, "A base de conhecimento do NencBoost nao esta configurada para a organizacao ativa."

    try:
        add_document_to_vector_store(
            prosodia_vs_id,
            filename,
            content,
            project_document("prosodia", project_id, tipo="briefing"),
            wait=False,
        )
        return True, filename
    except Exception as e:
        return False, str(e)


def _fmt_segundos(segundos: float) -> str:
    """8 s, 2 min 30 s, 1 h."""
    horas, resto = divmod(int(round(segundos)), 3600)
    minutos, segs = divmod(resto, 60)
    partes = [f"{horas} h" if horas else "", f"{minutos} min" if minutos else "",
              f"{segs} s" if segs else ""]
    return " ".join(p for p in partes if p) or "0 s"


def _tabela_parametros(t: dict, opiniao: bool) -> str:
    """Tabela em markdown dos limiares, para conferir os recomendados da faixa."""
    def inteiro(n) -> str:
        return f"{int(n):,}".replace(",", ".")

    def pct(x) -> str:
        return f"{float(x):.0%}"

    linhas = [
        ("Duração de fala", f"abaixo de {_fmt_segundos(t['duration_fail_s'])}",
         f"abaixo de {_fmt_segundos(t['duration_warn_s'])}"),
        ("Contagem de palavras", f"abaixo de {inteiro(t['words_fail'])}",
         f"abaixo de {inteiro(t['words_warn'])}"),
        ("Segmentos VAD", "—", f"abaixo de {inteiro(t['min_vad_segments_warn'])}"),
        ("Taxa de fala", "—", f"fora de {t['wpm_low_warn']}–{t['wpm_high_warn']} WPM"),
        ("Silêncio", "—", f"a partir de {pct(t['silence_ratio_warn'])}"),
        ("Turnos ininteligíveis", f"a partir de {pct(t['unintelligible_fail_pct'])}",
         f"a partir de {pct(t['unintelligible_warn_pct'])}"),
    ]
    # Na pesquisa de opinião o equilíbrio entre locutores não é checado.
    if not opiniao:
        linhas.append(("Dominância de um locutor", "—",
                       f"a partir de {pct(t['speaker_dominance_warn_pct'])} das palavras"))
    linhas += [
        ("F0 zerado (falta de voz)", "—", f"a partir de {pct(t['f0_zero_ratio_warn'])}"),
        ("Neutralidade emocional", "—", f"a partir de {pct(t['emotion_neutral_warn'])}"),
        ("Volume (loudness)", "—", f"abaixo de {t['loudness_low_warn']:g} dB"),
    ]
    return "| Parâmetro | Erro | Alerta |\n|---|---|---|\n" + "\n".join(
        f"| {nome} | {erro} | {alerta} |" for nome, erro, alerta in linhas
    )


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

# Ao editar, a autoria do projeto entra na conta; ao criar, basta o papel.
if editing:
    pode_editar = user_can_modify_project(project, user)

ui.inject_theme()
ui.breadcrumb(
    "NencBoost", "Projetos", "Editar" if editing else "Novo"
)
page_title(
    "note-pencil" if editing else "plus",
    "Editar Projeto" if editing else "Novo Projeto",
    project.get("name")
    if editing
    else "Perguntas, entidades e briefing do projeto.",
)

# ==================================================================
# Formulário
# ==================================================================
with st.container():
    st.subheader("Informações do Projeto")

    tipo_salvo = normalize_project_type(project.get("tipo_projeto"))
    tipos = list(PROJECT_TYPE_LABELS)
    tipo_projeto = st.radio(
        "Tipo de projeto",
        options=tipos,
        index=tipos.index(tipo_salvo),
        format_func=PROJECT_TYPE_LABELS.get,
        horizontal=True,
        # O id na chave impede que a escolha feita num projeto passe para
        # outro quando o projeto ativo muda na barra lateral.
        key=f"prep_tipo_projeto_{project_id or 'novo'}",
        help=(
            "Entrevista: conversa entre entrevistador e entrevistado. "
            "Pesquisa de opinião: áudio curto de um único respondente, como o "
            "recado enviado pelo QR Code do WhatsApp. O tipo define o roteiro "
            "da análise de IA; os parâmetros de qualidade seguem a duração "
            "esperada dos áudios, escolhida mais abaixo."
        ),
    )
    opiniao = tipo_projeto == PESQUISA_OPINIAO
    if editing and tipo_projeto != tipo_salvo:
        st.info("Análises e checagens já feitas não mudam; as próximas usam o novo tipo.")

    nome = st.text_input(
        "Nome do Projeto *",
        value=project.get("name", ""),
        placeholder="Ex: Pesquisa de Satisfação do Cliente 2026",
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

    # Só textos fora de widget mudam com o tipo: um widget sem key cujo label
    # ou placeholder muda é recriado e perde o que o usuário já digitou.
    if opiniao:
        st.subheader("Perguntas / Tópicos da Pesquisa de Opinião")
        st.markdown(
            "Liste as perguntas ou tópicos norteadores da coleta de feedback. "
            "O sistema verificará automaticamente se cada áudio os aborda. "
            "**Um item por linha.**"
        )
    else:
        st.subheader("Perguntas da Entrevista")
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
            "Como você avalia a qualidade do produto ou serviço?\n"
            "Quais são suas principais dificuldades no dia a dia?\n"
            "Você recomendaria isso para outras pessoas?"
        ),
        height=200,
        label_visibility="collapsed",
    )

    st.caption("_Deixe em branco para pular a verificação de cobertura de perguntas._")

    st.divider()
    st.subheader("Entidades prioritárias da análise")
    st.markdown(
        "Liste candidatos, marcas, produtos ou pessoas que devem receber análise "
        "individual no relatório. Use **uma entidade por linha**, opcionalmente "
        "com o tipo antes de dois-pontos."
    )
    entities = st.text_area(
        "Entidades prioritárias",
        value=project.get("entities", ""),
        placeholder=(
            "Candidato: Nome do Candidato\n"
            "Marca: Nome da Marca\n"
            "Produto: Nome do Produto"
        ),
        height=130,
        help=(
            "Esses nomes são buscados literalmente nas transcrições. "
            "Inclua grafias e apelidos relevantes em linhas separadas."
        ),
    )

    st.divider()
    st.subheader("Briefing do Projeto (contexto para análises)")
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
    st.subheader("Campanha do WhatsApp (Opcional)")
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
                st.warning("Não foi possível buscar campanhas: Chave de API do WhatsApp (X-API-Key) recusada pelo servidor (HTTP 403 Forbidden). Verifique as credenciais nas Configurações da WhatsApp API.")
            else:
                st.warning(f"Não foi possível buscar campanhas: {e}")
    else:
        st.caption(
            "API de WhatsApp não configurada. "
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
    st.subheader("Sincronização de Projeto na API")
    st.markdown(
        "Vincule este projeto local a um projeto na API para agrupar áudios, contatos e campanhas."
    )

    sincronizar_api = False
    desvincular_api = False
    api_organization = user.organization_name

    if is_configured():
        if current_api_project_id:
            st.success(f"Projeto vinculado à API: ID #{current_api_project_id}")
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
            "API de WhatsApp não configurada. "
            "Configure URL e chave na tela de Projetos para habilitar a sincronização de projetos."
        )

    # ------------------------------------------------------------------
    # Texto de Verificação do QR Code (WhatsApp)
    # ------------------------------------------------------------------
    st.divider()
    st.subheader("Texto de Verificação do QR Code (WhatsApp)")
    st.markdown(
        "Personalize o texto que será pré-preenchido na mensagem de WhatsApp do participante "
        "ao escanear qualquer QR Code deste projeto."
    )
    current_qr_verification_text = project.get("qr_verification_text") or DEFAULT_QR_VERIFICATION_TEXT
    qr_verification_input = st.text_area(
        "Texto de Verificação",
        value=current_qr_verification_text,
        height=100,
        help="Texto pré-preenchido no WhatsApp ao escanear o QR Code de verificação.",
    )

    # ------------------------------------------------------------------
    # Limiares de Qualidade Objetiva
    # ------------------------------------------------------------------
    st.divider()
    st.subheader("Parâmetros dos Checks Objetivos")
    st.markdown(
        "Escolha a duração esperada de cada áudio: os parâmetros que determinam os "
        "alertas e erros das verificações de qualidade se ajustam a ela."
    )

    duracao_salva = normalize_duracao_esperada(project.get("duracao_esperada"))
    faixas = list(DURACAO_ESPERADA_LABELS)
    duracao_esperada = st.selectbox(
        "Duração esperada de cada áudio *",
        options=faixas,
        index=faixas.index(duracao_salva) if duracao_salva else None,
        format_func=DURACAO_ESPERADA_LABELS.get,
        placeholder="Selecione a faixa de duração",
        # Como no tipo: o id na chave impede que a escolha passe de um projeto a outro.
        key=f"prep_duracao_esperada_{project_id or 'novo'}",
        help=(
            "Duração típica de um áudio do projeto, do início ao fim da gravação. "
            "Um recado de WhatsApp costuma ficar abaixo de 1 min; uma entrevista "
            "em profundidade, entre 30 min e 1 h."
        ),
    )

    # Carregar thresholds salvos
    saved_thresholds_json = project.get("quality_thresholds") if editing else None
    saved_thresholds = None
    if saved_thresholds_json:
        try:
            saved_thresholds = json.loads(saved_thresholds_json)
        except Exception:
            pass

    from utils.prosodia_quality import default_thresholds
    # None grava NULL ("Usar valores recomendados"): o padrão da faixa é
    # resolvido na hora da checagem (prosodia_quality.thresholds_for_project).
    custom_thresholds = None
    if duracao_esperada is None:
        st.info(
            "Selecione a faixa de duração para ver os parâmetros recomendados. "
            "Ela é obrigatória para salvar o projeto."
        )
    else:
        faixa_defaults = default_thresholds(tipo_projeto, duracao_esperada)
        # Valores personalizados valem para a faixa em que foram feitos: trocar
        # a faixa volta aos recomendados para a nova.
        personalizado_valido = bool(saved_thresholds) and duracao_esperada == duracao_salva
        if editing and duracao_esperada != duracao_salva:
            aviso = "Checagens já feitas não mudam; as próximas usam os parâmetros desta faixa."
            if saved_thresholds:
                aviso += (
                    " Os parâmetros personalizados salvos não foram feitos para ela "
                    "e deram lugar aos recomendados."
                )
            st.info(aviso)
        display_thresholds = saved_thresholds if personalizado_valido else faixa_defaults

        usar_padrao = st.checkbox(
            "Usar valores recomendados para a faixa",
            value=not personalizado_valido,
            help=(
                "Se marcado, o sistema utilizará os valores recomendados para a duração "
                "esperada dos áudios. Desmarque para personalizar os limites."
            ),
        )

        if usar_padrao:
            st.markdown(_tabela_parametros(faixa_defaults, opiniao))
        else:
            t_col1, t_col2 = st.columns(2)

            with t_col1:
                st.markdown("**Fala & Transcrição**")
                val_dur_fail = st.number_input(
                    "Duração de fala mínima (Erro - seg)",
                    min_value=0,
                    value=int(display_thresholds.get("duration_fail_s", faixa_defaults["duration_fail_s"])),
                    help="Duração total de fala em segundos abaixo da qual o check falhará."
                )
                val_dur_warn = st.number_input(
                    "Duração de fala recomendada (Alerta - seg)",
                    min_value=0,
                    value=int(display_thresholds.get("duration_warn_s", faixa_defaults["duration_warn_s"])),
                    help="Duração recomendada de fala em segundos. Abaixo disso, gera um alerta."
                )
                val_words_fail = st.number_input(
                    "Contagem mínima de palavras (Erro)",
                    min_value=0,
                    value=int(display_thresholds.get("words_fail", faixa_defaults["words_fail"])),
                    help="Mínimo de palavras na transcrição. Abaixo disso, o check falhará."
                )
                val_words_warn = st.number_input(
                    "Contagem recomendada de palavras (Alerta)",
                    min_value=0,
                    value=int(display_thresholds.get("words_warn", faixa_defaults["words_warn"])),
                    help="Mínimo recomendado de palavras. Abaixo disso, gera um alerta."
                )

                st.markdown("**Inteligibilidade & Diálogo**")
                init_unint_warn_pct = float(display_thresholds.get("unintelligible_warn_pct", faixa_defaults["unintelligible_warn_pct"]))
                val_unint_warn = st.slider(
                    "Alerta de ininteligibilidade (%)",
                    min_value=0,
                    max_value=100,
                    value=int(init_unint_warn_pct * 100),
                    help="Proporção limite de turnos com marcadores de ininteligibilidade para gerar um alerta."
                ) / 100.0

                init_unint_fail_pct = float(display_thresholds.get("unintelligible_fail_pct", faixa_defaults["unintelligible_fail_pct"]))
                val_unint_fail = st.slider(
                    "Erro de ininteligibilidade (%)",
                    min_value=0,
                    max_value=100,
                    value=int(init_unint_fail_pct * 100),
                    help="Proporção limite de turnos com marcadores de ininteligibilidade para falhar o check."
                ) / 100.0

                init_silence_ratio_warn = float(display_thresholds.get("silence_ratio_warn", faixa_defaults["silence_ratio_warn"]))
                val_silence = st.slider(
                    "Alerta de silêncio excessivo (%)",
                    min_value=0,
                    max_value=100,
                    value=int(init_silence_ratio_warn * 100),
                    help="Proporção de silêncio acima da qual gera um alerta."
                ) / 100.0

                init_speaker_dom = float(display_thresholds.get("speaker_dominance_warn_pct", faixa_defaults["speaker_dominance_warn_pct"]))
                if opiniao:
                    # Um só respondente por áudio: a checagem de equilíbrio entre
                    # locutores não roda nesse tipo. O valor segue no JSON, sem uso.
                    val_speaker_dom = init_speaker_dom
                else:
                    val_speaker_dom = st.slider(
                        "Alerta de dominância de locutor (%)",
                        min_value=0,
                        max_value=100,
                        value=int(init_speaker_dom * 100),
                        help="Limite de dominância de um único locutor (em número de palavras) para gerar alerta."
                    ) / 100.0

            with t_col2:
                st.markdown("**Ritmo & Acústica**")
                val_min_vad = st.number_input(
                    "Mínimo de segmentos VAD (Alerta)",
                    min_value=1,
                    value=int(display_thresholds.get("min_vad_segments_warn", faixa_defaults["min_vad_segments_warn"])),
                    help="Quantidade mínima esperada de segmentos VAD. Abaixo disso, gera um alerta."
                )
                val_wpm_low = st.number_input(
                    "Taxa de fala mínima (Alerta - WPM)",
                    min_value=0,
                    value=int(display_thresholds.get("wpm_low_warn", faixa_defaults["wpm_low_warn"])),
                    help="Taxa de fala em palavras por minuto (WPM) abaixo da qual gera alerta de lentidão."
                )
                val_wpm_high = st.number_input(
                    "Taxa de fala máxima (Alerta - WPM)",
                    min_value=0,
                    value=int(display_thresholds.get("wpm_high_warn", faixa_defaults["wpm_high_warn"])),
                    help="Taxa de fala em palavras por minuto (WPM) acima da qual gera alerta de rapidez excessiva."
                )
                val_loudness = st.number_input(
                    "Volume mínimo (Alerta - Loudness dB)",
                    value=float(display_thresholds.get("loudness_low_warn", faixa_defaults["loudness_low_warn"])),
                    help="Loudness média mínima em dB. Abaixo disso gera alerta de volume baixo."
                )

                init_f0_zero = float(display_thresholds.get("f0_zero_ratio_warn", faixa_defaults["f0_zero_ratio_warn"]))
                val_f0_zero = st.slider(
                    "Alerta de F0 zerado / Falta de voz (%)",
                    min_value=0,
                    max_value=100,
                    value=int(init_f0_zero * 100),
                    help="Proporção limite de frames com F0 zerado para gerar um alerta."
                ) / 100.0

                init_neutral = float(display_thresholds.get("emotion_neutral_warn", faixa_defaults["emotion_neutral_warn"]))
                val_neutral = st.slider(
                    "Alerta de neutralidade emocional (%)",
                    min_value=0,
                    max_value=100,
                    value=int(init_neutral * 100),
                    help="Média de probabilidade da emoção 'neutral' acima da qual gera alerta de monotonia prosódica."
                ) / 100.0

            custom_thresholds = {
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

    if not pode_editar:
        if editing and auth.can_write(user):
            st.info("Este projeto foi criado por outro administrador da organização.")
        else:
            st.info("Sua conta tem acesso somente de leitura ao NencBoost.")

    submitted = st.button(
        "Salvar e ir para Áudios",
        type="primary",
        width='stretch',
        disabled=not pode_editar,
    )

if submitted:
    if not nome.strip():
        st.error("O **Nome do Projeto** é obrigatório.")
    elif duracao_esperada is None:
        st.error(
            "Selecione a **Duração esperada de cada áudio** em Parâmetros dos Checks Objetivos."
        )
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
            briefing_text = cap_text(extracted_text)
            uploaded_briefing_name = briefing_file.name
            uploaded_briefing_bytes = briefing_file.getvalue()

        saved_project_id = project_id

        quality_thresholds_json = json.dumps(custom_thresholds) if custom_thresholds else None

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
                qr_verification_text=qr_verification_input.strip() if qr_verification_input else None,
                tipo_projeto=tipo_projeto,
                duracao_esperada=duracao_esperada,
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
                qr_verification_text=qr_verification_input.strip() if qr_verification_input else None,
                tipo_projeto=tipo_projeto,
                duracao_esperada=duracao_esperada,
            )
            st.session_state["pros_project_id"] = new_id
            saved_project_id = new_id
            st.success("Projeto criado!")

        if uploaded_briefing_name and uploaded_briefing_bytes:
            now_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
            kb_filename = f"briefing_{_slugify(nome.strip())}_{now_tag}_{uploaded_briefing_name}"
            kb_ok, kb_msg = _upload_briefing_to_kb(
                kb_filename, uploaded_briefing_bytes, saved_project_id
            )
            if kb_ok:
                st.caption(f"Briefing enviado para a base de conhecimento: {kb_msg}")
            else:
                st.warning(
                    f"Projeto salvo, mas não foi possível enviar o briefing para a base: {kb_msg}"
                )
                st.stop()

        if saved_project_id:
            st.session_state["pros_project_id"] = saved_project_id

        # Num projeto recem-criado o menu desta execucao foi montado sem
        # projeto, entao a pagina de Audios ainda nao esta registrada.
        st.session_state["_navigate_to"] = "modules/prosodia/entrevistas.py"
        st.rerun()
