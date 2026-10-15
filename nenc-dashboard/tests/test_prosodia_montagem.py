"""Prompt, montagem e verificação da Análise Geral v2 (fase 4 da spec).

A IA recebe as tabelas prontas e escreve só o texto. O código separa esse
texto nas seções, monta o relatório na ordem fixa e confere as referências:
IDs, áudios, subtemas, frações e a leitura de voz de cada achado. A IA aqui é
sempre simulada, e o exemplo de saída da spec, que vai no prompt, faz o papel
de resposta certa.
"""

import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from tests.test_prosodia_relatorio import ATENDENTE, LOTACAO, MAQUINA, PRECO, _agregar, _audio, _linhas, _sessao, _tema
from utils import prosodia_prompts as prompts
from utils import prosodia_relatorio as rel
from utils.prosodia_project_types import ENTREVISTA_QUALITATIVA, PESQUISA_OPINIAO
from utils.prosodia_relatorio import (
    ABAIXO,
    ACIMA,
    MENCAO_ISOLADA,
    NO_HABITUAL,
    PADRAO,
    SINAL,
    Agregacao,
    AnaliseAnterior,
    AudioDoProjeto,
    Delta,
    Emergente,
    GrupoMatriz,
    LeituraVoz,
    LinhaMatriz,
    LinhaTema,
    Matriz,
    Recorte,
    Reguas,
    Termometro,
)
from utils.prosodia_temas import RespostaInvalida

SPEC = Path(__file__).resolve().parents[1] / "docs" / "SPEC_ANALISE_GERAL.md"


class IaFalsa:
    """Responde sempre o mesmo texto, no formato de generate_analysis."""

    def __init__(self, texto, citacoes=()):
        self.texto = texto
        self.citacoes = list(citacoes)
        self.chamadas = []

    def __call__(self, **kwargs):
        self.chamadas.append(kwargs)
        return {"text": self.texto, "citations": self.citacoes, "search": {"available": True, "searched": True}}


def _exemplo(audios_no_projeto=64, matriz=True, termometro=True):
    """A agregação do exemplo da spec (§10): lavanderias, 64 respondentes, 12 novos.

    Com `audios_no_projeto` acima de 64, os áudios a mais ficam fora do recorte.
    """
    audios = [AudioDoProjeto(i, _sessao(i, i), _linhas(), {"temas": []}) for i in range(1, audios_no_projeto + 1)]
    nomes = rel.aliases(audios)

    def tema(rotulo, n, faixa, polaridades, voz, anteriores, novos):
        return LinhaTema(rotulo, rotulo.split(" > ")[1], n, faixa, polaridades, voz, anteriores, novos)

    def linha(audio_id, texto, voz, indice, ativacao, *temas):
        return LinhaMatriz(audio_id, nomes[audio_id], texto, voz, indice, ativacao, temas)

    vinculo = GrupoMatriz(
        (
            linha(3, 0.82, 0.36, 0.59, 0.4, "Atendente da unidade", "Limpeza da loja"),
            linha(11, 0.74, 0.28, 0.51, 0.3, "Atendente da unidade"),
            linha(27, 0.66, 0.30, 0.48, 0.2, "Atendente da unidade", "Preço do ciclo"),
            linha(40, 0.70, 0.18, 0.44, 0.3, "Atendente da unidade", "Limpeza da loja"),
            linha(58, 0.61, 0.23, 0.42, 0.1, "Limpeza da loja"),
        ),
        5,
        (("Atendente da unidade", 4), ("Limpeza da loja", 3)),
    )
    risco = GrupoMatriz(
        (
            linha(8, -0.64, -0.21, -0.43, -0.48, "Falha no app de pagamento"),
            linha(19, -0.52, -0.18, -0.35, -0.41, "Espera por máquina livre", "Máquina fora de serviço"),
            linha(33, -0.45, -0.12, -0.29, -0.39, "Falha no app de pagamento", "Preço do ciclo"),
            linha(45, -0.58, -0.22, -0.40, -0.44, "Falha no app de pagamento"),
        ),
        4,
        (("Falha no app de pagamento", 3),),
    )
    return Agregacao(
        tipo_projeto=PESQUISA_OPINIAO,
        aliases=nomes,
        recorte=tuple(audios[:64]),
        contados=tuple(range(1, 65)),
        sem_fala=(),
        sem_classificacao=(),
        respondente_incerto=(),
        n_respondentes=64,
        temas=[
            tema("Equipamentos > Máquina fora de serviço", 19, PADRAO, {"negativa": 17, "mista": 2},
                 LeituraVoz(19, -0.3, 0.5, ABAIXO, "alta", "reforça"), 13, 6),
            tema("Atendimento > Atendente da unidade", 13, PADRAO, {"positiva": 12, "mista": 1},
                 LeituraVoz(13, 0.3, 0.5, ACIMA, "alta", "reforça"), 11, 2),
            tema("Pagamento > Falha no app de pagamento", 11, PADRAO, {"negativa": 11},
                 LeituraVoz(11, 0.0, -0.5, NO_HABITUAL, "baixa", "atenua"), 6, 5),
            tema("Ambiente > Limpeza da loja", 9, PADRAO, {"positiva": 8, "negativa": 1},
                 LeituraVoz(9, 0.0, -0.5, NO_HABITUAL, "baixa", "atenua"), 8, 1),
            tema("Ambiente > Espera por máquina livre", 6, SINAL, {"negativa": 6},
                 LeituraVoz(6, -0.3, 0.0, ABAIXO, "média", "reforça"), 5, 1),
            tema("Pagamento > Preço do ciclo", 4, SINAL, {"negativa": 3, "positiva": 1}, LeituraVoz(4), 4, 0),
            tema("Atendimento > Suporte pelo WhatsApp", 3, SINAL, {"negativa": 2, "positiva": 1}, LeituraVoz(3), 2, 1),
            tema("Equipamentos > Secagem incompleta", 2, MENCAO_ISOLADA, {"negativa": 2}, LeituraVoz(2), 2, 0),
        ],
        sem_mencao=[],
        emergentes=[
            Emergente("Estacionamento", "Ambiente", 2, {"negativa": 2}),
            Emergente("Cheiro do sabão", "Produtos", 1, {"positiva": 1}),
        ],
        trechos={},
        termometro=Termometro(64, -0.18, -1, 20, 19, 25) if termometro else Termometro(),
        matriz=Matriz(64, 0.40, -0.38, vinculo, risco) if matriz else Matriz(8),
        reguas=Reguas(),
        delta=Delta(AnaliseAnterior(datetime(2026, 9, 30, 18, 0), frozenset(range(1, 53))), 52, 12, 0),
    )


def _verificar(texto, agregacao=None):
    return rel.verificar_referencias(agregacao or _exemplo(), rel.separar_secoes(texto))


def _trocar(original, novo):
    """O exemplo da spec com um trecho trocado."""
    texto = prompts.EXEMPLO_ANALISE_GERAL
    assert original in texto, original
    return texto.replace(original, novo)


def _blocos_da_ia_na_spec():
    """As linhas que a spec marca como [IA] no exemplo de saída (§10)."""
    secao = SPEC.read_text(encoding="utf-8").split("## 10. Exemplo de saída", 1)[1].split("\n## 11.", 1)[0]
    linhas, da_ia = [], False
    for linha in secao.splitlines():
        if linha.startswith("### "):
            da_ia = "**[IA]**" in linha
        elif linha.startswith("**[IA]**"):
            linhas.append(linha[len("**[IA]**"):].strip())
        elif da_ia and linha.strip() and linha.strip() != "---":
            linhas.append(linha.strip())
    return linhas


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

class SistemaTests(unittest.TestCase):
    def test_structure_example_and_reminder_close_the_prompt(self):
        sistema = prompts.sistema_analise_geral(PESQUISA_OPINIAO)

        posicoes = [
            sistema.index(marca)
            for marca in (
                "Você é um consultor sênior",
                "## Tipo de Material",
                "## Leitura Multimodal",
                "## Referencial de Experiência do Cliente",
                "## Rigor, Evidência e Amostra",
                "## Estrutura de Resposta Obrigatória",
                "<exemplo_de_saida>",
                "</exemplo_de_saida>",
                "## Lembrete",
            )
        ]
        self.assertEqual(posicoes, sorted(posicoes))
        self.assertTrue(sistema.rstrip().endswith(
            "- Recomendação é ação rastreada: responde a achados ou hipóteses e diz como medir."
        ))
        self.assertEqual(len(sistema.split("## Lembrete", 1)[1].strip().splitlines()), 3)

    def test_one_prompt_with_an_addendum_per_type(self):
        opiniao = prompts.sistema_analise_geral(PESQUISA_OPINIAO)
        entrevista = prompts.sistema_analise_geral(ENTREVISTA_QUALITATIVA)

        self.assertIn("## Tipo de Material: Pesquisa de Opinião", opiniao)
        self.assertNotIn("## Tipo de Material: Entrevista", opiniao)
        self.assertIn("## Tipo de Material: Entrevista", entrevista)
        self.assertIn("inclui as falas do entrevistador", entrevista)
        self.assertEqual(prompts.sistema_analise_geral(None), entrevista)

        def sem_adendo(texto):
            antes, resto = texto.split("## Tipo de Material", 1)
            return antes + resto[resto.index("## Leitura Multimodal"):]

        self.assertEqual(sem_adendo(opiniao), sem_adendo(entrevista))

    def test_own_versions_of_the_shared_blocks(self):
        sistema = prompts.sistema_analise_geral(PESQUISA_OPINIAO)

        self.assertIn("Use só os números das tabelas recebidas", sistema)
        self.assertIn("interpretação prévia", sistema)
        self.assertIn("Nunca tire dela número", sistema)
        for bloco in ("Pico e fim", "Limitações e Próximos Passos", "Feche com um saldo"):
            with self.subTest(bloco=bloco):
                self.assertNotIn(bloco, sistema)
        # Os prompts das análises individuais não mudam.
        self.assertIn(prompts.PROSODIA_CX_FRAMEWORK, prompts.PROMPT_PESQUISA_OPINIAO)
        self.assertIn(prompts.PROSODIA_EVIDENCE_RULES, prompts.PROMPT_ENTREVISTA)
        self.assertIn("Pico e fim", prompts.PROSODIA_CX_FRAMEWORK)

    def test_the_example_is_the_one_in_the_spec(self):
        linhas = _blocos_da_ia_na_spec()

        self.assertGreater(len(linhas), 15)
        for linha in linhas:
            with self.subTest(linha=linha[:60]):
                self.assertIn(linha, prompts.EXEMPLO_ANALISE_GERAL)
        self.assertIn(
            "Ilustra formato e profundidade num setor fictício. Não reutilize temas, termos nem números.\n"
            "<exemplo_de_saida>\n" + prompts.EXEMPLO_ANALISE_GERAL + "</exemplo_de_saida>",
            prompts.sistema_analise_geral(PESQUISA_OPINIAO),
        )

    def test_the_example_uses_the_titles_of_the_structure(self):
        titulos = [linha for linha in prompts.EXEMPLO_ANALISE_GERAL.splitlines() if linha.startswith("### ")]

        self.assertEqual(titulos, ["### " + titulo for titulo in rel.TITULOS_DA_IA.values()])
        for titulo in titulos:
            self.assertIn(titulo, prompts.ESTRUTURA_ANALISE_GERAL)


def _projeto_pequeno():
    """12 respondentes, sem voz nem nota do texto: Matriz e Termômetro não avaliáveis."""
    audios = [_audio(i, _tema(MAQUINA)) for i in range(1, 7)]
    audios += [_audio(i, _tema(ATENDENTE, "positiva")) for i in range(7, 10)]
    audios += [_audio(10, _tema(LOTACAO)), _audio(11), _audio(12)]
    return audios


TEXTO_DO_PROJETO_PEQUENO = """\
### Resumo Executivo
Máquina fora de serviço é a dor de metade dos respondentes (A1, 6/12); o atendente aparece como sinal (A2, 3/12). \
A ação prioritária é a manutenção preventiva (R1).

### Achados
- **A1** · Equipamentos > Máquina fora de serviço · padrão, 6/12 — os seis falam da máquina parada (Áudio 01, \
00:00). Voz: não avaliável (0 trechos).
- **A2** · Atendimento > Atendente da unidade · sinal, 3/12 — elogios ao atendente (Áudio 07, 00:00). Voz: não \
avaliável (0 trechos).
- Menção isolada, sem hipótese própria: Lotação (1/12).

### Hipóteses
- **H1** — A máquina parada pesa mais que o atendimento na decisão de voltar. *Apoia-se em:* A1, A2. \
*Como validar:* perguntar na próxima coleta.

### Recomendações
- **R1** [Higiene] (Driver: Retenção · Área: Manutenção) — Revisar as máquinas toda semana. *Responde a:* A1, H1. \
*Como medir:* fatia do tema na próxima análise (hoje 6/12).

### Matriz de Destaques — leitura
Não avaliável.

### Termômetro — leitura
Não avaliável.
"""


class MensagemTests(unittest.TestCase):
    def test_blocks_follow_the_order_of_the_spec_and_the_task_closes(self):
        agregacao = _exemplo()
        cabecalho = rel.cabecalho_markdown(agregacao, "Lavanderias", Recorte(), 64, "validada")

        mensagem = rel.mensagem_para_ia(agregacao, {"nome": "Lavanderias", "especialidade": "Lavanderia"}, cabecalho)

        posicoes = [
            mensagem.index(marca)
            for marca in (
                "<contexto_projeto>", "<cabecalho>", "<tabela_temas>", "<trechos>", "<emergentes>", "<matriz>",
                "<termometro>", "<divergencias>", "<sumarios_individuais>", "## Tarefa",
            )
        ]
        self.assertEqual(posicoes, sorted(posicoes))
        self.assertIn("(interpretação prévia — não é fonte de achado)\n<sumarios_individuais>", mensagem)
        tabela = mensagem.split("<tabela_temas>", 1)[1].split("</tabela_temas>", 1)[0]
        self.assertNotIn("Temas emergentes", tabela)
        self.assertIn("Temas emergentes (fora da taxonomia): Estacionamento (Ambiente), 2/64", mensagem)

    def test_the_task_gives_each_finding_its_id_row_and_voice(self):
        agregacao = _exemplo()

        tarefa = rel.mensagem_para_ia(agregacao, {}, "cabeçalho").split("## Tarefa", 1)[1]

        self.assertIn(
            "   - A1 · Equipamentos > Máquina fora de serviço · padrão, 19/64. "
            "Voz: abaixo do habitual, ativação alta → reforça.",
            tarefa,
        )
        self.assertIn("   - A7 · Atendimento > Suporte pelo WhatsApp · sinal, 3/64. Voz: não avaliável (3 trechos).",
                      tarefa)
        self.assertNotIn("Secagem incompleta", tarefa)
        self.assertIn("Feche com uma linha, sem ID, para as menções isoladas e os temas emergentes.", tarefa)
        self.assertIn("5. **Matriz de Destaques — leitura**, para Vínculo forte e Risco de abandono silencioso.",
                      tarefa)
        self.assertIn("compara só 12 respondentes novos: trate as diferenças como variação observada", tarefa)
        self.assertNotIn("Leitura exploratória", tarefa)

    def test_the_task_says_what_does_not_apply(self):
        agregacao = _agregar(_projeto_pequeno())

        tarefa = rel.mensagem_para_ia(agregacao, {}, "cabeçalho").split("## Tarefa", 1)[1]

        self.assertIn("Leitura exploratória: 12 respondentes, abaixo de 20.", tarefa)
        self.assertIn("a Matriz não tem grupo para ler neste recorte; escreva apenas: Não avaliável.", tarefa)
        self.assertIn("o Termômetro não é avaliável neste recorte; escreva apenas: Não avaliável.", tarefa)
        self.assertNotIn("Anteriores → novos", tarefa)

    def test_without_pattern_or_signal_there_is_no_hypothesis_to_ask_for(self):
        agregacao = _agregar([_audio(1, _tema(MAQUINA)), _audio(2, _tema(ATENDENTE, "positiva"))])

        tarefa = rel.mensagem_para_ia(agregacao, {}, "cabeçalho").split("## Tarefa", 1)[1]

        self.assertIn("nenhum subtema está em padrão ou sinal. Escreva só a linha, sem ID", tarefa)
        self.assertIn("3. **Hipóteses**: sem achado com ID", tarefa)
        self.assertIn("4. **Recomendações**: sem achado com ID", tarefa)
        self.assertIn("Leitura exploratória: 2 respondentes", tarefa)

    def test_no_session_reaches_the_ai(self):
        audios = _projeto_pequeno()
        agregacao = _agregar(audios)
        analises = {
            1: "## 1. Sumário Executivo\nRelato de {} sobre a máquina.\n\n## 2. Perfil\nx".format(audios[0].session_id),
        }

        mensagem = rel.mensagem_para_ia(
            agregacao, {"nome": "P"}, "cabeçalho", sumarios=rel.sumarios_individuais(agregacao, analises)
        )

        for audio in audios:
            self.assertNotIn(audio.session_id, mensagem)
        self.assertNotIn("+5521", mensagem)
        self.assertIn("### Áudio 01\nRelato de Áudio 01 sobre a máquina.", mensagem)


def _sinc_de_entrevista(sessao):
    """Entrevistador e entrevistado com 10 falas cada; a primeira de cada um diverge."""
    linhas = []
    for locutor, base in (("Entrevistador", 0), ("Entrevistado", 10)):
        for s in range(10):
            segmento = base + s
            linhas.append({
                "session_id": sessao, "SpeakerName": locutor, "Text": "{} {}".format(locutor, s),
                "start_s": segmento * 4.0, "end_s": segmento * 4.0 + 3.5, "segmento_idx": segmento,
                "dim_valence": -1.0 if s == 0 else 0.01 * s, "sentimento_texto": 0.9 if s == 0 else 0.0,
            })
    return pd.DataFrame(linhas)


class DivergenciasTests(unittest.TestCase):
    def test_the_ai_gets_only_the_interviewee_and_the_appendix_gets_everybody(self):
        sessao = _sessao(1, 1)
        audio = _audio(1, _tema(MAQUINA), linhas=_linhas(2, "Entrevistado"), respondente="Entrevistado",
                       sessao=sessao)
        sinc = _sinc_de_entrevista(sessao)
        agregacao = _agregar([audio], tipo=ENTREVISTA_QUALITATIVA, sinc_projeto=sinc)

        para_ia = rel.divergencias_markdown(agregacao, sinc, so_respondente=True)
        apendice = rel.divergencias_markdown(agregacao, sinc)

        self.assertIn("| Áudio 01 | 00:00:40 | Entrevistado |", para_ia)
        self.assertNotIn("Entrevistador", para_ia)
        self.assertIn("| Áudio 01 | 00:00:00 | Entrevistador |", apendice)
        self.assertNotIn(sessao, para_ia + apendice)
        self.assertIn("+0,90", para_ia)

    def test_opinion_keeps_every_line(self):
        sessao = _sessao(1, 1)
        sinc = _sinc_de_entrevista(sessao)
        agregacao = _agregar([_audio(1, _tema(MAQUINA), sessao=sessao)], sinc_projeto=sinc)

        self.assertIn("Entrevistador", rel.divergencias_markdown(agregacao, sinc, so_respondente=True))


class SumariosTests(unittest.TestCase):
    def test_only_counted_audios_by_alias_and_old_analyses_are_counted_apart(self):
        audios = [_audio(1, _tema(MAQUINA)), _audio(2, _tema(MAQUINA)), _audio(3, classificada=False)]
        agregacao = _agregar(audios)
        analises = {
            1: "## 1. Sumário Executivo\nMáquina parada.\n## 2. Perfil\nx",
            2: "## 1. Perfil do Entrevistado\nPrompt antigo.",
            3: "## 1. Sumário Executivo\nSem classificação válida.",
        }

        sumarios = rel.sumarios_individuais(agregacao, analises)

        self.assertEqual(sumarios.texto, "### Áudio 01\nMáquina parada.")
        self.assertEqual((sumarios.com, sumarios.sem, sumarios.fora_do_limite), (1, 1, 0))
        self.assertIn(
            "1 de 2 análises individuais não têm Sumário Executivo",
            rel.apendice_markdown(agregacao, None, None, sumarios=(sumarios.com, sumarios.sem)),
        )

    def test_sessions_with_a_phone_become_the_alias(self):
        audios = [_audio(1, _tema(MAQUINA)), _audio(2, _tema(MAQUINA))]
        agregacao = _agregar(audios)
        texto = "## Sumário Executivo\nIgual a {} e a wa_+5511988887777_9.".format(audios[1].session_id)

        sumarios = rel.sumarios_individuais(agregacao, {1: texto})

        self.assertEqual(sumarios.texto, "### Áudio 01\nIgual a Áudio 02 e a [sessão].")

    def test_over_the_limit_the_audios_cited_in_the_evidence_go_first(self):
        audios = [_audio(1), _audio(2), _audio(3, _tema(MAQUINA)), _audio(4, _tema(MAQUINA)), _audio(5, _tema(MAQUINA))]
        agregacao = _agregar(audios)
        analises = {i: "## Sumário Executivo\n" + "x" * 50 for i in range(1, 6)}

        with patch.object(rel, "_MAX_SUMARIOS_NA_MENSAGEM", 200):
            sumarios = rel.sumarios_individuais(agregacao, analises)

        self.assertEqual(
            [linha for linha in sumarios.texto.splitlines() if linha.startswith("###")],
            ["### Áudio 03", "### Áudio 04", "### Áudio 05"],
        )
        self.assertEqual(sumarios.fora_do_limite, 2)
        self.assertIn(
            "2 Sumários Executivos ficaram fora do que a IA recebeu pelo limite de tamanho",
            rel.apendice_markdown(agregacao, None, None, sumarios_fora=sumarios.fora_do_limite),
        )


# ---------------------------------------------------------------------------
# Separação e montagem
# ---------------------------------------------------------------------------

class SepararSecoesTests(unittest.TestCase):
    def test_titles_with_numbers_bold_or_any_level_and_the_preamble_is_dropped(self):
        texto = (
            "Segue a análise.\n\n## 1. Resumo executivo\nResumo.\n\n**Achados:**\n- **A1** · x\n\n"
            "#### 3) HIPÓTESES\n- **H1** — y\n\n### Recomendacoes\n- **R1** z\n\n"
            "### Leitura da Matriz de Destaques\n**Vínculo forte** — p\n\n### Termômetro — leitura\nT.\n"
        )

        secoes = rel.separar_secoes(texto).secoes

        self.assertEqual(list(secoes), [rel.RESUMO, rel.ACHADOS, rel.HIPOTESES, rel.RECOMENDACOES,
                                        rel.LEITURA_MATRIZ, rel.LEITURA_TERMOMETRO])
        self.assertEqual(secoes[rel.RESUMO], "Resumo.")
        self.assertEqual(secoes[rel.ACHADOS], "- **A1** · x")
        self.assertEqual(secoes[rel.LEITURA_MATRIZ], "**Vínculo forte** — p")

    def test_forbidden_sections_and_tables_stay_out_and_subtitles_stay_in(self):
        texto = (
            "### Resumo Executivo\nResumo.\n\n### Taxonomia de Temas\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
            "### Achados\n- **A1** · x\n\n### Menções isoladas\nLotação (1/12).\n| c | d |\n\n"
            "### Limitações\nAmostra pequena.\n---\n"
        )

        separado = rel.separar_secoes(texto)

        self.assertEqual(separado.secoes[rel.ACHADOS], "- **A1** · x\n\n### Menções isoladas\nLotação (1/12).")
        self.assertEqual(separado.fora_da_estrutura, ["Taxonomia de Temas", "Limitações"])
        self.assertEqual(separado.com_tabela, [rel.ACHADOS])
        self.assertNotIn("Amostra pequena", "".join(separado.secoes.values()))

    def test_a_forbidden_subtitle_below_the_section_level_does_not_swallow_the_rest(self):
        texto = "### Achados\n- **A1** · x\n#### Limitações do A1\nPoucos casos.\n- **A2** · y\n\n### Hipóteses\n- **H1** — z\n"

        separado = rel.separar_secoes(texto)

        self.assertEqual(separado.fora_da_estrutura, [])
        self.assertTrue(separado.secoes[rel.ACHADOS].endswith("- **A2** · y"))


class MontagemTests(unittest.TestCase):
    def setUp(self):
        self.agregacao = _exemplo()
        self.cabecalho = rel.cabecalho_markdown(
            self.agregacao, "Rede de lavanderias self-service — Voz do Cliente por QR Code", Recorte(), 64, "validada"
        )

    def _montar(self, texto=prompts.EXEMPLO_ANALISE_GERAL, agregacao=None):
        return rel.montar_relatorio(
            agregacao or self.agregacao, self.cabecalho, rel.separar_secoes(texto), "#### Limitações\n\n- Uma."
        )

    def test_the_spec_example_comes_out_in_the_fixed_order(self):
        relatorio = self._montar()

        self.assertTrue(relatorio.startswith(
            "**Rede de lavanderias self-service — Voz do Cliente por QR Code** · 64 respondentes (52 da análise de "
            "30/09 e 12 novos) · taxonomia validada com a cliente\n\n### Resumo Executivo\n\nMáquina fora de serviço"
        ))
        titulos = [linha for linha in relatorio.splitlines() if linha.startswith("### ")]
        self.assertEqual(titulos, [
            "### Resumo Executivo", "### 1. Taxonomia de Temas", "### 2. Achados", "### 3. Hipóteses",
            "### 4. Recomendações", "### 5. Matriz de Destaques", "### 6. Termômetro de Experiência",
            "### Apêndice técnico",
        ])
        # As linhas de tabela que a spec mostra no exemplo saem do código, iguais.
        for linha in (
            "| Equipamentos > Máquina fora de serviço | 19/64 (30%) | Padrão | 17 neg · 2 mistas | abaixo do "
            "habitual, ativação alta → reforça | 13/52 → 6/12 |",
            "| Pagamento > Preço do ciclo | 4/64 (6%) | Sinal | 3 neg · 1 pos | não avaliável (4 trechos) | 4/52 → 0/12 |",
            "| Equipamentos > Secagem incompleta | 2/64 (3%) | Menção isolada | 2 neg | — | 2/52 → 0/12 |",
            "Temas emergentes (fora da taxonomia): Estacionamento (Ambiente), 2/64, 2 neg · Cheiro do sabão "
            "(Produtos), 1/64, 1 pos.",
            "| Áudio 03 | +0,82 | +0,36 | +0,59 | Atendente da unidade; Limpeza da loja |",
            "Temas mais citados no grupo: Atendente da unidade (4/5), Limpeza da loja (3/5).",
            "| Áudio 08 | -0,64 | -0,48 | Falha no app de pagamento |",
        ):
            with self.subTest(linha=linha[:50]):
                self.assertIn(linha, relatorio.splitlines())
        self.assertTrue(relatorio.rstrip().endswith("#### Limitações\n\n- Uma."))

    def test_each_matrix_reading_goes_under_its_table_without_the_group_name(self):
        relatorio = self._montar()

        matriz = relatorio.split("### 5. Matriz de Destaques", 1)[1].split("### 6.", 1)[0]
        vinculo, risco = matriz.split("**Risco de abandono silencioso**", 1)
        self.assertIn(
            "Temas mais citados no grupo: Atendente da unidade (4/5), Limpeza da loja (3/5).\n\n"
            "*Perfil:* clientes que citam o atendente pelo nome",
            vinculo,
        )
        self.assertIn("Temas mais citados no grupo: Falha no app de pagamento (3/4).\n\n*Perfil:* reclamação de "
                      "pagamento dita sem energia", risco)
        self.assertNotIn("**Vínculo forte** — *Perfil:*", relatorio)
        termometro = relatorio.split("### 6. Termômetro de Experiência\n\n", 1)[1].split("\n\n### ", 1)[0]
        self.assertTrue(termometro.startswith("**-1** (de -5 a +5)"))
        self.assertTrue(termometro.endswith("a limpeza (A4) é elogiada sem energia e pesa pouco."))

    def test_a_reading_without_group_names_goes_after_the_tables(self):
        texto = _trocar(
            "**Vínculo forte** — *Perfil:* clientes",
            "*Perfil:* clientes",
        ).replace("**Risco de abandono silencioso** — *Perfil:*", "*Perfil:*")

        matriz = self._montar(texto).split("### 5. Matriz de Destaques", 1)[1].split("### 6.", 1)[0]

        self.assertLess(matriz.index("Falha no app de pagamento (3/4)."), matriz.index("*Perfil:* clientes"))

    def test_what_is_not_assessable_keeps_only_the_code_text(self):
        agregacao = _exemplo(matriz=False, termometro=False)

        relatorio = self._montar(agregacao=agregacao)

        self.assertIn(
            "### 5. Matriz de Destaques\n\nMatriz não avaliável: 8 áudios com índice combinado no projeto, menos "
            "que os 10 necessários.\n\n### 6. Termômetro de Experiência\n\n**Não avaliável:** nenhum respondente",
            relatorio,
        )
        self.assertNotIn("*Perfil:*", relatorio)
        self.assertNotIn("O saldo fica negativo", relatorio)

    def test_a_missing_section_gets_a_notice(self):
        texto = prompts.EXEMPLO_ANALISE_GERAL.split("### Hipóteses", 1)[0]

        relatorio = self._montar(texto)

        self.assertIn("### 3. Hipóteses\n\n_A IA não escreveu esta seção._", relatorio)
        self.assertIn("### 4. Recomendações\n\n_A IA não escreveu esta seção._", relatorio)


# ---------------------------------------------------------------------------
# Verificação das referências
# ---------------------------------------------------------------------------

class VerificacaoTests(unittest.TestCase):
    def test_the_spec_example_passes(self):
        self.assertEqual(_verificar(prompts.EXEMPLO_ANALISE_GERAL), [])

    def test_every_id_cited_must_exist(self):
        texto = _trocar("(R1); em seguida", "(R7); em seguida").replace("o retrato de H1.", "o retrato de H4.")

        self.assertEqual(_verificar(texto), [
            "Resumo Executivo cita R7, que não existe.",
            "Matriz de Destaques — leitura cita H4, que não existe.",
        ])

    def test_hypothesis_needs_a_finding_and_recommendation_needs_responde_a(self):
        texto = (
            _trocar("*Apoia-se em:* A3.", "*Apoia-se em:* H2.")
            .replace("*Apoia-se em:* A1. ", "")
            .replace("*Responde a:* A5. ", "")
            .replace("*Responde a:* A3, H1.", "*Responde a:* a equipe.")
        )

        self.assertEqual(_verificar(texto), [
            "H1 não se apoia em nenhum achado.",
            'H2 está sem "Apoia-se em".',
            "R2 não responde a nenhum achado nem hipótese.",
            'R4 está sem "Responde a".',
        ])

    def test_recommendation_parts(self):
        texto = _trocar(
            "- **R4** [Higiene] (Driver: Retenção · Área: Operações) — Mostrar",
            "- **R4** — Mostrar",
        ).replace(" *Como medir:* menções a espera na próxima análise e uso fora do pico.", "")

        self.assertEqual(_verificar(texto), [
            'R4 está sem a classificação [Higiene] ou [Encantamento], "Driver", "Área" e "Como medir".',
        ])

    def test_audios_must_exist_and_be_in_the_cut(self):
        texto = _trocar("(Áudio 12, 00:14; Áudio 31, 00:09)", "(Áudio 12, 00:14; Áudios 66 e 71)")

        self.assertEqual(_verificar(texto, _exemplo(audios_no_projeto=70)), [
            "Achados cita Áudio 66, que está fora do recorte.",
            "Achados cita Áudio 71, que não existe.",
        ])

    def test_findings_follow_their_row_of_the_table(self):
        texto = (
            _trocar("· padrão, 13/64 —", "· sinal, 13/64 —")
            .replace("**A4** · Ambiente > Limpeza da loja · padrão, 9/64", "**A4** · Ambiente > Limpeza · padrão, 9/64")
            .replace("- Menções isoladas, sem hipótese própria:",
                     "- **A8** · Equipamentos > Secagem incompleta · menção isolada, 2/64 — pouco.\n- Menções:")
            .replace("**A7** · Atendimento > Suporte pelo WhatsApp · sinal, 3/64",
                     "**A7** Suporte pelo WhatsApp, 3 de 64")
        )

        self.assertEqual(_verificar(texto), [
            'A2 diz "sinal", mas Atendente da unidade está em padrão na tabela.',
            'A4 cita "Ambiente > Limpeza", que não está na tabela de temas.',
            'A7 não segue o formato "Categoria > Subtema · faixa, n/N".',
            "A8 é de Secagem incompleta, menção isolada (2/64): menção isolada não leva ID.",
            "Falta o achado de Ambiente > Limpeza da loja (padrão, 9/64).",
            "Falta o achado de Atendimento > Suporte pelo WhatsApp (sinal, 3/64).",
        ])

    def test_the_finding_line_tolerates_the_usual_variations(self):
        resto = "— 17 dos 19 falam da máquina parada. Voz: abaixo do habitual, com ativação alta — reforça o texto."
        a1 = next(linha for linha in prompts.EXEMPLO_ANALISE_GERAL.splitlines() if linha.startswith("- **A1**"))
        for variante in (
            "- **A1** · Máquina fora de serviço · padrão, 19/64 " + resto,
            "- A1 · Equipamentos>Máquina fora de serviço · Padrão · 19/64 " + resto,
            "1. **A1** — Equipamentos > Máquina fora de serviço — padrão (19/64): " + resto,
            "- **A1 · Equipamentos > Máquina fora de serviço · padrão, 19/64** " + resto,
        ):
            with self.subTest(variante=variante[:40]):
                self.assertEqual(_verificar(_trocar(a1, variante)), [])

    def test_the_same_subtheme_in_two_findings(self):
        linhas = prompts.EXEMPLO_ANALISE_GERAL.splitlines()
        a4 = next(linha for linha in linhas if linha.startswith("- **A4**"))
        a5 = next(linha for linha in linhas if linha.startswith("- **A5**"))

        self.assertEqual(_verificar(_trocar(a5, a4.replace("**A4**", "**A5**"))), [
            "Falta o achado de Ambiente > Espera por máquina livre (sinal, 6/64).",
            "Ambiente > Limpeza da loja aparece em A4 e A5.",
        ])

    def test_the_same_id_twice(self):
        a5 = next(linha for linha in prompts.EXEMPLO_ANALISE_GERAL.splitlines() if linha.startswith("- **A5**"))

        self.assertEqual(_verificar(_trocar(a5, a5 + "\n" + a5)), [
            "A5 aparece 2 vezes.",
            "Ambiente > Espera por máquina livre aparece em A5 e A5.",
        ])

    def test_voice_reading_of_a_finding_must_match_the_table(self):
        texto = (
            _trocar("Voz: no habitual, com ativação baixa — atenua. O tema",
                    "Voz: acima do habitual, com ativação alta — reforça. O tema")
            .replace("Leitura de voz não avaliável (4 trechos).", "Voz: abaixo do habitual — reforça.")
        )

        self.assertEqual(_verificar(texto), [
            'A3: a tabela traz a voz "no habitual, ativação baixa → atenua", e o achado diz "reforça", '
            '"acima do habitual" e "ativação alta".',
            'A6: a tabela traz a voz "não avaliável (4 trechos)", e o achado diz "reforça" e "abaixo do habitual".',
        ])

    def test_fractions_must_match_the_table(self):
        texto = (
            _trocar("· padrão, 19/64 —", "· padrão, 18/64 —")
            .replace("(A3, 11/64) é relatada", "(A3, 12/64) é relatada")
            .replace("Secagem incompleta (2/64)", "Secagem incompleta (3/64)")
            .replace("(hoje 19/64)", "(hoje 25/64)")
        )

        self.assertEqual(_verificar(texto), [
            "A1 traz 18/64, mas a tabela traz 19/64 para Máquina fora de serviço.",
            "Resumo Executivo cita A3 com 12/64, mas a tabela traz 11/64 para Falha no app de pagamento.",
            "Achados cita Secagem incompleta com 3/64, mas a tabela traz 2/64.",
            "Recomendações cita 25/64, que não aparece nas tabelas.",
        ])

    def test_fractions_of_the_delta_column_of_the_matrix_and_dates_are_accepted(self):
        texto = _trocar(
            "O saldo fica negativo",
            "Desde a análise de 30/09, o tema de pagamento foi de 6/52 para 5/12 (A3, 5/12), e o vínculo se "
            "concentra no Atendente da unidade (4/5). O saldo fica negativo",
        )

        self.assertEqual(_verificar(texto), [])

    def test_a_date_that_looks_like_a_fraction(self):
        agregacao = _exemplo()
        agregacao.delta = Delta(AnaliseAnterior(datetime(2026, 11, 10, 9, 0), frozenset(range(1, 53))), 52, 12, 0)
        texto = _trocar("O saldo fica negativo", "Desde a análise de 10/11, o saldo fica negativo")

        self.assertEqual(_verificar(texto, agregacao), [])

    def test_subthemes_cited_in_the_prose_must_be_in_the_table(self):
        texto = _trocar(
            "O saldo fica negativo",
            "Ambiente > Estacionamento, Equipamentos > Máquina fora de serviço e Ambiente > Barulho e pela "
            "noite. O saldo fica negativo",
        )

        self.assertEqual(_verificar(texto), [
            'Termômetro — leitura cita "Ambiente > Barulho", que não está na tabela de temas.',
        ])

    def test_missing_extra_and_tabled_sections(self):
        texto = (
            prompts.EXEMPLO_ANALISE_GERAL.split("### Termômetro — leitura", 1)[0]
            .replace("### Hipóteses", "### Limitações\nAmostra pequena.\n\n### Hipóteses")
            .replace("### Achados\n", "### Achados\n| Tema | n |\n|---|---|\n| Máquina | 19/64 |\n")
        )

        self.assertEqual(_verificar(texto), [
            'A resposta da IA não trouxe a seção "Termômetro — leitura".',
            'A IA escreveu a seção "Limitações", fora da estrutura: ela ficou fora do relatório.',
            'A IA escreveu uma tabela em "Achados": ela ficou fora do relatório, que usa as tabelas do código.',
        ])

    def test_a_named_group_without_reading(self):
        texto = _trocar("**Risco de abandono silencioso** — ", "")

        self.assertEqual(_verificar(texto), ['A leitura da Matriz não trouxe o grupo "Risco de abandono silencioso".'])

    def test_text_left_out_of_the_report_is_not_checked(self):
        texto = _trocar("o retrato de H1.", "o retrato de H9 (Áudio 99, 50/64).")

        self.assertEqual(_verificar(texto, _exemplo(matriz=False)), [])

    def test_a_session_with_a_phone_in_the_text(self):
        texto = _trocar("(Áudio 22, 00:03)", "(wa_+5521998548459_39, 00:03)")

        self.assertEqual(_verificar(texto), [
            'Achados cita a sessão de um áudio, que traz o telefone do respondente: use o alias "Áudio NN".',
        ])


# ---------------------------------------------------------------------------
# Etapas 4 e 5 com a IA simulada
# ---------------------------------------------------------------------------

class EscreverRelatorioTests(unittest.TestCase):
    def setUp(self):
        self.audios = _projeto_pequeno()
        self.agregacao = _agregar(self.audios)

    def _escrever(self, ia, **kwargs):
        return rel.escrever_relatorio(
            self.agregacao, {"nome": "Demonstração", "especialidade": "Lavanderia"}, Recorte(), 12, "provisoria",
            ia, **kwargs
        )

    def test_one_call_with_the_prompt_temperature_and_tokens(self):
        ia = IaFalsa(TEXTO_DO_PROJETO_PEQUENO, citacoes=[{"filename": "literatura.pdf", "quote": "x"}])

        relatorio = self._escrever(ia)

        self.assertEqual(len(ia.chamadas), 1)
        chamada = ia.chamadas[0]
        self.assertEqual(chamada["system_prompt"], prompts.sistema_analise_geral(PESQUISA_OPINIAO))
        self.assertEqual(chamada["user_prompt"], relatorio.mensagem)
        self.assertEqual((chamada["temperature"], chamada["max_tokens"]), (0.3, prompts.MAX_TOKENS_ANALISE_GERAL))
        self.assertIn("<tabela_temas>", chamada["user_prompt"])
        self.assertEqual(relatorio.citacoes, [{"filename": "literatura.pdf", "quote": "x"}])
        self.assertEqual(relatorio.busca, {"available": True, "searched": True})
        self.assertEqual(relatorio.texto_da_ia, TEXTO_DO_PROJETO_PEQUENO)

    def test_the_report_is_assembled_and_checked(self):
        relatorio = self._escrever(IaFalsa(TEXTO_DO_PROJETO_PEQUENO))

        self.assertEqual(relatorio.problemas, [])
        self.assertTrue(relatorio.texto.startswith(
            "**Demonstração** · 12 respondentes · taxonomia provisória, a validar com a cliente\n\n"
            "**Leitura exploratória:** 12 respondentes, abaixo de 20."
        ))
        self.assertIn("| Equipamentos > Máquina fora de serviço | 6/12 (50%) | Padrão | 6 neg |", relatorio.texto)
        self.assertIn("- **A1** · Equipamentos > Máquina fora de serviço · padrão, 6/12", relatorio.texto)
        self.assertIn("Subtemas da taxonomia sem menção no recorte: {}.".format(PRECO), relatorio.texto)
        self.assertIn("### Apêndice técnico\n\n#### Voz por tema", relatorio.texto)
        self.assertIn("#### Legenda dos áudios", relatorio.texto)
        self.assertNotIn("Não avaliável.\n", relatorio.texto)

    def test_the_appendix_follows_the_options_and_the_summaries(self):
        analises = {1: "## Sumário Executivo\nResumo.", 2: "## 1. Perfil\nPrompt antigo."}

        relatorio = self._escrever(IaFalsa(TEXTO_DO_PROJETO_PEQUENO), analises_individuais=analises,
                                   incluir_legenda=False)

        self.assertNotIn("#### Legenda dos áudios", relatorio.texto)
        for audio in self.audios:
            self.assertNotIn(audio.session_id, relatorio.texto)
        self.assertIn("1 de 2 análises individuais não têm Sumário Executivo", relatorio.texto)
        self.assertIn("### Áudio 01\nResumo.", relatorio.mensagem)

    def test_problems_are_reported_and_the_report_still_comes_out(self):
        texto = TEXTO_DO_PROJETO_PEQUENO.replace("(A2, 3/12)", "(A2, 4/12)")

        relatorio = self._escrever(IaFalsa(texto))

        self.assertEqual(
            relatorio.problemas,
            ["Resumo Executivo cita A2 com 4/12, mas a tabela traz 3/12 para Atendente da unidade."],
        )
        self.assertIn("(A2, 4/12)", relatorio.texto)

    def test_an_empty_answer_is_an_error(self):
        with self.assertRaises(RespostaInvalida):
            self._escrever(IaFalsa("   "))

    def test_without_respondents_the_ai_is_not_called(self):
        self.agregacao = _agregar([_audio(1, linhas=())])
        ia = IaFalsa(TEXTO_DO_PROJETO_PEQUENO)

        with self.assertRaises(ValueError):
            self._escrever(ia)
        self.assertEqual(ia.chamadas, [])


if __name__ == "__main__":
    unittest.main()
