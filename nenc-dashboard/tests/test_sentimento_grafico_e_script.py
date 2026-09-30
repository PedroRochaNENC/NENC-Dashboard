"""Peças pequenas do sentimento do texto: o JSON da Timeline e o gráfico do projeto.

A Timeline cola o Sincronizado dentro de um <script>. Com a fala e a
justificativa do sentimento indo junto, um `</script>` dito na entrevista
fecharia o bloco e o resto viraria HTML da página.
"""

import json
import math
import unittest

import pandas as pd

from utils.prosodia_charts import (
    create_project_acoustic_comparison,
    create_project_text_sentiment_distribution,
)
from utils.ui import json_para_script


class JsonParaScriptTests(unittest.TestCase):

    def test_fala_nao_fecha_o_script(self):
        texto = json_para_script([{"Text": "ok </script><img src=x onerror=alert(1)> & fim"}])

        self.assertNotIn("</", texto)
        self.assertNotIn("<", texto)
        self.assertEqual(json.loads(texto)[0]["Text"], "ok </script><img src=x onerror=alert(1)> & fim")

    def test_nan_do_pandas_vira_null(self):
        linhas = pd.DataFrame({"a": [1.0, math.nan]}).to_dict(orient="records")

        self.assertEqual(json.loads(json_para_script(linhas)), [{"a": 1.0}, {"a": None}])

    def test_separadores_de_linha_unicode_sao_escapados(self):
        texto = json_para_script("a b c")

        self.assertNotIn(" ", texto)
        self.assertEqual(json.loads(texto), "a b c")


def transcricao():
    return pd.DataFrame({
        "session_id": ["s1", "s1", "s2"],
        "start_s": [0.0, 3.0, 0.0],
        "end_s": [3.0, 4.0, 2.0],
        "sentimento_texto": [0.8, -0.5, 0.0],
        "sentimento_rotulo": ["positivo", "negativo", "neutro"],
    })


class GraficoSentimentoTests(unittest.TestCase):

    def test_fatias_do_tempo_de_fala_por_audio(self):
        fig = create_project_text_sentiment_distribution(transcricao())

        barras = {trace.name: list(trace.y) for trace in fig.data}
        self.assertEqual(set(barras), {"Positivo", "Neutro", "Negativo"})
        self.assertEqual(barras["Positivo"], [75.0, 0.0])
        self.assertEqual(barras["Negativo"], [25.0, 0.0])
        self.assertEqual(barras["Neutro"], [0.0, 100.0])

    def test_sem_sentimento_mostra_aviso(self):
        fig = create_project_text_sentiment_distribution(pd.DataFrame({"session_id": ["s1"]}))

        self.assertEqual(len(fig.data), 0)
        self.assertIn("Sem sentimento", fig.layout.annotations[0].text)

    def test_comparacao_acustica_ganha_a_barra_do_texto(self):
        sinc = pd.DataFrame({"session_id": ["s1", "s2"], "dim_valence": [0.1, -0.2]})

        sem = create_project_acoustic_comparison(sinc)
        com = create_project_acoustic_comparison(sinc, tr_df=transcricao())

        self.assertEqual([t.name for t in sem.data], ["Valência (Média)"])
        self.assertEqual([t.name for t in com.data][-1], "Sentimento do texto (média)")
        self.assertAlmostEqual(com.data[-1].y[0], 0.475)


if __name__ == "__main__":
    unittest.main()
