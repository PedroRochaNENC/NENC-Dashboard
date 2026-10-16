"""
Prosodia - Analise Geral do Projeto.

Consolida dados de todos os áudios de um projeto para gerar:
- visao agregada de metricas
- visualizacoes por locutor/features
- analise geral por IA com historico
"""

import io
import logging
from datetime import datetime

import pandas as pd
import streamlit as st
from utils import auth, ui
from utils.icons import page_title

_user = auth.require_module("prosodia")
pode_editar = auth.can_write(_user)
from utils import pdf_report
from utils import prosodia_overview as overview

from utils.prosodia_db import (
    init_db,
    get_project,
    get_audios_for_interviews,
    attach_audio_blobs,
    get_latest_project_analysis,
    get_project_analyses,
    delete_project_analyses,
)
from utils.prosodia_loader import (
    extract_topic_from_text,
    load_prosodia_from_uploads,
    normalizar_sincronizado,
)
from utils.prosodia_signals import (
    detectar_divergencias,
    formatar_tempo,
    indice_combinado_por_grupo,
    referencia_valencia,
    selecionar_momentos_ativacao,
    tem_sentimento_texto,
)
# from utils.prosodia_powerbi_export import export_project_to_powerbi_excel
from utils.prosodia_charts import (
    create_speaker_stats,
    create_project_acoustic_comparison,
    create_project_word_ranking,
)
from utils.prosodia_project_types import ENTREVISTA_QUALITATIVA, normalize_project_type
from utils import prosodia_geracao as geracao
from utils.ai_provider import (
    PROVIDER_OPENAI,
    add_document_to_vector_store,
    chat_completion,
    get_openai_client,
    get_prosodia_vector_store_id,
    create_analysis as ai_create_analysis,
)
from utils.kb_attributes import build_kb_filter, project_document

_LOGGER = logging.getLogger(__name__)

init_db()


def _slugify(text: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(text or ""))
    return safe.strip("_")[:80] or "projeto"


def _build_project_analysis_markdown(
    project_name: str,
    model: str,
    created_at: str,
    text: str,
    citations: list,
) -> str:
    lines = [
        "# Analise Geral do Projeto - NencBoost",
        "",
        f"- Projeto: {project_name or '-'}",
        f"- Modelo: {model or '-'}",
        f"- Gerado em: {created_at}",
        "",
        "## Resultado",
        "",
        text or "",
        "",
    ]

    if citations:
        lines.extend(["## Referencias", ""])
        for i, cit in enumerate(citations, 1):
            filename = cit.get("filename", "Documento")
            quote = cit.get("quote", "")
            lines.append(f"{i}. {filename}")
            if quote:
                lines.append(f"   - Trecho: {quote}")

    return "\n".join(lines)


def _build_project_analysis_pdf(
    project_name: str,
    project_info: dict,
    model: str,
    created_at: str,
    text: str,
    citations: list,
    acoustic_summary: str = "",
) -> bytes:
    """PDF da análise geral, no mesmo desenho dos demais relatórios.

    Usa `pdf_report` em vez de montar o FPDF na mão: o texto da IA passa a
    renderizar títulos, negrito e tabelas em vez de sair com `**` e barras
    verticais cruas, o rodapé numera as páginas, e a sanitização preserva
    travessões e aspas curvas que a codificação antiga virava `?`.
    """
    pdf = pdf_report.ReportPDF("Análise Geral · {}".format(project_name or "Projeto"))
    pdf.add_page()

    # -- Capa curta -------------------------------------------------------
    pdf.set_font(pdf_report.FONT, "B", 8.5)
    pdf.set_text_color(*pdf_report.ACCENT)
    pdf.cell(0, 5, pdf_report.sanitize("NENCBOOST · ANÁLISE DE VOZ E PROSÓDIA"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1.5)

    pdf.set_font(pdf_report.FONT, "B", 20)
    pdf.set_text_color(*pdf_report.INK)
    pdf.multi_cell(0, 9, pdf_report.sanitize(project_name or "Projeto"),
                   new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    pdf.set_draw_color(*pdf_report.ACCENT)
    pdf.set_line_width(0.8)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + 24, pdf.get_y())
    pdf.set_line_width(0.2)
    pdf.ln(5)

    ficha = [item for item in (
        "Modelo: {}".format(model) if model else "",
        "Gerado em: {}".format(created_at) if created_at else "",
    ) if item]
    if ficha:
        pdf_report.paragraph(pdf, " · ".join(ficha), size=8.5, color=pdf_report.MUTED)

    # -- Contexto do projeto ----------------------------------------------
    rotulos = [
        ("Especialidade", "especialidade"),
        ("Histórico", "historico"),
        ("Perguntas centrais", "problemas"),
        ("Briefing", "briefing"),
    ]
    contexto = [(rotulo, str(project_info.get(chave) or "").strip())
                for rotulo, chave in rotulos]
    contexto = [(rotulo, valor) for rotulo, valor in contexto if valor]
    if contexto:
        pdf_report.heading(pdf, "Contexto do Projeto", level=2)
        for rotulo, valor in contexto:
            # O briefing e o campo longo; os demais cabem em poucas linhas.
            if len(valor) > 1200:
                valor = valor[:1200].rstrip() + "…"
            pdf_report.paragraph(pdf, "{}:".format(rotulo), size=9, style="B",
                                 color=pdf_report.INK)
            pdf_report.paragraph(pdf, valor, size=9)

    # -- Resultado da análise ---------------------------------------------
    if text:
        pdf_report.heading(pdf, "Resultado da Análise", level=2)
        pdf_report.render_markdown_lite(pdf, text)

    # -- Métricas acústicas -----------------------------------------------
    # Vem em markdown do mesmo gerador que alimenta o prompt; renderizar como
    # tabela e nao como texto monoespacado cortado em 120 colunas.
    if acoustic_summary:
        pdf_report.heading(pdf, "Métricas Acústicas Consolidadas", level=2)
        pdf_report.render_markdown_lite(pdf, acoustic_summary, size=8.5)

    # -- Referências -------------------------------------------------------
    if citations:
        pdf_report.heading(pdf, "Referências da Base de Conhecimento", level=2)
        for numero, citacao in enumerate(citations, 1):
            arquivo = citacao.get("filename") or "Documento"
            trecho = (citacao.get("quote") or "").strip()
            pdf_report.paragraph(pdf, "{}. {}".format(numero, arquivo), size=9,
                                 style="B", color=pdf_report.INK)
            if trecho:
                if len(trecho) > 600:
                    trecho = trecho[:600].rstrip() + "…"
                pdf_report.paragraph(pdf, trecho, size=8.5, style="I")

    return pdf_report.output_bytes(pdf)


def _append_result_to_kb(
    filename: str, content: str, project_id=None
) -> tuple[bool, str]:
    client = get_openai_client()
    prosodia_vs_id = get_prosodia_vector_store_id()

    if not client:
        return False, "OpenAI nao configurado para envio a base de conhecimento."
    if not prosodia_vs_id:
        return False, "A base de conhecimento do NencBoost nao esta configurada para a organizacao ativa."

    try:
        add_document_to_vector_store(
            prosodia_vs_id,
            filename,
            content.encode("utf-8"),
            project_document("prosodia", project_id, escopo="analise"),
            wait=False,
        )
        return True, filename
    except Exception as e:
        return False, str(e)


def _load_project_frames(
    project_id: int, audios: list[dict]
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Dataframes consolidados do projeto, lidos do conteúdo dos áudios.

    Os áudios chegam da tabela de entrevistas, que carrega só metadado; os
    blobs vêm aqui, onde vão ser de fato lidos.
    """
    audios = attach_audio_blobs(project_id, audios)

    vad_parts = []
    tr_parts = []
    sinc_parts = []
    sincronizados_ilegiveis = []

    class _BytesFile:
        def __init__(self, data: bytes, name: str):
            self._buf = io.BytesIO(data)
            self.name = name

        def read(self):
            return self._buf.read()

        def seek(self, pos: int):
            return self._buf.seek(pos)

    for audio in audios:
        sid = audio.get("session_id", "")

        parsed = load_prosodia_from_uploads(
            json_files=[_BytesFile(audio["prosodia_json"], f"Prosodia-{sid}.json")] if audio.get("prosodia_json") else [],
            csv_files=[_BytesFile(audio["transcricao_csv"], f"Transcricao-{sid}.csv")] if audio.get("transcricao_csv") else [],
            sincronizado_files=[_BytesFile(audio["sincronizado_csv"], f"Sincronizado-{sid}.csv")] if audio.get("sincronizado_csv") else [],
        )

        vad_df = parsed.get("vad", pd.DataFrame())
        tr_df = parsed.get("transcricao", pd.DataFrame())

        if not vad_df.empty:
            if "session_id" not in vad_df.columns:
                vad_df = vad_df.copy()
                vad_df["session_id"] = sid
            vad_parts.append(vad_df)

        if not tr_df.empty:
            if "session_id" not in tr_df.columns:
                tr_df = tr_df.copy()
                tr_df["session_id"] = sid
            tr_parts.append(tr_df)

        if audio.get("sincronizado_csv"):
            try:
                sinc_df = pd.read_csv(io.BytesIO(audio["sincronizado_csv"]))
                if not sinc_df.empty:
                    sinc_parts.append(normalizar_sincronizado(sinc_df, sid))
            except Exception:
                # Engolir aqui apaga o audio da analise sem deixar rastro: o
                # prompt sai sem as metricas dele e o relatorio conclui que nao
                # havia dados. O projeto segue com os demais, mas registrado.
                _LOGGER.exception(
                    "Sincronizado ilegivel no audio %s do projeto %s; "
                    "ele fica fora das metricas acusticas do projeto.",
                    sid,
                    project_id,
                )
                sincronizados_ilegiveis.append(sid)

    if sincronizados_ilegiveis:
        st.warning(
            "Não foi possível ler o NencBoost de {} áudio(s): {}. "
            "Eles ficam de fora das métricas acústicas deste relatório.".format(
                len(sincronizados_ilegiveis), ", ".join(sincronizados_ilegiveis)
            )
        )

    all_vad = pd.concat(vad_parts, ignore_index=True) if vad_parts else pd.DataFrame()
    all_tr = pd.concat(tr_parts, ignore_index=True) if tr_parts else pd.DataFrame()
    all_sinc = pd.concat(sinc_parts, ignore_index=True) if sinc_parts else pd.DataFrame()
    return all_vad, all_tr, all_sinc


def _extract_high_activation_moments(sinc_df: pd.DataFrame, top_n: int = 15) -> pd.DataFrame:
    # Mesma regra da análise do áudio; o mesmo trecho de fala conta uma vez.
    return selecionar_momentos_ativacao(sinc_df, top_n=top_n, min_altos=5)


def _limpo(valor) -> str:
    return "" if valor is None or (isinstance(valor, float) and pd.isna(valor)) else str(valor).strip()


def _tempo_do_momento(row) -> str:
    """O início do segmento do VAD, onde a ativação foi medida."""
    return formatar_tempo(row.get("seconds", row.get("start_s"))) or _limpo(row.get("Timestamp", ""))


def _group_similar_topics(moments: list) -> list:
    if not moments:
        return []
        
    import re
    from collections import Counter
    
    word_occurrences = []
    moment_words = []
    for m in moments:
        topic = m.get("topic", "") or ""
        words = [w.lower() for w in re.findall(r"\b[a-z]{3,}\b", topic.lower())]
        moment_words.append((m, words))
        word_occurrences.extend(words)
        
    word_counts = Counter(word_occurrences)
    
    groups = {}
    core_words_by_freq = [w for w, c in word_counts.most_common() if c > 1]
    
    assigned_moments = set()
    
    # Primeiro pass: agrupar por palavras compartilhadas
    for core in core_words_by_freq:
        for idx, (m, words) in enumerate(moment_words):
            if idx in assigned_moments:
                continue
            if core in words:
                if core not in groups:
                    groups[core] = []
                groups[core].append(m)
                assigned_moments.add(idx)
                
    # Segundo pass: momentos restantes ganham grupo próprio
    for idx, (m, words) in enumerate(moment_words):
        if idx in assigned_moments:
            continue
        core = words[0] if words else "Geral"
        if core not in groups:
            groups[core] = []
        groups[core].append(m)
        assigned_moments.add(idx)
        
    grouped_results = []
    for core_word, group_moments in groups.items():
        topic_counts = Counter(m.get("topic", "") for m in group_moments)
        representative_name = topic_counts.most_common(1)[0][0]
        
        arousals = [m.get("dim_arousal", 0.0) for m in group_moments]
        avg_arousal = sum(arousals) / len(arousals) if arousals else 0.0
        
        sessions = sorted(list(set(m.get("session_id", "") for m in group_moments)))
        sessions_str = ", ".join(sessions)
        
        best_moment = max(group_moments, key=lambda m: m.get("dim_arousal", 0.0))
        example_quote = f"\"{best_moment.get('Text', '')}\" ({best_moment.get('SpeakerName', '')})"
        
        grouped_results.append({
            "topic_group": representative_name,
            "count": len(group_moments),
            "avg_arousal": avg_arousal,
            "sessions": sessions_str,
            "example": example_quote,
        })
        
    grouped_results.sort(key=lambda x: (x["count"], x["avg_arousal"]), reverse=True)
    return grouped_results


def _calculate_questions_activation(audios: list, all_sinc: pd.DataFrame) -> list:
    if not audios or all_sinc.empty:
        return []
        
    from utils.prosodia_db import get_latest_quality_check
    import re
    
    def _get_clean_tokens(txt: str) -> set:
        if not txt or not isinstance(txt, str):
            return set()
        cleaned = re.sub(r"[^\w\s]", " ", txt.lower())
        return {w for w in cleaned.split() if len(w) >= 3}
        
    question_matches = {}
    
    for audio in audios:
        sid = audio.get("session_id", "")
        audio_id = audio.get("id")
        
        quality = get_latest_quality_check(audio_id)
        if not quality or not quality.get("coverage"):
            continue
            
        sinc_sub = all_sinc[all_sinc["session_id"] == sid]
        if sinc_sub.empty:
            continue
            
        segment_tokens = []
        for _, row in sinc_sub.iterrows():
            txt = str(row.get("Text", ""))
            segment_tokens.append((row, _get_clean_tokens(txt)))
            
        for item in quality["coverage"]:
            q = item.get("question", "").strip()
            if not q:
                continue
            is_covered = item.get("covered_keywords") or item.get("covered_ai")
            evidence = item.get("evidence", "").strip()
            
            if not is_covered or not evidence:
                continue
                
            evidence_tokens = _get_clean_tokens(evidence)
            if not evidence_tokens:
                continue
                
            best_row = None
            best_overlap = 0.0
            
            for row, tokens in segment_tokens:
                if not tokens:
                    continue
                overlap = len(evidence_tokens & tokens) / len(evidence_tokens)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_row = row
                    
            if best_row is not None and best_overlap >= 0.20:
                if q not in question_matches:
                    question_matches[q] = []
                    
                question_matches[q].append({
                    "session_id": sid,
                    "SpeakerName": str(best_row.get("SpeakerName", "Desconhecido")),
                    "Text": str(best_row.get("Text", "")),
                    "dim_arousal": float(best_row.get("dim_arousal", 0.0)) if pd.notna(best_row.get("dim_arousal")) else 0.0,
                    "f0_variacao": float(best_row.get("f0_variacao", 0.0)) if pd.notna(best_row.get("f0_variacao")) else 0.0,
                    "loudness_variacao": float(best_row.get("loudness_variacao", 0.0)) if pd.notna(best_row.get("loudness_variacao")) else 0.0,
                    "Timestamp": str(best_row.get("Timestamp", "")),
                })
                
    results = []
    for q, matches in question_matches.items():
        arousals = [m["dim_arousal"] for m in matches]
        f0_vars = [m["f0_variacao"] for m in matches]
        ld_vars = [m["loudness_variacao"] for m in matches]
        
        avg_arousal = sum(arousals) / len(arousals) if arousals else 0.0
        avg_f0_var = sum(f0_vars) / len(f0_vars) if f0_vars else 0.0
        avg_ld_var = sum(ld_vars) / len(ld_vars) if ld_vars else 0.0
        
        best = max(matches, key=lambda m: m["dim_arousal"])
        example_str = f"\"{best['Text']}\" ({best['SpeakerName']}, {best['Timestamp']})"
        
        results.append({
            "question": q,
            "count": len(matches),
            "avg_arousal": avg_arousal,
            "avg_f0_var": avg_f0_var,
            "avg_ld_var": avg_ld_var,
            "example": example_str,
        })
        
    results.sort(key=lambda x: (x["avg_arousal"], x["count"]), reverse=True)
    return results


def _calculate_acoustic_summary_text(sinc_df: pd.DataFrame) -> str:
    if sinc_df.empty:
        return "Nenhuma métrica acústica disponível."
        
    # Dominância entra junto com ativação e valência: é o trio VAD completo, e
    # sem ela não se separa uma crítica firme de um desabafo hesitante.
    metrics = ["f0_media", "f0_variacao", "loudness_media", "loudness_variacao", "speaking_rate", "dim_arousal", "dim_valence", "dim_dominance"]
    available = [m for m in metrics if m in sinc_df.columns]
    
    if not available:
        return "Nenhuma métrica compatível disponível."
        
    agg_sess = sinc_df.groupby("session_id")[available].mean().reset_index()
    lines = ["### Médias por Áudio", ""]
    cols_header = "| Áudio | " + " | ".join(available) + " |"
    cols_sep = "|---| " + " | ".join(["---"] * len(available)) + " |"
    lines.append(cols_header)
    lines.append(cols_sep)
    for _, row in agg_sess.iterrows():
        row_str = f"| {row['session_id']} | " + " | ".join(f"{row[m]:.3f}" if pd.notna(row[m]) else "-" for m in available) + " |"
        lines.append(row_str)
        
    lines.append("")
    
    if "SpeakerName" in sinc_df.columns:
        agg_spk = sinc_df.groupby("SpeakerName")[available].mean().reset_index()
        lines.append("### Médias por Locutor")
        lines.append("")
        cols_header = "| Locutor | " + " | ".join(available) + " |"
        lines.append(cols_header)
        lines.append(cols_sep)
        for _, row in agg_spk.iterrows():
            row_str = f"| {row['SpeakerName']} | " + " | ".join(f"{row[m]:.3f}" if pd.notna(row[m]) else "-" for m in available) + " |"
            lines.append(row_str)
            
    return "\n".join(lines)


# ------------------------------------------------------------------
# Carregar projeto
# ------------------------------------------------------------------
project_id = st.session_state.get("pros_project_id")
if not project_id:
    st.warning("Nenhum projeto selecionado. Volte a lista de projetos.")
    if st.button("<- Projetos"):
        st.switch_page("modules/prosodia/projetos.py")
    st.stop()

project = get_project(project_id)
if not project:
    st.session_state.pop("pros_project_id", None)
    st.error("Projeto nao encontrado.")
    if st.button("<- Projetos"):
        st.switch_page("modules/prosodia/projetos.py")
    st.stop()

audios = get_audios_for_interviews(project_id)

# ------------------------------------------------------------------
# Header
# ------------------------------------------------------------------
ui.inject_theme()
st.page_link("modules/prosodia/resumo.py", label="Voltar para Resumo", icon=":material/arrow_back:")
ui.breadcrumb_nav(
    ("NencBoost", "modules/prosodia/projetos.py"),
    (project.get("name", ""), "modules/prosodia/resumo.py"),
    ("Análise Geral", None),
)
page_title("chart-bar", "Análise Geral", "{} · {} áudios".format(project.get("name", ""), len(audios)))

# --------------------------------------------------------------------
# Exportacao para Power BI — desativada.
#
# O codigo fica: utils/prosodia_powerbi_export.py e
# tests/test_prosodia_powerbi_export.py seguem intactos e testados. So
# nao ha caminho ate eles pela interface. Para reativar, descomente
# este bloco e o import de export_project_to_powerbi_excel no topo.
# --------------------------------------------------------------------
# st.divider()
# st.subheader("Exportação para Power BI")
# st.caption(
#     "Baixe todas as tabelas relacionáveis do projeto, incluindo históricos de "
#     "análises, verificações de qualidade e ativações prosódicas."
# )
# try:
#     powerbi_workbook, powerbi_filename = export_project_to_powerbi_excel(project_id)
# except ValueError as error:
#     st.error(f"Não foi possível preparar a exportação: {error}")
# else:
#     st.download_button(
#         "Exportar para Power BI (.xlsx)",
#         data=powerbi_workbook,
#         file_name=powerbi_filename,
#         mime=(
#             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
#         ),
#         key=f"download_powerbi_{project_id}",
#         type="primary",
#         use_container_width=False,
#     )
#
# with st.expander("Guia de importação e relacionamentos"):
#     st.markdown(
#         """
# 1. No Power BI Desktop, selecione **Obter Dados > Excel** e escolha o arquivo
#    exportado. Importe todas as abas.
# 2. Use `Projeto[id]` como a dimensão principal e relacione-o com
#    `Perguntas_Projeto[project_id]`, `Entrevistas[project_id]` e
#    `Analises_Projeto[project_id]`.
# 3. Use `Entrevistas[id]` para relacionar `Segmentos_VAD`, `Transcricoes`,
#    `Dados_Sincronizados`, `Analises_Entrevista`,
#    `Verificacoes_Qualidade` e `Momentos_Alta_Ativacao` por `audio_id`.
# 4. Relacione as tabelas filhas de IA e qualidade pelos IDs de origem:
#    `Citacoes_Analise_Entrevista[analysis_id]`,
#    `Citacoes_Analise_Projeto[project_analysis_id]`,
#    `Checks_Qualidade[quality_check_id]` e
#    `Cobertura_Perguntas[quality_check_id]`.
#
# As abas com sufixo numérico, como `Dados_Sincronizados_2`, são continuações da
# mesma tabela quando o limite de linhas do Excel é atingido. Anexe-as no Power
# Query antes de criar os relacionamentos.
#         """
#     )
#
if not audios:
    st.info("Nenhum áudio disponível para análise geral. Faça uploads primeiro.")
    st.stop()

# ------------------------------------------------------------------
# Filtros: valem para a página inteira, inclusive para o que vai à IA
# ------------------------------------------------------------------
audios_projeto = audios
_inicio, _fim = overview.period_bounds(audios_projeto)
with st.container(border=True):
    f_qr, f_periodo, f_origem, f_total = st.columns([2, 2, 2, 1.4], vertical_alignment="bottom")
    filtro_qr = f_qr.multiselect(
        "QR code", overview.qr_options(audios_projeto), placeholder="Todos", key="prj_f_qr"
    )
    filtro_periodo = overview.as_period(f_periodo.date_input(
        "Período", value=(_inicio, _fim), min_value=_inicio, max_value=_fim,
        format="DD/MM/YYYY", key="prj_f_periodo",
    ))
    filtro_origem = f_origem.multiselect(
        "Origem", list(overview.ORIGENS), format_func=overview.ORIGENS.get,
        placeholder="WhatsApp e upload", key="prj_f_origem",
    )
    audios = overview.filter_audios(audios_projeto, filtro_qr, filtro_periodo, filtro_origem)
    f_total.markdown(
        '<div style="text-align:right;font-size:.78rem;color:var(--nenc-muted);padding-bottom:.6rem">'
        "{} de {} áudios</div>".format(len(audios), len(audios_projeto)),
        unsafe_allow_html=True,
    )

# O recorte fica escrito na análise por IA salva; vazio sem filtro. Marcar
# todos os QR codes é o mesmo que não filtrar por QR.
recorte = overview.filter_description(
    [] if set(filtro_qr) == set(overview.qr_options(audios_projeto)) else filtro_qr,
    filtro_periodo, filtro_origem, (_inicio, _fim),
)

if not audios:
    st.info("Nenhum áudio atende aos filtros.")
    st.stop()

# A régua da voz do índice combinado é o projeto inteiro, com ou sem filtro:
# o índice de um áudio não muda com o recorte e bate com o Resumo e com a
# página do áudio. O filtro só escolhe quais áudios entram na página.
all_vad, all_tr, all_sinc = _load_project_frames(project_id, audios_projeto)
referencia_voz = referencia_valencia(all_sinc)
# A Análise Geral também mede contra o projeto inteiro (aliases, régua da voz
# e terços da Matriz) e conta só o recorte: ela recebe os dois.
tr_projeto, sinc_projeto = all_tr, all_sinc
if len(audios) < len(audios_projeto):
    _sessoes = {str(a.get("session_id") or "") for a in audios}
    all_vad, all_tr, all_sinc = (
        df[df["session_id"].astype(str).isin(_sessoes)].reset_index(drop=True)
        if "session_id" in df.columns else df
        for df in (all_vad, all_tr, all_sinc)
    )

# ------------------------------------------------------------------
# Painel: números, distribuição, recortes e o que olhar de perto
# ------------------------------------------------------------------


def _linhas_escolhidas(evento) -> list:
    selecao = getattr(evento, "selection", None) if evento else None
    if selecao is None:
        return []
    if isinstance(selecao, dict):
        return selecao.get("rows", [])
    return getattr(selecao, "rows", []) or []


def _inicio_do_trecho(linha):
    """Segundo em que o trecho começa: `seconds`, senão `start_s`."""
    for coluna in ("seconds", "start_s"):
        valor = linha.get(coluna)
        if valor is not None and pd.notna(valor):
            return float(valor)
    return None


def _abrir_na_timeline(linha, pergunta: str, origem: str, timestamp: str) -> None:
    """Grava o foco e cruza para o nível do áudio (ver `app.py`)."""
    sessao = linha.get("session_id")
    alvo = next((a for a in audios if a.get("session_id") == sessao), None)
    if not alvo:
        st.error("Não foi possível localizar este áudio.")
        return
    st.session_state["pros_audio_id"] = alvo["id"]
    st.session_state["pros_timeline_focus"] = {
        "audio_id": alvo["id"],
        "session_id": sessao,
        "question": pergunta,
        "seconds": _inicio_do_trecho(linha),
        "timestamp": timestamp,
        "speaker": str(linha.get("SpeakerName", "")),
        "text": str(linha.get("Text", "")),
        "source": origem,
    }
    st.session_state["_navigate_to"] = "modules/prosodia/audio.py"
    st.rerun()


n_interviews = len(audios)
total_speech = float(all_vad["duration"].sum()) if not all_vad.empty and "duration" in all_vad.columns else 0.0
cov_total = int(sum(int(a.get("coverage_total", 0)) for a in audios))
ai_found = int(sum(int(a.get("coverage_ai_found", 0)) for a in audios))

project_divergences = detectar_divergencias(all_sinc)
indice_por_audio = indice_combinado_por_grupo(all_sinc, referencia_voz, "session_id")
indice_projeto = indice_combinado_por_grupo(all_sinc, referencia_voz, None)
qualitativa = normalize_project_type(project.get("tipo_projeto")) == ENTREVISTA_QUALITATIVA

_geral = indice_projeto.iloc[0] if not indice_projeto.empty else None
_horas, _resto = divmod(int(total_speech), 3600)
_fala = "{}h {:02d}min de fala".format(_horas, _resto // 60) if _horas else "{} min de fala".format(_resto // 60)
_div_audios = project_divergences["session_id"].nunique() if not project_divergences.empty else 0
st.markdown(
    '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:.7rem;margin:.4rem 0 .2rem">'
    + overview.kpi_html("Áudios", str(n_interviews), _fala)
    + overview.kpi_html(
        "Índice combinado",
        "{:+.2f}".format(_geral["indice"]).replace(".", ",") if _geral is not None else "—",
        "texto + voz, −1 a +1", accent=True)
    + overview.kpi_html(
        "Tempo positivo",
        "{:.0%}".format(_geral["positivo"]) if _geral is not None else "—",
        "{:.0%} negativo".format(_geral["negativo"]) if _geral is not None else "sem sentimento do texto")
    + overview.kpi_html("Voz × texto", str(len(project_divergences)), "divergências em {} áudios".format(_div_audios))
    + overview.kpi_html(
        "Cobertura do roteiro",
        "{:.0%}".format(ai_found / cov_total) if cov_total else "—",
        "perguntas respondidas (IA)")
    + "</div>",
    unsafe_allow_html=True,
)

# -- 01 ---------------------------------------------------------------
st.markdown(overview.section_html("01", "Como o sentimento se distribui"), unsafe_allow_html=True)
if indice_por_audio.empty:
    st.caption("Sem sentimento do texto e valência vocal nos mesmos áudios: o índice combinado não pode ser calculado.")
else:
    c_hist, c_disp = st.columns(2, gap="small")
    with c_hist:
        with st.container(border=True):
            st.markdown(overview.card_title(
                "Índice combinado por áudio",
                "como os {} áudios se espalham de −1 a +1".format(len(indice_por_audio))), unsafe_allow_html=True)
            st.plotly_chart(overview.fig_index_histogram(indice_por_audio), width="stretch", key="prj_hist")
    with c_disp:
        with st.container(border=True):
            st.markdown(overview.card_title(
                "Texto × voz", "cada ponto é um áudio: nota do texto e voz em relação ao projeto"),
                unsafe_allow_html=True)
            st.plotly_chart(overview.fig_text_voice(indice_por_audio, audios), width="stretch", key="prj_disp")
    with st.expander("Tabela do índice por áudio"):
        st.dataframe(
            pd.DataFrame({
                "Áudio": indice_por_audio["grupo"],
                "Trechos": indice_por_audio["trechos"],
                "Texto": indice_por_audio["texto"].map(lambda v: f"{v:+.2f}"),
                "Voz": indice_por_audio["voz"].map(lambda v: f"{v:+.2f}"),
                "Índice": indice_por_audio["indice"].map(lambda v: f"{v:+.2f}"),
                "Positivo": indice_por_audio["positivo"].map(lambda v: f"{v:.0%}"),
                "Neutro": indice_por_audio["neutro"].map(lambda v: f"{v:.0%}"),
                "Negativo": indice_por_audio["negativo"].map(lambda v: f"{v:.0%}"),
            }),
            width="stretch",
            hide_index=True,
        )
        st.caption(
            "Metade a nota do texto, metade a valência da voz em relação a todos os áudios do projeto, "
            "com ou sem filtro. Fatias em proporção do tempo de fala; neutro é |índice| < 0,2."
        )

# -- 02 ---------------------------------------------------------------
st.markdown(overview.section_html("02", "Onde ele muda"), unsafe_allow_html=True)
resumo_qr = overview.qr_summary(audios, indice_por_audio, all_sinc, project_divergences)
with st.container(border=True):
    st.markdown(overview.card_title(
        "Por QR code", "qual ponto de contato puxa o sentimento para cima ou para baixo"), unsafe_allow_html=True)
    if resumo_qr["Índice"].notna().any():
        st.plotly_chart(overview.fig_qr_index(resumo_qr), width="stretch", key="prj_qr")
    st.dataframe(
        resumo_qr,
        hide_index=True,
        width="stretch",
        column_config={
            "Índice": st.column_config.NumberColumn(format="%+.2f"),
            "Positivo": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
        },
    )

top_moments = _extract_high_activation_moments(all_sinc, top_n=15) if not all_sinc.empty else pd.DataFrame()
grouped_topics = []
if not top_moments.empty:
    moments_list = [
        {
            "session_id": str(row.get("session_id", "")),
            "SpeakerName": _limpo(row.get("SpeakerName")) or "Desconhecido",
            "Timestamp": _tempo_do_momento(row),
            "Text": _limpo(row.get("Text", "")),
            "dim_arousal": float(row.get("dim_arousal", 0.0)) if pd.notna(row.get("dim_arousal")) else 0.0,
            "topic": extract_topic_from_text(_limpo(row.get("Text", ""))),
        }
        for _, row in top_moments.iterrows()
    ]
    grouped_topics = _group_similar_topics(moments_list)

c_semana, c_temas = st.columns([1.25, 1], gap="small")
with c_semana:
    with st.container(border=True):
        st.markdown(overview.card_title(
            "Ao longo da coleta", "índice combinado por semana, com as entradas ao fundo"), unsafe_allow_html=True)
        serie = overview.weekly(audios, indice_por_audio)
        if len(serie) >= 2:
            st.plotly_chart(overview.fig_weekly(serie), width="stretch", key="prj_semana")
        else:
            st.caption("Menos de duas semanas de coleta no filtro.")
with c_temas:
    with st.container(border=True):
        st.markdown(overview.card_title(
            "Temas que mais mobilizam", "momentos de maior ativação, agrupados por tema"), unsafe_allow_html=True)
        if grouped_topics:
            df_grouped = pd.DataFrame(grouped_topics)
            df_grouped.columns = ["Tópico Consolidado", "Ocorrências", "Arousal Médio", "Áudios Relacionados", "Exemplo de Destaque"]
            df_grouped["Arousal Médio"] = df_grouped["Arousal Médio"].map(lambda v: f"{v:.2f}")
            st.dataframe(df_grouped[["Tópico Consolidado", "Ocorrências", "Arousal Médio"]], hide_index=True, width="stretch")
            with st.expander("Exemplos e áudios"):
                st.dataframe(df_grouped, hide_index=True, width="stretch")
        else:
            st.caption("Nenhum momento de alta ativação no filtro.")

# Só a Entrevista tem roteiro: as perguntas aparecem aqui e no apêndice da
# Análise Geral.
q_activations = []
if qualitativa:
    if not all_tr.empty and "SpeakerName" in all_tr.columns:
        with st.expander("Participação por locutor"):
            st.plotly_chart(
                create_speaker_stats(all_tr, session_id=None, title="Participação geral por locutor"),
                width="stretch",
            )
    q_activations = _calculate_questions_activation(audios, all_sinc) if not all_sinc.empty else []
    if q_activations:
        with st.expander("Perguntas com maior ativação prosódica"):
            df_q = pd.DataFrame(q_activations)
            df_q.columns = ["Pergunta", "Respostas Cobertas", "Arousal Médio", "Variação Pitch Médio",
                            "Variação Volume Médio", "Destaque (Maior Arousal)"]
            for coluna in ("Arousal Médio", "Variação Pitch Médio", "Variação Volume Médio"):
                df_q[coluna] = df_q[coluna].map(lambda v: f"{v:.2f}")
            st.dataframe(df_q, width="stretch", hide_index=True)

with st.expander("Médias acústicas por áudio e palavras mais frequentes"):
    if not all_sinc.empty:
        st.plotly_chart(
            create_project_acoustic_comparison(all_sinc, title="Média de indicadores por áudio", tr_df=all_tr),
            width="stretch",
        )
    if not all_tr.empty:
        st.plotly_chart(
            create_project_word_ranking(all_tr, title="Palavras mais mencionadas", top_n=15),
            width="stretch",
        )

# -- 03 ---------------------------------------------------------------
st.markdown(overview.section_html("03", "O que olhar de perto"), unsafe_allow_html=True)
with st.container(border=True):
    st.markdown(overview.card_title(
        "Onde texto e voz discordam",
        "{} trechos em {} áudios, mais fortes primeiro".format(len(project_divergences), _div_audios)),
        unsafe_allow_html=True)
    if project_divergences.empty:
        st.caption(
            "Nenhuma divergência acima dos limiares nos áudios do filtro."
            if tem_sentimento_texto(all_sinc)
            else "Os áudios do filtro não trazem sentimento do texto."
        )
    else:
        _qr_da_sessao = {str(a.get("session_id") or ""): overview.qr_label(a) for a in audios}
        df_div = pd.DataFrame({
            "QR code": project_divergences["session_id"].astype(str).map(_qr_da_sessao),
            "Áudio": project_divergences["session_id"],
            "Tempo": [
                formatar_tempo(inicio) or ts
                for inicio, ts in zip(project_divergences["start_s"], project_divergences["Timestamp"])
            ],
            "Fala": project_divergences["Text"],
            "Texto": project_divergences["sentimento_texto"].map(lambda v: f"{v:+.2f}"),
            "Voz": [
                f"{v:+.2f} (z {z:+.1f})"
                for v, z in zip(project_divergences["valencia_voz"], project_divergences["z_valencia"])
            ],
            "Leitura": project_divergences["tipo"],
        })
        div_event = st.dataframe(df_div, width="stretch", hide_index=True, on_select="rerun",
                                 selection_mode="single-row", key="prj_div_select")
        div_rows = _linhas_escolhidas(div_event)
        if st.button("Abrir na timeline", key="prj_div_open", disabled=not div_rows,
                     help="Selecione uma linha da tabela."):
            linha = project_divergences.iloc[int(div_rows[0])]
            _abrir_na_timeline(linha, "Divergência voz × texto", linha.get("tipo", "Divergência voz × texto"),
                               str(linha.get("Timestamp", "")))

with st.expander("Momentos de maior ativação prosódica"):
    if top_moments.empty:
        st.caption("Não foi possível extrair momentos de alta ativação no filtro.")
    else:
        textos = top_moments["Text"].fillna("") if "Text" in top_moments.columns else pd.Series([""] * len(top_moments))
        df_show = pd.DataFrame({
            "Tópico": [extract_topic_from_text(t) for t in textos],
            "Áudio": top_moments["session_id"],
            "Tempo": [_tempo_do_momento(row) for _, row in top_moments.iterrows()],
            "Fala": textos.astype(str).str.strip(),
            "Arousal": top_moments["dim_arousal"].fillna(0.0).map(lambda v: f"{v:.2f}")
            if "dim_arousal" in top_moments.columns else "0.00",
        })
        mom_event = st.dataframe(df_show, width="stretch", hide_index=True, on_select="rerun",
                                 selection_mode="single-row", key="prj_top_moments_select")
        mom_rows = _linhas_escolhidas(mom_event)
        if st.button("Abrir na timeline", key="prj_mom_open", disabled=not mom_rows,
                     help="Selecione uma linha da tabela."):
            momento = top_moments.iloc[int(mom_rows[0])]
            _abrir_na_timeline(momento, "Momento de Alta Ativação Geral", "Filtro de Ativação Consolidado",
                               _tempo_do_momento(momento))

proj_ctx = geracao.contexto_do_projeto(project)


def _resumo_da_geracao(analise: dict) -> None:
    """O que a última geração deixou para a tela: a verificação e os avisos.

    A página recarrega depois de salvar, e o banco guarda só a análise: o
    resto fica na sessão, e só vale para a análise que esta geração salvou.
    """
    info = st.session_state.get(f"prj_geracao_{project_id}")
    if not info or info.get("analise_id") != analise.get("id"):
        return
    if info["taxonomia_da_ia"]:
        st.info(
            "O projeto não tinha taxonomia: a IA propôs uma provisória, que ficou salva na "
            "Preparação para revisar com a cliente."
        )
    if info["falhas"] == 1:
        st.warning(
            "1 áudio ficou fora da contagem porque a classificação dele falhou. Ele volta a ser "
            "classificado na próxima geração."
        )
    elif info["falhas"]:
        st.warning(
            "{} áudios ficaram fora da contagem porque a classificação deles falhou. Eles voltam a "
            "ser classificados na próxima geração.".format(info["falhas"])
        )
    problemas = info["problemas"]
    if problemas:
        with st.expander(
            "Verificação das referências: {} para revisar".format(
                "1 ponto" if len(problemas) == 1 else "{} pontos".format(len(problemas))
            ),
            expanded=True,
        ):
            st.markdown("\n".join("- " + problema for problema in problemas))
            st.caption(
                "A verificação confere o texto da IA contra as tabelas calculadas e não impede "
                "salvar: revise estes pontos antes de enviar o relatório."
            )
    else:
        st.caption("Verificação das referências: o texto da IA bate com as tabelas.")
    kb_ok, kb_msg = info["kb"]
    if kb_ok:
        st.caption("Base de Conhecimento atualizada com: {}".format(kb_msg))
    elif info["use_kb"]:
        st.warning("A análise foi salva, mas não foi possível enviá-la para a base: {}".format(kb_msg))


# ------------------------------------------------------------------
# Analise Geral por IA
# ------------------------------------------------------------------
st.markdown(overview.section_html("04", "Análise por IA"), unsafe_allow_html=True)
with st.container(border=True):
    c_base, c_legenda, c_modelo = st.columns([1.2, 1.2, 2], vertical_alignment="top")
    use_kb = c_base.toggle("Usar Base de Conhecimento", value=True, key="prj_ai_kb")
    incluir_legenda = c_legenda.toggle(
        "Telefones no relatório", value=False, key="prj_ai_legenda",
        help="A legenda Áudio NN → sessão traz o telefone de quem mandou o áudio. "
        "Desligada, ela fica só na tela, abaixo do relatório.",
    )
    with c_modelo:
        ai_provider_id, ai_model = ui.ai_model_selector("prj_ai_model", use_kb=use_kb)
    st.caption(
        "A geração classifica nos temas da taxonomia os áudios do recorte que ainda não foram "
        "classificados, faz as contas em código e pede à IA só o texto."
        + (
            " O projeto ainda não tem taxonomia: a IA propõe uma provisória, que fica salva na Preparação."
            if not str(project.get("taxonomia") or "").strip()
            else ""
        )
    )
    if recorte:
        st.caption(
            "A análise gerada agora considera só o recorte ({}) e ele fica registrado no texto "
            "salvo, no PDF e na Base de Conhecimento.".format(recorte)
        )

latest_analysis = get_latest_project_analysis(project_id)

if latest_analysis:
    st.caption(
        f"Ultima analise geral: {latest_analysis['created_at']} - Modelo: {latest_analysis.get('model', '-')}"
    )
    _resumo_da_geracao(latest_analysis)
    st.markdown(latest_analysis.get("analysis_text", ""))

    analysis_md = _build_project_analysis_markdown(
        project_name=project.get("name", ""),
        model=latest_analysis.get("model", ""),
        created_at=latest_analysis.get("created_at", ""),
        text=latest_analysis.get("analysis_text", ""),
        citations=latest_analysis.get("citations", []),
    )
    col_d1, col_d2 = st.columns(2)
    with col_d1:
        st.download_button(
            "Download Analise Geral (.md)",
            data=analysis_md,
            file_name=f"analise_geral_{_slugify(project.get('name', 'projeto'))}.md",
            mime="text/markdown",
            key="download_latest_project_analysis",
            use_container_width=True,
        )
    with col_d2:
        pdf_data = _build_project_analysis_pdf(
            project_name=project.get("name", ""),
            project_info=proj_ctx,
            model=latest_analysis.get("model", ""),
            created_at=latest_analysis.get("created_at", ""),
            text=latest_analysis.get("analysis_text", ""),
            citations=latest_analysis.get("citations", []),
            acoustic_summary=_calculate_acoustic_summary_text(all_sinc) if not all_sinc.empty else "",
        )
        st.download_button(
            "Download Analise Geral (.pdf)",
            data=pdf_data,
            file_name=f"analise_geral_{_slugify(project.get('name', 'projeto'))}.pdf",
            mime="application/pdf",
            key="download_latest_project_analysis_pdf",
            use_container_width=True,
        )

    with st.expander("Referencias da Base de Conhecimento"):
        ui.knowledge_base_references(
            {
                "citations": latest_analysis.get("citations", []),
                # A tela recarrega depois de salvar e o banco guarda so as
                # citacoes: o que a busca fez fica na sessao desta rodada.
                "search": st.session_state.get(
                    f"pr_kb_search_project_{project_id}", {}
                ),
            }
        )

    # As análises da v2 citam "Áudio 07"; a legenda com a sessão, que traz o
    # telefone, fica aqui quando não vai no relatório. O alias é o do projeto
    # inteiro, o mesmo que o relatório usou.
    _cobertos = (latest_analysis.get("temas") or {}).get("audios") or []
    if _cobertos:
        with st.expander("Legenda dos áudios (Áudio NN → sessão)"):
            st.dataframe(
                pd.DataFrame(
                    geracao.legenda(audios_projeto, _cobertos),
                    columns=["Áudio", "Sessão", "QR code", "Chegada"],
                ),
                hide_index=True,
                width="stretch",
            )
            st.caption("A sessão traz o telefone de quem mandou o áudio: não a repasse à cliente.")

    chat_key = f"prj_chat_history_{project_id}"
    confirm_key = f"prj_an_confirm_{project_id}"

    history = get_project_analyses(project_id)
    with st.expander(f"Historico de analises gerais ({len(history)} registros)"):
        if pode_editar and len(history) > 1:
            if st.button("Apagar análises anteriores", key=f"prj_an_del_old_{project_id}"):
                st.session_state[confirm_key] = {
                    "ids": [an["id"] for an in history[1:]],
                    "label": f"as {len(history) - 1} análises anteriores (a mais recente será mantida)",
                }
                st.rerun()
        for an in history:
            col_h, col_del = st.columns([5, 1])
            with col_h:
                st.markdown(f"**{an['created_at']} - {an.get('model', '-')}**")
            with col_del:
                if pode_editar and st.button("Excluir", key=f"prj_an_del_{an['id']}", width="stretch"):
                    st.session_state[confirm_key] = {
                        "ids": [an["id"]],
                        "label": f"a análise de **{an['created_at']}**",
                    }
                    st.rerun()
            text = an.get("analysis_text", "")
            st.markdown(text[:500] + ("..." if len(text) > 500 else ""))
            st.divider()

    pending = st.session_state.get(confirm_key)
    if pending and pode_editar:
        st.warning(f"Excluir {pending['label']}? Esta ação não pode ser desfeita.")
        cc1, cc2 = st.columns(2)
        with cc1:
            if st.button("Confirmar exclusão", key=f"prj_an_del_yes_{project_id}", width="stretch"):
                delete_project_analyses(project_id, pending["ids"])
                # O chat conversa sobre a analise mais recente: se ela saiu,
                # a conversa perdeu o contexto.
                if latest_analysis["id"] in pending["ids"]:
                    st.session_state.pop(chat_key, None)
                st.session_state.pop(confirm_key, None)
                st.rerun()
        with cc2:
            if st.button("Cancelar", key=f"prj_an_del_no_{project_id}", width="stretch"):
                st.session_state.pop(confirm_key, None)
                st.rerun()

    st.divider()
    if chat_key not in st.session_state:
        st.session_state[chat_key] = []

    col_t, col_clear = st.columns([5, 1], vertical_alignment="bottom")
    with col_t:
        st.subheader("Chat com a IA sobre o Projeto")
    with col_clear:
        if st.session_state[chat_key] and st.button(
            "Limpar conversa", key=f"prj_chat_clear_{project_id}", width="stretch"
        ):
            st.session_state[chat_key] = []
            st.rerun()
    st.markdown(
        "Tire dúvidas ou peça detalhamentos específicos sobre o relatório geral gerado acima."
    )

    for msg in st.session_state[chat_key]:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])
            
    if prompt := st.chat_input("Pergunte algo sobre a análise geral...", key=f"prj_chat_input_{project_id}"):
        with st.chat_message("user"):
            st.write(prompt)
        st.session_state[chat_key].append({"role": "user", "content": prompt})
        
        if not ai_provider_id:
            st.error("Configure uma chave de API no .env para habilitar o chat.")
        else:
            with st.chat_message("assistant"):
                with st.spinner("Pensando..."):
                    try:
                        report_context = latest_analysis.get("analysis_text", "")
                        sys_msg = (
                            "Você é um consultor analítico especialista em prosódia e comportamento humano. "
                            "O usuário deseja fazer perguntas sobre a Análise Geral do Projeto consolidada abaixo. "
                            "Responda de forma concisa, objetiva e baseada nas informações do relatório.\n\n"
                            f"--- RELATÓRIO DO PROJETO ---\n{report_context}\n-----------------------------"
                        )

                        if ai_provider_id == PROVIDER_OPENAI:
                            chat_user_prompt = ""
                            for h in st.session_state[chat_key][:-1]:
                                role_name = "Usuário" if h["role"] == "user" else "Assistente"
                                chat_user_prompt += f"{role_name}: {h['content']}\n\n"
                            chat_user_prompt += f"Usuário: {prompt}"
                            
                            chat_vs_id = get_prosodia_vector_store_id() if use_kb else None
                            
                            result = ai_create_analysis(
                                system_prompt=sys_msg,
                                user_prompt=chat_user_prompt,
                                model=ai_model,
                                vector_store_id=chat_vs_id,
                                kb_filter=build_kb_filter(project_id),
                                temperature=0.7,
                                max_tokens=1500,
                            )
                            answer = result.get("text", "")
                            
                            citations = result.get("citations", [])
                            if citations:
                                answer += "\n\n**Referências da Base de Conhecimento:**"
                                for cit in citations:
                                    filename = cit.get("filename") or "Documento"
                                    quote = cit.get("quote")
                                    if quote:
                                        answer += f"\n- *{filename}*: \"{quote}\""
                                    else:
                                        answer += f"\n- *{filename}*"
                        else:
                            messages = [
                                {"role": h["role"], "content": h["content"]}
                                for h in st.session_state[chat_key]
                            ]
                            answer = chat_completion(
                                ai_provider_id,
                                ai_model,
                                sys_msg,
                                messages,
                                temperature=0.7,
                                max_tokens=1500,
                            )
                            
                        st.write(answer)
                        st.session_state[chat_key].append({"role": "assistant", "content": answer})
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erro ao obter resposta da IA: {e}")
else:
    st.info("Nenhuma análise geral disponível. Clique em Gerar Análise Geral.")

btn_label = "Regenerar Análise Geral" if latest_analysis else "Gerar Análise Geral"
if st.button(
    btn_label,
    type="primary",
    disabled=not pode_editar,
    help=None if pode_editar else "Seu perfil só consulta: gerar grava a taxonomia, a classificação e a análise.",
):
    if not ai_provider_id:
        st.error(
            "Configure uma chave de API no .env (OPENAI_API_KEY, GROQ_API_KEY "
            "ou ANTHROPIC_API_KEY) e reinicie o app."
        )
        st.stop()

    vs_id = get_prosodia_vector_store_id() if use_kb else None
    with st.status("Gerando a Análise Geral…", expanded=True) as painel:
        _progresso = {}

        def _avisar(etapa: int, texto: str) -> None:
            nome = geracao.ETAPAS[etapa - 1]
            painel.update(label="{}/{} · {}".format(etapa, len(geracao.ETAPAS), nome))
            st.markdown("**{}.** {}".format(nome, texto))

        def _ao_classificar(feitos: int, total: int) -> None:
            if not total:
                return
            texto = "{} de {} áudios classificados".format(feitos, total)
            if "barra" not in _progresso:
                _progresso["barra"] = st.progress(0.0, text=texto)
            _progresso["barra"].progress(feitos / total, text=texto)

        try:
            responder, modelo_da_classificacao = geracao.responder_da_classificacao(ai_provider_id, ai_model)
            resultado = geracao.gerar_analise_geral(
                project,
                audios_projeto,
                [a["id"] for a in audios],
                geracao.recorte_da_pagina(
                    filtro_qr, overview.qr_options(audios_projeto), filtro_periodo, filtro_origem, recorte
                ),
                tr_projeto,
                sinc_projeto,
                responder,
                modelo_da_classificacao,
                geracao.gerador_do_relatorio(ai_provider_id, ai_model, project_id, vs_id),
                ai_model,
                perguntas_ativacao=q_activations,
                incluir_legenda=incluir_legenda,
                avisar=_avisar,
                ao_classificar=_ao_classificar,
            )
        except Exception as e:
            _LOGGER.exception("Falha ao gerar a Análise Geral do projeto %s", project_id)
            painel.update(label="A Análise Geral não foi gerada", state="error")
            st.error(f"Erro ao gerar a Análise Geral: {e}")
            st.stop()
        painel.update(label="Análise Geral salva", state="complete")

    agora = datetime.now()
    kb_doc_name = "analise_geral_{}_{}.md".format(
        _slugify(project.get("name", "projeto")), agora.strftime("%Y%m%d_%H%M%S")
    )
    kb_doc = _build_project_analysis_markdown(
        project_name=project.get("name", ""),
        model=ai_model,
        created_at=agora.strftime("%Y-%m-%d %H:%M:%S"),
        text=resultado.relatorio.texto,
        citations=resultado.relatorio.citacoes,
    )
    # A Análise Geral continua indo para a base, porque o chat e outras telas a
    # usam; é a geração seguinte que a deixa de fora (§11 da spec). Devolver a
    # análise só faz sentido para quem está usando a base.
    if use_kb:
        kb = _append_result_to_kb(kb_doc_name, kb_doc, project_id)
    else:
        kb = (False, "base de conhecimento desligada nesta análise")
    st.session_state[f"pr_kb_search_project_{project_id}"] = resultado.relatorio.busca
    st.session_state[f"prj_geracao_{project_id}"] = {
        "analise_id": resultado.analise_id,
        "problemas": list(resultado.relatorio.problemas),
        "falhas": len(resultado.classificacao.falhas),
        "taxonomia_da_ia": resultado.taxonomia_da_ia,
        "kb": kb,
        "use_kb": use_kb,
    }
    st.rerun()
