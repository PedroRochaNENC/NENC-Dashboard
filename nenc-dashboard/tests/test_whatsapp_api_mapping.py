"""Resultado da API virando os CSVs do Dashboard.

O Sincronizado juntava a linha i do VAD com o segmento i do Whisper — duas
segmentações independentes do mesmo áudio. Os "momentos de alta ativação"
citavam a fala de outro minuto, e isso chegava ao relatório e ao Power BI.
Agora o casamento é pelo tempo, e cada linha leva o índice do segmento e o
sentimento do texto que a API calculou para ele.
"""

import io
import unittest
from unittest import mock

import pandas as pd

from utils import whatsapp_api_client as api
from utils.prosodia_loader import load_prosodia_from_uploads
from utils.whatsapp_api_client import (
    alinhar_vad_aos_segmentos,
    map_api_result_to_all_formats,
    map_api_result_to_sincronizado_csv,
    transcricao_para_base_conhecimento,
)


class _BytesFile:
    def __init__(self, data: bytes, name: str):
        self._buf = io.BytesIO(data)
        self.name = name

    def read(self):
        return self._buf.read()

    def seek(self, pos):
        return self._buf.seek(pos)


def vad(*intervalos):
    return [{"start": s, "end": e} for s, e in intervalos]


def resultado(vad_rows=None, segmentos=None, sentimento=None, **extra):
    """Um result_json da API com o mínimo que o mapper lê."""
    vad_rows = vad_rows if vad_rows is not None else vad((0.0, 2.0), (2.5, 4.0), (10.0, 12.0))
    n = len(vad_rows)
    base = {
        "vad": vad_rows,
        "expressionLarge": [
            {"dimensional": {"arousal": 0.1 * i, "valence": -0.1 * i, "dominance": 0.0}}
            for i in range(n)
        ],
        "prosody": [{"f0": {"average": 200.0 + i}} for i in range(n)],
        "whisper": {
            "text": "...",
            "segments": segmentos if segmentos is not None else [
                {"start": 0.0, "end": 4.2, "text": "Bom dia, adorei o atendimento", "speaker": "A"},
                {"start": 4.3, "end": 4.6, "text": "  ", "speaker": "B"},
                {"start": 9.8, "end": 12.5, "text": "Mas a entrega atrasou demais", "speaker": "B"},
            ],
        },
    }
    if sentimento is not None:
        base["text_sentiment"] = sentimento
    base.update(extra)
    return base


def bloco_sentimento(status="done", n_segments=3, notas=None):
    notas = notas if notas is not None else {0: 0.8, 2: -0.7}
    return {
        "status": status,
        "n_segments": n_segments,
        "faixa_neutra": 0.2,
        "segments": [
            {"index": i, "score": notas.get(i),
             "label": None if notas.get(i) is None else ("positivo" if notas[i] > 0 else "negativo"),
             "justificativa": None if notas.get(i) is None else "motivo {}".format(i)}
            for i in range(n_segments)
        ],
    }


def sincronizado(result) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(map_api_result_to_sincronizado_csv(result, "wa_1_7")))


class AlinhamentoTests(unittest.TestCase):

    def test_vence_a_maior_sobreposicao(self):
        self.assertEqual(
            alinhar_vad_aos_segmentos([(0, 2), (2, 5)], [(0, 3), (3, 6)]),
            [0, 1],
        )

    def test_sem_sobreposicao_aceita_o_mais_proximo_ate_a_tolerancia(self):
        self.assertEqual(
            alinhar_vad_aos_segmentos([(6.2, 7.0), (7.0, 8.0)], [(0.0, 6.0)]),
            [0, None],
        )

    def test_segmento_longo_que_comecou_antes_ainda_e_encontrado(self):
        # O segmento 1 começa depois mas termina antes: a varredura para trás
        # não pode parar nele e perder o segmento 0, que cobre o VAD.
        self.assertEqual(
            alinhar_vad_aos_segmentos([(50, 51)], [(0, 100), (10, 12)]),
            [0],
        )

    def test_empate_fica_com_o_primeiro_e_intervalo_nulo_fica_sem_segmento(self):
        self.assertEqual(
            alinhar_vad_aos_segmentos([(1, 3), (None, 2)], [(0, 2), (2, 4)]),
            [0, None],
        )

    def test_segmentos_fora_de_ordem(self):
        self.assertEqual(
            alinhar_vad_aos_segmentos([(0, 1), (5, 6)], [(5, 6), (0, 1)]),
            [1, 0],
        )


class SincronizadoTests(unittest.TestCase):

    def test_casa_pelo_tempo_e_nao_pela_posicao(self):
        df = sincronizado(resultado())

        self.assertEqual(len(df), 3)  # uma linha por VAD, sem enchimento
        self.assertEqual(df["texto_transcricao"].tolist(), [
            "Bom dia, adorei o atendimento",
            "Bom dia, adorei o atendimento",
            "Mas a entrega atrasou demais",
        ])
        self.assertEqual(df["speakers"].tolist(), ["A", "A", "B"])
        # O índice é o de whisper.segments, com o segmento vazio contando.
        self.assertEqual(df["segmento_idx"].tolist(), [0, 0, 2])
        self.assertEqual(df["timestamp_inicio"].tolist()[2], "00:00:09.80")
        self.assertEqual(df["f0_media"].tolist(), [200.0, 201.0, 202.0])

    def test_linha_do_vad_sem_fala_por_perto_fica_sem_texto(self):
        df = sincronizado(resultado(vad_rows=vad((0.0, 2.0), (30.0, 31.0))))

        self.assertTrue(pd.isna(df.loc[1, "texto_transcricao"]))
        self.assertTrue(pd.isna(df.loc[1, "segmento_idx"]))

    def test_sentimento_done_vai_para_cada_linha(self):
        df = sincronizado(resultado(sentimento=bloco_sentimento()))

        self.assertEqual(df["sentimento_texto"].tolist(), [0.8, 0.8, -0.7])
        self.assertEqual(df["sentimento_rotulo"].tolist(), ["positivo", "positivo", "negativo"])
        self.assertEqual(df["sentimento_justificativa"].tolist()[2], "motivo 2")

    def test_partial_traz_o_que_tem_nota(self):
        df = sincronizado(resultado(sentimento=bloco_sentimento("partial", notas={2: -0.4})))

        self.assertTrue(df["sentimento_texto"].isna().tolist()[:2] == [True, True])
        self.assertEqual(df["sentimento_texto"].tolist()[2], -0.4)

    def test_failed_skipped_ou_de_outra_transcricao_nao_trazem_nota(self):
        for bloco in (bloco_sentimento("failed"), bloco_sentimento("skipped"),
                      bloco_sentimento(n_segments=5)):
            with self.subTest(status=bloco["status"], n=bloco["n_segments"]):
                df = sincronizado(resultado(sentimento=bloco))
                self.assertTrue(df["sentimento_texto"].isna().all())

    def test_resultado_antigo_sem_bloco_continua_funcionando(self):
        df = sincronizado(resultado())

        for coluna in ("sentimento_texto", "sentimento_rotulo", "sentimento_justificativa"):
            self.assertIn(coluna, df.columns)
            self.assertTrue(df[coluna].isna().all())

    def test_audio_cortado_pela_marca_da_api_ou_pela_duracao(self):
        self.assertFalse(sincronizado(resultado())["audio_cortado"].any())
        self.assertTrue(sincronizado(resultado(segmentacao={"cortado": True}))["audio_cortado"].all())
        longo = resultado(vad_rows=vad((0.0, 2.0), (950.0, 951.0)))
        self.assertTrue(sincronizado(longo)["audio_cortado"].all())

    def test_sem_vad_uma_linha_por_segmento(self):
        df = sincronizado(resultado(vad_rows=[], sentimento=bloco_sentimento()))

        self.assertEqual(df["segmento_idx"].tolist(), [0, 2])
        self.assertTrue(df["start_s"].isna().all())
        self.assertEqual(df["sentimento_texto"].tolist(), [0.8, -0.7])

    def test_inicio_nulo_nao_derruba_a_importacao(self):
        segmentos = [
            {"start": 0.0, "end": 2.0, "text": "primeiro", "speaker": "A"},
            {"start": None, "end": None, "text": "sem tempo", "speaker": "A"},
        ]

        df = sincronizado(resultado(vad_rows=vad((0.0, 1.0), (2.0, 2.3)), segmentos=segmentos))

        self.assertEqual(df["texto_transcricao"].tolist(), ["primeiro", "primeiro"])
        _, csv_bytes, _ = map_api_result_to_all_formats(resultado(segmentos=segmentos), "wa_1_7")
        tr = pd.read_csv(io.BytesIO(csv_bytes))
        self.assertEqual(tr["start_s"].tolist(), [0.0, 2.0])


class TranscricaoCsvTests(unittest.TestCase):

    def test_colunas_originais_na_frente_e_as_novas_depois(self):
        _, csv_bytes, _ = map_api_result_to_all_formats(resultado(sentimento=bloco_sentimento()), "wa_1_7")

        cabecalho = csv_bytes.decode("utf-8").splitlines()[0].split(",")
        self.assertEqual(cabecalho, [
            "SpeakerName", "Timestamp", "Text", "start_s", "end_s", "segmento_idx",
            "sentimento_texto", "sentimento_rotulo", "sentimento_justificativa",
        ])
        tr = pd.read_csv(io.BytesIO(csv_bytes))
        self.assertEqual(tr["segmento_idx"].tolist(), [0, 2])
        self.assertEqual(tr["Timestamp"].tolist(), ["00:00:00", "00:00:09"])
        self.assertEqual(tr["sentimento_texto"].tolist(), [0.8, -0.7])

    def test_whisper_so_com_texto_mantem_a_linha_unica(self):
        result = resultado(segmentos=[])
        result["whisper"]["text"] = "texto corrido sem segmentos"

        _, csv_bytes, sinc_bytes = map_api_result_to_all_formats(result, "wa_1_7")

        tr = pd.read_csv(io.BytesIO(csv_bytes))
        self.assertEqual(tr["Text"].tolist(), ["texto corrido sem segmentos"])
        sinc = pd.read_csv(io.BytesIO(sinc_bytes))
        self.assertEqual(sinc.loc[0, "texto_transcricao"], "texto corrido sem segmentos")
        self.assertTrue(sinc["texto_transcricao"].iloc[1:].isna().all())


class IdaEVoltaPeloLoaderTests(unittest.TestCase):

    def test_sincronizado_sozinho_nao_duplica_falas_nem_palavras(self):
        _, _, sinc_bytes = map_api_result_to_all_formats(resultado(), "wa_1_7")

        dados = load_prosodia_from_uploads(sincronizado_files=[_BytesFile(sinc_bytes, "Sincronizado-wa_1_7.csv")])

        tr = dados["transcricao"]
        self.assertEqual(tr["Text"].tolist(), ["Bom dia, adorei o atendimento", "Mas a entrega atrasou demais"])
        self.assertEqual(int(tr["word_count"].sum()), 10)  # 15 com a fala repetida
        self.assertEqual(len(dados["vad"]), 3)

    def test_transcricao_usa_o_inicio_exato_do_segmento(self):
        json_bytes, csv_bytes, sinc_bytes = map_api_result_to_all_formats(resultado(), "wa_1_7")

        dados = load_prosodia_from_uploads(
            json_files=[_BytesFile(json_bytes, "Prosodia-wa_1_7.json")],
            csv_files=[_BytesFile(csv_bytes, "Transcricao-wa_1_7.csv")],
            sincronizado_files=[_BytesFile(sinc_bytes, "Sincronizado-wa_1_7.csv")],
        )

        self.assertEqual(dados["transcricao"]["seconds"].tolist(), [0.0, 9.8])


class BaseDeConhecimentoTests(unittest.TestCase):

    def test_so_locutor_tempo_e_fala_saem_para_a_base(self):
        _, csv_bytes, _ = map_api_result_to_all_formats(resultado(sentimento=bloco_sentimento()), "wa_1_7")

        recortado = transcricao_para_base_conhecimento(csv_bytes).decode("utf-8")

        self.assertEqual(recortado.splitlines()[0], "SpeakerName,Timestamp,Text")
        self.assertNotIn("motivo", recortado)
        self.assertNotIn("0.8", recortado)
        self.assertIn("Mas a entrega atrasou demais", recortado)

    def test_csv_antigo_e_csv_ilegivel_seguem_como_vieram(self):
        antigo = b"SpeakerName,Timestamp,Text\nA,00:00:01,ola\n"
        self.assertEqual(transcricao_para_base_conhecimento(antigo), antigo)
        self.assertEqual(transcricao_para_base_conhecimento(b"\xff\xfe\x00"), b"\xff\xfe\x00")
        self.assertIsNone(transcricao_para_base_conhecimento(None))


class AtualizarAudiosImportadosTests(unittest.TestCase):
    """O botão só rebaixa e remapeia: nada de reprocessar, IA ou qualidade."""

    def rodar(self, audios, result=None):
        result = resultado(sentimento=bloco_sentimento()) if result is None else result
        proibido = mock.Mock(side_effect=AssertionError("o botão não pode chamar isto"))
        with mock.patch.object(api, "get_audio_result", return_value=result) as baixar, \
                mock.patch.object(api, "reprocess_audio", proibido), \
                mock.patch("utils.ai_provider.create_analysis", proibido), \
                mock.patch("utils.prosodia_quality.run_quality_checks", proibido), \
                mock.patch("utils.prosodia_db.save_quality_check", proibido), \
                mock.patch("utils.prosodia_db.update_audio_content") as gravar, \
                mock.patch("utils.prosodia_db.save_high_activations") as momentos:
            resumo = api.atualizar_conteudo_audios_importados(audios)
        return resumo, baixar, gravar, momentos

    def test_atualiza_os_blobs_e_os_momentos_dos_audios_da_api(self):
        resumo, baixar, gravar, momentos = self.rodar(
            [{"id": 5, "session_id": "wa_5511999_42", "quality_status": "pass"}]
        )

        self.assertEqual(resumo, {"atualizados": 1, "ignorados": 0, "falhas": []})
        baixar.assert_called_once_with(42)
        audio_id, json_bytes, csv_bytes, sinc_bytes = gravar.call_args.args
        self.assertEqual(audio_id, 5)
        self.assertIn(b"sentimento_texto", csv_bytes)
        self.assertIn(b"segmento_idx", sinc_bytes)
        self.assertEqual(momentos.call_args.args[0], 5)
        for momento in momentos.call_args.args[1]:
            self.assertIn("segmento_idx", momento)

    def test_ignora_upload_manual_e_audio_em_processamento(self):
        resumo, baixar, gravar, _ = self.rodar([
            {"id": 1, "session_id": "entrevista_manual", "quality_status": "pass"},
            {"id": 2, "session_id": "wa_5511_9", "quality_status": "processing"},
            {"id": 3, "session_id": "wa_upload_10", "quality_status": "failed"},
        ])

        self.assertEqual(resumo["ignorados"], 3)
        baixar.assert_not_called()
        gravar.assert_not_called()

    def test_falha_num_audio_nao_para_os_outros(self):
        with mock.patch.object(api, "map_api_result_to_all_formats",
                               side_effect=[ValueError("json quebrado"), (b"{}", b"a", b"start_s\n1\n")]):
            resumo, _, gravar, _ = self.rodar([
                {"id": 1, "session_id": "wa_1_1", "quality_status": "warn"},
                {"id": 2, "session_id": "wa_1_2", "quality_status": "warn"},
            ])

        self.assertEqual(resumo["atualizados"], 1)
        self.assertEqual(resumo["falhas"], [("wa_1_1", "json quebrado")])
        self.assertEqual(gravar.call_count, 1)


if __name__ == "__main__":
    unittest.main()
