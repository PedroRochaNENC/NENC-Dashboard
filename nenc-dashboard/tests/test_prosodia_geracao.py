"""Geração da Análise Geral v2: as etapas da página, de ponta a ponta.

Banco descartável e IA simulada: a taxonomia, a classificação e o texto do
relatório vêm de funções falsas, e o resto é o código de verdade, até a
análise gravada com o snapshot.
"""

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from utils import ai_provider, auth, prosodia_db
from utils import prosodia_geracao as geracao
from utils import prosodia_prompts as prompts
from utils import prosodia_relatorio as rel
from utils.kb_attributes import build_kb_filter
from utils.prosodia_project_types import PESQUISA_OPINIAO
from utils.prosodia_temas import TAXONOMIA_PROVISORIA, TAXONOMIA_VALIDADA

TAXONOMIA = (
    "Atendimento > Demora: tempo de espera para ser atendido\n"
    "Preço > Caro: preço acima do esperado"
)

TEXTO_DA_IA = """\
### Resumo Executivo
A espera no atendimento aparece em todos os recados.

### Achados
- **A1** · Atendimento > Demora · sinal, 3/3 — Os respondentes reclamam da espera.

### Hipóteses
- **H1** · Apoia-se em A1. A fila cresce no horário de pico. Como validar: medir a espera por horário.

### Recomendações
- **R1** · Responde a A1 e H1. Reforçar a equipe no pico. Como medir: tempo médio de espera.
"""


def _classificacao(subtema="Atendimento > Demora"):
    return json.dumps(
        {
            "locutor_respondente": "Respondente",
            "temas": [{"subtema": subtema, "polaridade": "negativa", "linhas": [1], "evidencia": "espera"}],
            "emergentes": [],
        },
        ensure_ascii=False,
    )


class IaFalsa:
    """O responder da taxonomia e da classificação."""

    def __init__(self, classificacao=None, proposta=TAXONOMIA, falhar_em=()):
        self.classificacao = classificacao or _classificacao()
        self.proposta = proposta
        self.falhar_em = tuple(falhar_em)
        self.propostas = 0
        self.classificacoes = []

    def __call__(self, sistema, usuario, max_tokens):
        if "<transcricoes>" in usuario:
            self.propostas += 1
            return self.proposta
        self.classificacoes.append(usuario)
        if any(marca in usuario for marca in self.falhar_em):
            return "não é JSON"
        return self.classificacao


class RelatorioFalso:
    """O gerar do relatório: guarda a chamada e devolve o texto da IA."""

    def __init__(self, texto=TEXTO_DA_IA):
        self.texto = texto
        self.chamadas = []

    def __call__(self, **kwargs):
        self.chamadas.append(kwargs)
        return {"text": self.texto, "citations": [{"filename": "literatura.pdf"}], "search": {"searched": True}}


class _BancoDescartavel(unittest.TestCase):
    def setUp(self):
        # Banco descartavel: sem NENC_DB_PATH o teste escreveria no banco local.
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "nenc-insights.db"
        self.environment = patch.dict(os.environ, {"NENC_DB_PATH": str(self.database_path)})
        self.environment.start()
        organization = auth.create_organization(
            "Organization One", database_path=self.database_path, _bootstrap=True
        )
        admin = auth.create_user(
            name="Platform Admin",
            email="platform@example.com",
            phone="5511999999999",
            organization_id=organization.id,
            password="platform-admin-password",
            module_keys=auth.MODULE_KEYS,
            is_organization_admin=True,
            is_platform_admin=True,
            database_path=self.database_path,
            _bootstrap=True,
        )
        prosodia_db.init_db()
        for patcher in (
            patch("utils.prosodia_db._require_write", return_value=admin),
            patch("utils.prosodia_db._active_organization_id", return_value=organization.id),
            patch("utils.prosodia_db._claim_external_project_resources"),
            patch("utils.prosodia_db._audit"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def _projeto(self, taxonomia=TAXONOMIA, status=TAXONOMIA_VALIDADA, audios=3):
        self.project_id = prosodia_db.create_project(
            "Recados", especialidade="Academia", tipo_projeto=PESQUISA_OPINIAO,
            taxonomia=taxonomia, taxonomia_status=status if taxonomia else "",
        )
        for numero in range(1, audios + 1):
            self._audio(numero)

    def _audio(self, numero, prosodia_json=None):
        return prosodia_db.create_audio(
            self.project_id, "wa_55119000000{:02d}_{}".format(numero, numero), prosodia_json=prosodia_json
        )

    def _audios(self):
        return prosodia_db.get_audios_for_interviews(self.project_id)

    def _transcricao(self, audios):
        """Duas falas por áudio; a primeira traz o número do áudio, para a IA falsa achar."""
        linhas = []
        for audio in audios:
            numero = audio["session_id"].rsplit("_", 1)[-1]
            falas = ((1.0, "Recado {}: demorou demais para me atenderem.".format(numero)),
                     (5.0, "Fora isso, tudo certo."))
            for segundo, texto in falas:
                linhas.append({
                    "session_id": audio["session_id"],
                    "SpeakerName": "Respondente",
                    "Text": texto,
                    "seconds": segundo,
                    "end_s": segundo + 3,
                })
        return pd.DataFrame(linhas)

    def _gerar(self, ia=None, relatorio=None, ids=None, recorte=None, **kwargs):
        audios = self._audios()
        self.avisos = []
        return geracao.gerar_analise_geral(
            prosodia_db.get_project(self.project_id),
            audios,
            ids if ids is not None else [a["id"] for a in audios],
            recorte or rel.Recorte(),
            self._transcricao(audios),
            pd.DataFrame(),
            ia or IaFalsa(),
            "gpt-4.1-mini",
            relatorio or RelatorioFalso(),
            "gpt-5",
            avisar=lambda etapa, texto: self.avisos.append((etapa, texto)),
            **kwargs,
        )


class GeracaoTests(_BancoDescartavel):
    def test_classifies_aggregates_writes_and_saves_with_the_snapshot(self):
        self._projeto()
        ia, relatorio = IaFalsa(), RelatorioFalso()

        resultado = self._gerar(ia, relatorio)

        ids = sorted(a["id"] for a in self._audios())
        self.assertEqual(len(ia.classificacoes), 3)
        self.assertEqual(ia.propostas, 0)
        self.assertEqual(len(relatorio.chamadas), 1)
        # O tipo vai do cadastro do projeto até o prompt da Análise Geral.
        self.assertEqual(relatorio.chamadas[0]["system_prompt"], prompts.sistema_analise_geral(PESQUISA_OPINIAO))
        self.assertEqual(resultado.agregacao.n_respondentes, 3)
        self.assertEqual(sorted(resultado.classificacao.classificados), ids)
        self.assertFalse(resultado.taxonomia_da_ia)

        salvas = prosodia_db.get_audio_temas_for_project(self.project_id)
        self.assertEqual(sorted(salvas), ids)
        self.assertEqual({item["modelo"] for item in salvas.values()}, {"gpt-4.1-mini"})

        analise = prosodia_db.get_latest_project_analysis(self.project_id)
        self.assertEqual(analise["id"], resultado.analise_id)
        self.assertEqual(analise["model"], "gpt-5")
        self.assertEqual(analise["analysis_text"], resultado.relatorio.texto)
        self.assertEqual(analise["citations"], [{"filename": "literatura.pdf"}])
        self.assertEqual(analise["temas"]["audios"], ids)
        self.assertEqual(analise["temas"]["respondentes"], 3)
        self.assertIn("taxonomia validada com a cliente", resultado.relatorio.texto)

    def test_the_screen_hears_every_step_in_order(self):
        self._projeto()

        self._gerar()

        etapas = [etapa for etapa, _ in self.avisos]
        self.assertEqual(sorted(set(etapas)), [1, 2, 3, 4, 5])
        self.assertEqual(etapas, sorted(etapas))
        self.assertIn((2, "3 classificados agora; 0 reaproveitados do cache."), self.avisos)

    def test_a_second_generation_reuses_the_classification(self):
        self._projeto()
        self._gerar()
        ia = IaFalsa()

        resultado = self._gerar(ia)

        self.assertEqual(ia.classificacoes, [])
        self.assertEqual(len(resultado.classificacao.reaproveitados), 3)
        self.assertEqual(resultado.agregacao.n_respondentes, 3)

    def test_only_the_audios_of_the_cut_go_to_the_ai_and_to_the_snapshot(self):
        self._projeto(audios=4)
        audios = self._audios()
        recorte = [audios[0]["id"], audios[2]["id"]]
        ia = IaFalsa()

        resultado = self._gerar(ia, ids=recorte)

        self.assertEqual(len(ia.classificacoes), 2)
        self.assertEqual(resultado.agregacao.n_respondentes, 2)
        analise = prosodia_db.get_latest_project_analysis(self.project_id)
        self.assertEqual(analise["temas"]["audios"], sorted(recorte))

    def test_without_taxonomy_the_ai_proposes_one_and_the_project_keeps_it(self):
        self._projeto(taxonomia="")
        ia = IaFalsa()

        resultado = self._gerar(ia)

        self.assertEqual(ia.propostas, 1)
        self.assertTrue(resultado.taxonomia_da_ia)
        projeto = prosodia_db.get_project(self.project_id)
        self.assertEqual(projeto["taxonomia_status"], TAXONOMIA_PROVISORIA)
        self.assertIn("Atendimento > Demora", projeto["taxonomia"])
        self.assertIn("gerada pela IA", resultado.relatorio.texto)
        self.assertEqual(resultado.agregacao.n_respondentes, 3)

    def test_text_outside_the_format_is_not_replaced_by_a_proposal(self):
        self._projeto(taxonomia="atendimento, preço e limpeza", status=TAXONOMIA_PROVISORIA)
        ia, relatorio = IaFalsa(), RelatorioFalso()

        with self.assertRaises(geracao.TaxonomiaSemSubtema):
            self._gerar(ia, relatorio)

        self.assertEqual((ia.propostas, ia.classificacoes, relatorio.chamadas), (0, [], []))
        self.assertEqual(prosodia_db.get_project(self.project_id)["taxonomia"], "atendimento, preço e limpeza")

    def test_a_failed_audio_stays_out_and_comes_back_next_time(self):
        self._projeto()
        falha = next(a for a in self._audios() if a["session_id"].endswith("_2"))

        resultado = self._gerar(IaFalsa(falhar_em=("Recado 2:",)))

        self.assertEqual(list(resultado.classificacao.falhas), [falha["id"]])
        self.assertEqual(resultado.agregacao.sem_classificacao, (falha["id"],))
        self.assertEqual(resultado.agregacao.n_respondentes, 2)
        self.assertIn((2, "2 classificados agora; 0 reaproveitados do cache; 1 áudio com falha, fora da contagem."),
                      self.avisos)

        ia = IaFalsa()
        self._gerar(ia)
        self.assertEqual(len(ia.classificacoes), 1)

    def test_when_nothing_is_classified_the_report_is_not_written(self):
        self._projeto()
        relatorio = RelatorioFalso()

        with self.assertRaises(geracao.SemClassificacao) as erro:
            self._gerar(IaFalsa(classificacao="não é JSON"), relatorio)

        self.assertIn("3 falhas", str(erro.exception))
        self.assertEqual(relatorio.chamadas, [])
        self.assertIsNone(prosodia_db.get_latest_project_analysis(self.project_id))

    def test_the_next_analysis_compares_with_the_saved_snapshot(self):
        self._projeto()
        primeira = self._gerar()
        # A análise fica mais antiga que os áudios que chegam depois dela.
        with closing(sqlite3.connect(self.database_path)) as conn, conn:
            conn.execute("UPDATE project_analyses SET created_at = '2026-01-01 00:00:00'")
        for numero in (4, 5):
            self._audio(numero)

        segunda = self._gerar()

        delta = segunda.agregacao.delta
        self.assertIsNotNone(delta)
        self.assertEqual(delta.anterior.analise_id, primeira.analise_id)
        self.assertFalse(delta.anterior.reconstruida)
        self.assertEqual((delta.anteriores, delta.novos), (3, 2))

    def test_the_individual_summaries_and_the_options_reach_the_report(self):
        self._projeto()
        audio = self._audios()[0]
        prosodia_db.save_analysis(audio["id"], "gpt-5", "## Sumário Executivo\nEspera longa na recepção.")

        resultado = self._gerar(incluir_legenda=False)

        self.assertIn("Espera longa na recepção.", resultado.relatorio.mensagem)
        self.assertNotIn(audio["session_id"], resultado.relatorio.texto)

        resultado = self._gerar(incluir_legenda=True)

        self.assertIn(audio["session_id"], resultado.relatorio.texto)

    def test_the_same_file_sent_again_counts_once(self):
        self._projeto(audios=0)
        arquivo = json.dumps({"result": {"vad": [{"start": 0.0, "end": 2.5}]}}).encode("utf-8")
        primeiro = self._audio(1, arquivo)
        segundo = self._audio(2, json.dumps({"result": {"vad": [{"start": 0.0, "end": 3.0}]}}).encode("utf-8"))
        copia = self._audio(3, arquivo)

        resultado = self._gerar()

        self.assertEqual(resultado.agregacao.repetidos, {copia: primeiro})
        self.assertEqual(resultado.agregacao.n_respondentes, 2)
        self.assertIn(
            (3, "2 respondentes em 2 áudios contados do recorte (1 cópia de outro áudio, fora da contagem), "
                "leitura exploratória."),
            self.avisos,
        )
        self.assertIn("Em cada grupo conta só o primeiro: Áudio 01 = Áudio 03.", resultado.relatorio.texto)
        analise = prosodia_db.get_latest_project_analysis(self.project_id)
        self.assertEqual(analise["temas"]["audios"], sorted([primeiro, segundo, copia]))


class ChamadasTests(unittest.TestCase):
    def test_the_report_call_uses_the_base_without_analyses(self):
        with patch.object(ai_provider, "generate_analysis", return_value={"text": "ok"}) as chamada:
            gerar = geracao.gerador_do_relatorio("openai", "gpt-5", 7, "vs_1")
            resposta = gerar(system_prompt="sistema", user_prompt="mensagem", temperature=0.3, max_tokens=10)

        self.assertEqual(resposta, {"text": "ok"})
        chamada.assert_called_once_with(
            "openai", "gpt-5", vector_store_id="vs_1", kb_filter=build_kb_filter(7, incluir_analises=False),
            system_prompt="sistema", user_prompt="mensagem", temperature=0.3, max_tokens=10,
        )

    def test_classification_uses_the_cheap_model_on_openai_and_the_chosen_one_elsewhere(self):
        cliente = object()
        with patch.object(ai_provider, "get_provider_client", return_value=cliente), \
                patch.object(ai_provider, "chat_completion", return_value="{}") as chamada:
            responder, modelo = geracao.responder_da_classificacao("openai", "gpt-5")
            self.assertEqual(modelo, "gpt-4.1-mini")
            self.assertEqual(responder("sistema", "usuario", 100), "{}")
            chamada.assert_called_once_with(
                "openai", "gpt-4.1-mini", "sistema", [{"role": "user", "content": "usuario"}],
                temperature=geracao.TEMPERATURA_CLASSIFICACAO, max_tokens=100, client=cliente,
            )

            _, modelo = geracao.responder_da_classificacao("anthropic", "claude-sonnet-5-5")
            self.assertEqual(modelo, "claude-sonnet-5-5")

    def test_without_a_client_classification_is_refused_before_any_thread(self):
        with patch.object(ai_provider, "get_provider_client", return_value=None):
            with self.assertRaises(RuntimeError) as erro:
                geracao.responder_da_classificacao("groq", "llama")
        self.assertIn("GROQ_API_KEY", str(erro.exception))


class PaginaTests(unittest.TestCase):
    def test_marking_every_qr_code_or_origin_is_no_filter(self):
        periodo = (date(2026, 9, 1), date(2026, 9, 30))

        tudo = geracao.recorte_da_pagina(["B", "A"], ["A", "B"], periodo, ["upload", "whatsapp"], "")
        parte = geracao.recorte_da_pagina(["B"], ["A", "B"], periodo, ["whatsapp"], "QR code B")

        self.assertEqual((tudo.qr, tudo.origem), ((), ()))
        self.assertEqual(tudo.periodo, ("2026-09-01", "2026-09-30"))
        self.assertEqual((parte.qr, parte.origem, parte.descricao), (("B",), ("whatsapp",), "QR code B"))
        self.assertTrue(tudo.mesmo_filtro(rel.Recorte()))

    def test_lines_come_from_each_audio_transcript(self):
        audios = [{"id": 1, "session_id": "a"}, {"id": 2, "session_id": "b"}]
        transcricao = pd.DataFrame({
            "session_id": ["a", "a"],
            "SpeakerName": ["Respondente", "Respondente"],
            "Text": ["primeira", "segunda"],
        })

        linhas = geracao.linhas_por_audio(audios, transcricao)

        self.assertEqual([linha.texto for linha in linhas[1]], ["primeira", "segunda"])
        self.assertEqual(linhas[2], ())
        self.assertEqual(geracao.linhas_por_audio(audios, pd.DataFrame()), {1: (), 2: ()})

    def test_the_legend_uses_the_aliases_of_the_whole_project(self):
        audios = [
            {"id": 9, "session_id": "wa_5511900000003_9", "qr_code_name": "Recepção"},
            {"id": 4, "session_id": "wa_upload_1"},
            {"id": 7, "session_id": "wa_5511900000002_7"},
        ]

        self.assertEqual(
            [linha[:3] for linha in geracao.legenda(audios, [9, 7])],
            [("Áudio 02", "wa_5511900000002_7", "Sem QR"), ("Áudio 03", "wa_5511900000003_9", "Recepção")],
        )


if __name__ == "__main__":
    unittest.main()
