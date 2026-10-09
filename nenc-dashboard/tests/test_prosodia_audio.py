"""Página do áudio (7a): funções puras de prosodia_audio."""

import unittest

import pandas as pd

from utils import prosodia_audio as pa


def _sinc():
    linhas = []
    for i in range(6):
        linhas.append({
            "session_id": "wa_1_10", "segmento_idx": i // 2, "start_s": i * 2.0, "end_s": i * 2.0 + 1.8,
            "emocao_neutral": 0.5, "emocao_happy": 0.3, "emocao_sad": 0.1, "emocao_angry": 0.1,
            "dim_arousal": 0.1 * i,
        })
    return pd.DataFrame(linhas)


class CabecalhoTest(unittest.TestCase):
    def test_navegacao_pela_lista_da_tabela(self):
        self.assertEqual(pa.navegacao([7, 5, 9], 5), {"posicao": 2, "total": 3, "anterior": 7, "proximo": 9})
        self.assertEqual(pa.navegacao([7, 5, 9], 7)["anterior"], None)
        self.assertEqual(pa.navegacao([7, 5, 9], 1)["posicao"], None)

    def test_numeros_e_tempos(self):
        self.assertEqual(pa.numero(0.344), "+0,34")
        self.assertEqual(pa.numero(-1.62, 1), "−1,6")
        self.assertEqual(pa.numero(None), "")
        self.assertEqual(pa.tempo_curto(158), "2:38")
        self.assertEqual(pa.tempo_curto(3725), "1:02:05")


class AnaliseEQualidadeTest(unittest.TestCase):
    def test_resumo_pula_titulo_e_tabela(self):
        texto = "## Resumo\n\n| a | b |\n|---|---|\n\nCliente de primeira visita, atraída pela promoção. " \
                "Elogia o atendimento. Reage à espera no caixa. Quarta frase fora."
        self.assertEqual(
            pa.resumo_curto(texto),
            "Cliente de primeira visita, atraída pela promoção. Elogia o atendimento. Reage à espera no caixa.",
        )

    def test_cobertura_usa_a_ia_e_cai_nas_palavras_chave(self):
        cob = pa.cobertura([
            {"question": "Q1", "covered_ai": True},
            {"question": "Q2", "covered_ai": False, "covered_keywords": True},
            {"question": "Q3", "covered_ai": None, "covered_keywords": True},
        ])
        self.assertEqual((cob["cobertas"], cob["total"], cob["faltou"]), (2, 3, ["Q2"]))


class MomentosTest(unittest.TestCase):
    def test_divergencias_e_ativacao_numa_lista_por_tempo(self):
        div = pd.DataFrame([{"start_s": 12.0, "Timestamp": "", "SpeakerName": "R", "Text": "Ótimo.",
                             "sentimento_texto": 0.8, "z_valencia": -1.6, "tipo": "texto positivo × voz abaixo"}])
        ativ = [{"seconds": 4.0, "Timestamp": "00:00:04", "Text": "Fila demorada", "dim_arousal": 0.71,
                 "topic": "espera"}]
        indice = pd.DataFrame([{"start_s": 0.0, "indice": -0.45}, {"start_s": 10.0, "indice": 0.3}])
        lista = pa.momentos(div, ativ, indice)
        self.assertEqual([m["tipo"] for m in lista], ["ativacao", "divergencia"])
        self.assertEqual(lista[0]["numeros"], "ativação 0,71 · índice −0,45")
        self.assertEqual(lista[1]["numeros"], "texto +0,80 · voz z −1,6")
        self.assertEqual(lista[1]["tempo"], "0:12")


class DadosDoWidgetTest(unittest.TestCase):
    def test_emocao_em_fatias_que_somam_um(self):
        janelas = pa.janelas_emocao(_sinc())
        self.assertEqual(len(janelas), 6)
        self.assertAlmostEqual(sum(janelas[0][2:]), 1.0, places=2)

    def test_faixa_de_divergencia_cobre_o_segmento_inteiro(self):
        div = pd.DataFrame([{"segmento_idx": 1, "start_s": 4.0}])
        self.assertEqual(pa.faixas_divergencia(_sinc(), div), [[4.0, 7.8]])

    def test_indice_em_degraus_ate_o_fim_do_segmento(self):
        trechos = pd.DataFrame([{"segmento_idx": 0, "start_s": 0.0, "indice": 0.2}])
        self.assertEqual(pa.serie_indice(trechos, _sinc()), [[0.0, 3.8, 0.2]])

    def test_transcricao_marca_divergencia_e_ativacao(self):
        tr = pd.DataFrame([
            {"seconds": 0.0, "Text": "Oi", "SpeakerName": "R", "sentimento_texto": 0.35},
            {"seconds": 12.0, "end_s": 20.0, "Text": "Ótimo", "SpeakerName": "R", "sentimento_texto": -0.6},
        ])
        itens = pa.trechos_transcricao(tr, pd.DataFrame({"start_s": [12.0]}), [{"seconds": 3.0}])
        self.assertEqual([(i["divergencia"], i["ativacao"]) for i in itens], [(False, True), (True, False)])
        self.assertEqual(itens[0]["e"], 12.0)
        self.assertEqual(itens[1]["nota_txt"], "−0,60")

    def test_html_nao_deixa_fala_fechar_o_script(self):
        html = pa.faixas_html({"canal": "c", "duracao": 10, "foco": None, "faixas_div": [], "indice": [],
                               "emocao": [], "ativacao": [], "mais": [],
                               "trechos": [{"texto": "</script><b>x</b>"}]})
        self.assertNotIn("</script><b>", html)


if __name__ == "__main__":
    unittest.main()
