"""
Relatórios de exemplo para o desenho (Fase 3 de docs/SPEC_RELATORIO_POR_TEMA.md).

O setor é fictício (a rede de lavanderias "Lava Já", como nas specs): nenhum dado
da Smart Fit entra no repositório. Cada função devolve um documento do
esquema de utils/relatorio_esquema.py, com os casos que o desenho precisa
cobrir:

- `rede()`: 64 respondentes em 6 unidades, com alerta crítico, capítulos em
  padrão, sinal e menção isolada, Outros, Δ, voz avaliável e não avaliável,
  leituras transversais, as duas faixas da Matriz e reclamações em cartão e
  em tabela.
- `exploratorio()`: 13 respondentes numa unidade só, leitura exploratória (sem
  recomendação, com "o que acompanhar"), sem alerta nem leitura transversal,
  um grupo vazio na Matriz e reclamações só de menção isolada.
"""

from typing import Dict, List, Optional


def _pol(positiva: int = 0, negativa: int = 0, mista: int = 0, neutra: int = 0) -> Dict[str, int]:
    return {"positiva": positiva, "negativa": negativa, "mista": mista, "neutra": neutra}


def _trecho(alias, tempo, texto, nota, polaridade, mascarado=False) -> Dict:
    return {
        "alias": alias, "tempo": tempo, "texto": texto, "nota": nota,
        "polaridade": polaridade, "mascarado": mascarado,
    }


def _voz(avaliavel=False, direcao="", nivel="", qualificador="", frase="", com_voz=0) -> Dict:
    return {
        "avaliavel": avaliavel, "respondentes_com_voz": com_voz, "direcao": direcao,
        "nivel": nivel, "qualificador": qualificador, "frase": frase,
    }


def _sub(cid, numero, nome, respondentes, polaridades, dito, falas, critica=None, subtemas=None) -> Dict:
    return {
        "id": "{}.s{}".format(cid, numero.split(".")[1]), "numero": numero, "nome": nome,
        "respondentes": respondentes, "polaridades": polaridades, "subtemas": subtemas or [],
        "o_que_foi_dito": dito, "critica": critica, "falas": falas,
    }


def _capitulo(numero, tema, faixa, respondentes, total, polaridades, voz, **resto) -> Dict:
    capitulo = {
        "id": "c{}".format(numero), "numero": numero, "tema": tema, "tipo": "tag", "faixa": faixa,
        "respondentes": respondentes, "total": total, "polaridades": polaridades, "voz": voz,
        "delta": None, "selo": None, "texto": None, "falas": [], "subcapitulos": [], "demais": None,
        "hipotese": None, "recomendacao": None, "acompanhar": None,
    }
    capitulo.update(resto)
    return capitulo


def _visao(capitulos: List[Dict]) -> List[Dict]:
    return [
        {"capitulo": c["id"], "tema": c["tema"], "respondentes": c["respondentes"],
         "total": c["total"], "polaridades": c["polaridades"]}
        for c in capitulos
    ]


def rede() -> Dict:
    c1 = _capitulo(
        1, "Equipamentos", "Padrão", 19, 64, _pol(positiva=1, negativa=17, mista=1),
        _voz(True, "abaixo", "alta", "reforça",
             "A voz confirma a crítica: é a dor dita com mais intensidade em toda a amostra.", 19),
        subcapitulos=[
            _sub("c1", "1.1", "Máquina fora de serviço", 14, _pol(negativa=14),
                 "Máquina parada com o ciclo já pago e um aviso de manutenção que se repete. O cliente só "
                 "descobre o problema depois de colocar a roupa.", ["t1", "t2"],
                 {"resumo": "Ciclo pago e perdido com a máquina parada, sem aviso antes do pagamento.",
                  "falas": ["t1", "t2"]},
                 ["máquina travou", "fora de serviço"]),
            _sub("c1", "1.2", "Secadora que não aquece", 4, _pol(negativa=3, mista=1),
                 "A secadora termina o ciclo e a roupa sai úmida. Dois clientes repetem o ciclo para "
                 "completar a secagem.", ["t3", "t4"],
                 {"resumo": "Roupa úmida no fim do ciclo, com um ciclo extra pago.", "falas": ["t3"]}),
            _sub("c1", "1.3", "Máquinas novas", 1, _pol(positiva=1),
                 "Um cliente da unidade Lapa elogia as máquinas novas, que lavam mais rápido.", ["t5"]),
        ],
        demais="Outros assuntos do tema: porta que trava 1 · medidor de sabão 1",
        hipotese={
            "texto": "A máquina parada é a dor com mais chance de virar reclamação pública: é a mais citada "
                     "e a única dita com tanta intensidade.",
            "como_validar": "Cruzar, por unidade, as menções ao tema com o tempo médio de reparo e com as "
                            "reclamações em canais públicos.",
            "apoia_em": ["c1.s1"],
        },
        recomendacao={
            "tipo": "Higiene", "driver": "Retenção", "area": "Manutenção",
            "texto": "Mostrar no app e na própria máquina o status \"fora de serviço\" antes do pagamento, e "
                     "estornar automaticamente o ciclo interrompido.",
            "como_medir": "Fatia de respondentes no tema na próxima análise (hoje 19/64) e estornos manuais "
                          "por unidade.",
            "responde_a": ["c1.s1"],
        },
    )
    c2 = _capitulo(
        2, "Atendimento", "Padrão", 13, 64, _pol(positiva=11, negativa=2),
        _voz(True, "acima", "alta", "reforça",
             "A voz reforça o elogio: o atendente é citado com mais energia que no restante da amostra.", 13),
        subcapitulos=[
            _sub("c2", "2.1", "Atendente da unidade", 10, _pol(positiva=10),
                 "O elogio vem com o nome do atendente e com uma ajuda fora do roteiro, como ensinar a "
                 "usar o app na primeira lavagem.", ["t6", "t7"]),
            _sub("c2", "2.2", "Suporte pelo WhatsApp", 3, _pol(positiva=1, negativa=2),
                 "O suporte por mensagem é elogiado quando responde na hora e criticado quando passa de "
                 "um dia sem retorno.", ["t8"],
                 {"resumo": "Demora de dois dias na resposta do suporte.", "falas": ["t8"]}),
        ],
        hipotese={
            "texto": "O vínculo com a loja passa pelo atendente: o elogio vem com nome e com energia na voz.",
            "como_validar": "Verificar, no recorte por unidade, se os elogios se concentram em poucas "
                            "unidades ou turnos.",
            "apoia_em": ["c2.s1"],
        },
        recomendacao={
            "tipo": "Encantamento", "driver": "Reputação", "area": "Operações das unidades",
            "texto": "Transformar o que os atendentes elogiados fazem em rotina de turno (cumprimentar pelo "
                     "nome, ensinar o app na primeira lavagem) e levar a rotina às unidades sem elogio.",
            "como_medir": "Elogios ao atendimento por unidade, na próxima análise.",
            "responde_a": ["c2.s1"],
        },
    )
    c3 = _capitulo(
        3, "Pagamento", "Padrão", 11, 64, _pol(positiva=1, negativa=9, mista=1),
        _voz(True, "habitual", "baixa", "atenua",
             "A voz atenua a crítica: o problema é relatado com pouca energia, o que combina mais com "
             "desistência silenciosa do que com reclamação ativa.", 11),
        delta={"anteriores": 6, "novos": 5, "total_anteriores": 52, "total_novos": 12, "tendencia": False},
        subcapitulos=[
            _sub("c3", "3.1", "Cobrança que não libera a máquina", 7, _pol(negativa=7),
                 "O app confirma o pagamento, mas a máquina segue bloqueada. O cliente não sabe a quem "
                 "recorrer na hora.", ["t9", "t10"],
                 {"resumo": "Pagamento confirmado no app e máquina bloqueada.", "falas": ["t9", "t10"]}),
            _sub("c3", "3.2", "Estorno demorado", 5, _pol(negativa=4, mista=1),
                 "O estorno prometido em 24 horas leva de uma a duas semanas para chegar.", ["t11", "t12"],
                 {"resumo": "Estorno que passa muito do prazo prometido.", "falas": ["t11", "t12"]}),
            _sub("c3", "3.3", "Preço do ciclo", 2, _pol(positiva=1, negativa=1),
                 "O preço aparece de passagem, uma vez como justo e outra como alto para a secagem.", []),
        ],
        hipotese={
            "texto": "A falha de pagamento virou atrito normalizado: o cliente a relata sem energia, o que "
                     "sugere desistência silenciosa.",
            "como_validar": "Comparar a recompra em 30 dias de quem teve estorno com a de quem não teve, e "
                            "perguntar na próxima coleta se o cliente deixou de usar a loja por causa do "
                            "pagamento.",
            "apoia_em": ["c3.s1", "c3.s2"],
        },
        recomendacao={
            "tipo": "Higiene", "driver": "Retenção", "area": "Produto digital",
            "texto": "Corrigir o ponto do fluxo em que a cobrança não libera a máquina; até a correção, "
                     "estornar em até 24 horas com aviso no app.",
            "como_medir": "Menções ao tema entre os respondentes novos (hoje 5/12) e prazo médio de estorno.",
            "responde_a": ["c3.s1", "c3.s2"],
        },
    )
    c4 = _capitulo(
        4, "Ambiente", "Sinal", 6, 64, _pol(positiva=2, negativa=4),
        _voz(True, "habitual", "media", "", "", 6),
        subcapitulos=[
            _sub("c4", "4.1", "Espera por máquina livre", 4, _pol(negativa=4),
                 "As quatro menções são de fim de semana, quando a loja enche e há fila na porta.",
                 ["t13", "t14"],
                 {"resumo": "Fila e espera de até quarenta minutos nos fins de semana.",
                  "falas": ["t13", "t14"]}),
            _sub("c4", "4.2", "Limpeza da loja", 3, _pol(positiva=2, negativa=1),
                 "A limpeza é elogiada em geral, com uma crítica ao chão molhado perto das secadoras.",
                 ["t15"],
                 {"resumo": "Chão molhado perto das secadoras.", "falas": ["t15"]}),
        ],
        hipotese={
            "texto": "A espera é um problema de pico de fim de semana, e não de capacidade geral da loja.",
            "como_validar": "Cruzar as menções com a ocupação por horário, quando a unidade a registrar.",
            "apoia_em": ["c4.s1"],
        },
        acompanhar={
            "texto": "Observar se a espera por máquina aumenta nos fins de semana e se a ocupação por "
                     "horário vira pedido recorrente.",
            "como_investigar": "Na próxima coleta, perguntar em que dia e horário o cliente esperou.",
            "porque": ["c4.s1"],
        },
    )
    c5 = _capitulo(
        5, "Estacionamento", "Menção isolada", 2, 64, _pol(negativa=2), _voz(False, com_voz=2),
        texto="Dois clientes reclamam da falta de vaga perto da loja Pinheiros, e um deles diz que desistiu "
              "de lavar no horário de pico.",
        falas=["t16"],
    )
    c6 = _capitulo(
        6, "Aplicativo", "Menção isolada", 1, 64, _pol(positiva=1), _voz(False, com_voz=1),
        texto="Um cliente elogia o aviso no app quando o ciclo termina.", falas=["t17"],
    )
    c7 = _capitulo(
        7, "Avaliação geral e outros assuntos", "", 24, 64, _pol(positiva=20, negativa=2, mista=1, neutra=1),
        _voz(True, "acima", "media", "reforça",
             "A voz acompanha o texto: os elogios gerais saem com mais energia que no restante da amostra.",
             24),
        tipo="outros", selo="Fora da taxonomia validada com a cliente",
        texto="Reúne o que não coube em nenhum dos temas: sobretudo elogios gerais à loja e relatos pessoais "
              "de experiência.",
        subcapitulos=[
            _sub("c7", "7.1", "Avaliação geral da loja", 18, _pol(positiva=17, negativa=1),
                 "Elogios curtos e gerais, sem detalhe: a loja é prática, limpa e funciona.", ["t18", "t19"]),
            _sub("c7", "7.2", "Experiência pessoal", 6, _pol(positiva=5, neutra=1),
                 "Relatos de como o cliente passou a usar a lavanderia e de como a rotina mudou.", ["t20"]),
        ],
        demais="Outros assuntos: horário de funcionamento 2 · espaço infantil 1",
    )
    capitulos = [c1, c2, c3, c4, c5, c6, c7]
    return {
        "versao": 2,
        "cabecalho": {
            "projeto": "Lava Já — Voz do Cliente por QR Code",
            "recorte": "Rede · 6 unidades",
            "periodo": ["14/09/2026", "24/11/2026"],
            "respondentes": 64, "audios": 71, "unidades": 6, "exploratoria": False,
            "gerado_em": "24/11/2026 19:23", "modelo": "gpt-4.1",
            "contexto": "Pesquisa de opinião com clientes de lavanderias self-service. O cliente escaneia um "
                        "QR Code na loja e envia um áudio espontâneo pelo WhatsApp.",
        },
        "alertas": [{
            "alias": "Áudio 047", "tema": "Alerta Crítico", "unidade": "Pinheiros (LJ-PIN-02)",
            "chegada": "12/11/2026", "tempo": "00:41",
            "evidencia": "Cliente relata o desaparecimento de pertences deixados na máquina.",
        }],
        "resumo": "A máquina fora de serviço é a dor mais citada (capítulo 1, 19 de 64) e a única dita com muita "
                  "intensidade na voz. A falha de pagamento (capítulo 3, 11 de 64) é relatada sem energia e "
                  "aparece em 5 dos 12 respondentes novos. O encantamento vem do atendente da unidade, citado "
                  "pelo nome (capítulo 2, 13 de 64). A ação prioritária é mostrar o status da máquina antes do "
                  "pagamento e estornar automaticamente o ciclo interrompido; em seguida, corrigir o fluxo de "
                  "pagamento.",
        "termometro": {
            "nota": -1, "media": -0.22, "positivos": 22, "neutros": 18, "negativos": 21, "avaliaveis": 61,
            "leitura": "O saldo fica levemente negativo por duas dores de higiene em padrão: máquina parada "
                       "(capítulo 1) e pagamento (capítulo 3). O que puxa para cima é o elogio ao atendente "
                       "(capítulo 2).",
        },
        "como_ler": [
            {"observamos": "64 respondentes, numa coleta aberta e espontânea.",
             "significa": "Os temas descrevem esta amostra: as frações não são a proporção do público."},
            {"observamos": "Os relatos são curtos (mediana de 23 s).",
             "significa": "Cada relato traz pouco contexto: um tema citado pode ter sido dito de passagem."},
            {"observamos": "4 dos 10 temas da taxonomia não foram citados.",
             "significa": "Numa coleta aberta, a ausência de menções não indica que o tema seja irrelevante."},
        ],
        "visao_geral": _visao(capitulos),
        "sem_mencao": ["Fidelidade e cupons", "Entrega em domicílio", "Segurança", "Wi-Fi"],
        "capitulos": capitulos,
        "trechos": {
            "t1": _trecho("Áudio 012", "00:14", "Paguei o ciclo, a máquina travou no meio e o aviso de manutenção só apareceu depois. Perdi o dinheiro e a roupa ficou molhada.", -0.8, "negativa"),
            "t2": _trecho("Áudio 031", "00:09", "Terceira vez que chego e a máquina nove está fora de serviço, sem nenhum aviso no app.", -0.7, "negativa"),
            "t3": _trecho("Áudio 019", "00:22", "A secadora termina e a roupa sai úmida, tive que pagar outro ciclo.", -0.6, "negativa"),
            "t4": _trecho("Áudio 028", "00:11", "Seca, mas demora o dobro do tempo. Já não compensa.", -0.5, "mista"),
            "t5": _trecho("Áudio 055", "00:08", "As máquinas novas daqui são ótimas, lavam rápido.", 0.7, "positiva"),
            "t6": _trecho("Áudio 003", "00:21", "O Marcos me ensinou a usar o app na primeira lavagem, sem eu pedir.", 0.9, "positiva"),
            "t7": _trecho("Áudio 040", "00:05", "Atendimento nota dez, sempre tem alguém para ajudar.", 0.8, "positiva"),
            "t8": _trecho("Áudio 037", "00:10", "Mandei mensagem no suporte e [funcionário] só respondeu dois dias depois.", -0.6, "negativa", True),
            "t9": _trecho("Áudio 008", "00:11", "Cobrou no cartão, a máquina não liberou e fiquei sem saber o que fazer.", -0.7, "negativa"),
            "t10": _trecho("Áudio 045", "00:17", "O app diz que pagou, mas a máquina continua bloqueada.", -0.6, "negativa"),
            "t11": _trecho("Áudio 052", "00:15", "Pedi o estorno e já faz uma semana, nada do dinheiro voltar.", -0.7, "negativa"),
            "t12": _trecho("Áudio 060", "00:19", "O estorno prometeram em 24 horas e chegou em dez dias.", -0.6, "negativa"),
            "t13": _trecho("Áudio 024", "00:08", "Sábado de manhã não tem máquina livre, tive que esperar quarenta minutos.", -0.5, "negativa"),
            "t14": _trecho("Áudio 066", "00:12", "Domingo é impossível, a loja enche e tem fila na porta.", -0.5, "negativa"),
            "t15": _trecho("Áudio 070", "00:16", "O chão fica molhado perto das secadoras e já quase escorreguei.", -0.5, "negativa"),
            "t16": _trecho("Áudio 058", "00:07", "Nunca tem vaga aqui perto, já desisti de lavar no sábado.", -0.5, "negativa"),
            "t17": _trecho("Áudio 071", "00:06", "Gosto do aviso no app quando o ciclo termina.", 0.6, "positiva"),
            "t18": _trecho("Áudio 021", "00:04", "Gosto muito da loja, sempre volto.", 0.8, "positiva"),
            "t19": _trecho("Áudio 063", "00:09", "Primeira vez que usei e achei tudo muito prático e limpo.", 0.8, "positiva"),
            "t20": _trecho("Áudio 029", "00:13", "Estou lavando aqui desde que mudei de bairro e me adaptei bem.", 0.5, "positiva"),
            "t21": _trecho("Áudio 073", "00:08", "Podia abrir mais cedo no domingo.", -0.2, "neutra"),
            "t22": _trecho("Áudio 075", "00:12", "O ciclo de secar ficou caro para o que entrega.", -0.5, "negativa"),
        },
        "transversais": [
            {"texto": "O encantamento depende de pessoas, e não de processo: o elogio ao atendimento vem com "
                      "nome e com energia na voz, enquanto equipamento e pagamento, que são processo, "
                      "concentram as críticas.",
             "como_validar": "Verificar, por unidade, se a nota do atendimento compensa as críticas de "
                             "equipamento e pagamento na recompra.",
             "capitulos": ["c1", "c2", "c3"]},
            {"texto": "As duas maiores dores são de higiene: o cliente não as elogia quando funcionam, e elas "
                      "pesam no saldo quando falham.",
             "como_validar": "Comparar a recompra de quem teve máquina parada ou estorno com a de quem não "
                             "teve.",
             "capitulos": ["c1", "c3"]},
        ],
        "matriz": {
            "avaliavel": True,
            "grupos": [
                {
                    "chave": "vinculo", "titulo": "Os relatos mais positivos",
                    "descricao": "Os elogios mais fortes do projeto, somando o que foi dito e o tom de voz.",
                    "linhas": [
                        {"alias": "Áudio 003", "unidade": "Lapa (LJ-LAP-05)", "temas": "Atendimento", "frase": "Elogia o atendente pelo nome e diz que ele ensinou o app sem ser chamado."},
                        {"alias": "Áudio 040", "unidade": "Moema (LJ-MOE-01)", "temas": "Atendimento", "frase": "Diz que sempre há alguém para ajudar e dá nota dez ao atendimento."},
                        {"alias": "Áudio 063", "unidade": "Tatuapé (LJ-TAT-06)", "temas": "Outros", "frase": "Primeira vez na loja e acha tudo prático e limpo."},
                        {"alias": "Áudio 021", "unidade": "Centro (LJ-CEN-04)", "temas": "Outros", "frase": "Gosta muito da loja e diz que sempre volta."},
                    ],
                    "fora_da_tabela": 12,
                    "voz_no_grupo": "Tom de voz no grupo: 9 de 16 com tom mais positivo que a média do projeto e 1 com tom mais negativo.",
                    "perfil": "Clientes que citam o atendente pelo nome e falam com mais energia que o restante da amostra; o vínculo é com a pessoa, e não com a marca.",
                    "acao": "Convidar para depoimento e indicação, e avisar a unidade antes de trocar de turno o atendente citado.",
                },
                {
                    "chave": "risco", "titulo": "Críticas ditas com resignação",
                    "descricao": "Relatos negativos ditos com menos energia na voz que a maioria do projeto: risco de abandono silencioso.",
                    "linhas": [
                        {"alias": "Áudio 045", "unidade": "Vila Nova (LJ-VNO-03)", "temas": "Pagamento", "frase": "O app diz que pagou, mas a máquina continua bloqueada."},
                        {"alias": "Áudio 060", "unidade": "Moema (LJ-MOE-01)", "temas": "Pagamento", "frase": "Estorno prometido em 24 horas que levou dez dias."},
                    ],
                    "fora_da_tabela": 0,
                    "voz_no_grupo": "Tom de voz no grupo: 0 de 2 com tom mais positivo que a média do projeto e 1 com tom mais negativo.",
                    "perfil": "Reclamação de pagamento dita sem energia, o retrato do atrito normalizado do capítulo 3.",
                    "acao": "Contato da unidade em até 7 dias, com confirmação do estorno e um ciclo de cortesia, acompanhando se o cliente volta.",
                },
            ],
        },
        "reclamacoes": {
            "abertura": "As reclamações se concentram em duas frentes de higiene: a máquina parada (capítulo 1) "
                        "e a cobrança que não libera o ciclo (capítulo 3). A primeira vem com intensidade na "
                        "voz; a segunda, com resignação.",
            "itens": [
                {"posicao": 1, "titulo": "Máquina fora de serviço", "tema": "Equipamentos", "origem": "1.1",
                 "respondentes": 14, "total": 64, "faixa": "Padrão",
                 "critica": "Ciclo pago e perdido com a máquina parada, sem aviso antes do pagamento.",
                 "falas": ["t1", "t2"], "etiqueta_voz": "Crítica dita com irritação perceptível",
                 "unidades": ["Moema (LJ-MOE-01)", "Pinheiros (LJ-PIN-02)", "Vila Nova (LJ-VNO-03)", "Centro (LJ-CEN-04)"]},
                {"posicao": 2, "titulo": "Cobrança que não libera a máquina", "tema": "Pagamento", "origem": "3.1",
                 "respondentes": 7, "total": 64, "faixa": "Padrão",
                 "critica": "Pagamento confirmado no app e máquina bloqueada.",
                 "falas": ["t9", "t10"], "etiqueta_voz": "Crítica dita com resignação, sem irritação na voz",
                 "unidades": ["Vila Nova (LJ-VNO-03)", "Moema (LJ-MOE-01)", "Lapa (LJ-LAP-05)"]},
                {"posicao": 3, "titulo": "Estorno demorado", "tema": "Pagamento", "origem": "3.2",
                 "respondentes": 5, "total": 64, "faixa": "Padrão",
                 "critica": "Estorno que passa muito do prazo prometido.",
                 "falas": ["t11", "t12"], "etiqueta_voz": None,
                 "unidades": ["Moema (LJ-MOE-01)", "Tatuapé (LJ-TAT-06)"]},
                {"posicao": 4, "titulo": "Espera por máquina livre", "tema": "Ambiente", "origem": "4.1",
                 "respondentes": 4, "total": 64, "faixa": "Sinal",
                 "critica": "Fila e espera de até quarenta minutos nos fins de semana.",
                 "falas": ["t13", "t14"], "etiqueta_voz": None,
                 "unidades": ["Pinheiros (LJ-PIN-02)", "Centro (LJ-CEN-04)"]},
                {"posicao": 5, "titulo": "Secadora que não aquece", "tema": "Equipamentos", "origem": "1.2",
                 "respondentes": 4, "total": 64, "faixa": "Sinal",
                 "critica": "Roupa úmida no fim do ciclo, com um ciclo extra pago.",
                 "falas": ["t3"], "etiqueta_voz": None,
                 "unidades": ["Lapa (LJ-LAP-05)"]},
            ],
            "isoladas": [
                {"tema": "Atendimento", "assunto": "Demora no suporte pelo WhatsApp", "unidade": "Vila Nova (LJ-VNO-03)", "fala": "t8", "origem": "2.2"},
                {"tema": "Estacionamento", "assunto": "Falta de vaga perto da loja", "unidade": "Pinheiros (LJ-PIN-02)", "fala": "t16", "origem": "5"},
                {"tema": "Ambiente", "assunto": "Chão molhado perto das secadoras", "unidade": "Centro (LJ-CEN-04)", "fala": "t15", "origem": "4.2"},
                {"tema": "Pagamento", "assunto": "Preço do ciclo de secar", "unidade": "Moema (LJ-MOE-01)", "fala": "t22", "origem": "3.3"},
                {"tema": "Outros", "assunto": "Horário de funcionamento", "unidade": "Lapa (LJ-LAP-05)", "fala": "t21", "origem": "7"},
            ],
        },
        "notas": [
            "Respondente é a unidade de contagem: áudios do mesmo número de WhatsApp contam como uma pessoa, "
            "que trata do tema se qualquer áudio dela tratar. Por isso as contagens dos temas não se somam.",
            "Faixas: 1 a 2 respondentes, menção isolada; 3 a 4, sinal; 5 ou mais e pelo menos 10% do total, "
            "padrão. A comparação com a análise anterior só aparece com 5 ou mais respondentes novos e, com "
            "menos de 20, vale como variação observada, e não como tendência.",
            "As tabelas de voz, o índice combinado, as divergências entre voz e texto e as médias por áudio "
            "estão no Anexo técnico do projeto.",
        ],
        "anexo": {"arquivo": "Anexo_Tecnico_Lava_Ja_2026-11-24.pdf"},
        "problemas": [],
    }


def exploratorio() -> Dict:
    c1 = _capitulo(
        1, "Atendimento", "Padrão", 9, 13, _pol(positiva=9),
        _voz(True, "habitual", "alta", "atenua",
             "A voz traz mais energia que o restante da amostra, mas sem mudar o tom do elogio.", 9),
        subcapitulos=[
            _sub("c1", "1.1", "Recepção", 5, _pol(positiva=5),
                 "A recepção é elogiada pela cordialidade e pela rapidez no acesso.", ["t1", "t2"]),
            _sub("c1", "1.2", "Professores", 4, _pol(positiva=4),
                 "Os professores são citados pela atenção e pela disposição para ajudar.", ["t3"]),
        ],
        hipotese={
            "texto": "O atendimento da equipe pode estar sustentando o vínculo dos clientes com esta unidade.",
            "como_validar": "Medir, nas próximas coletas, se as menções ao atendimento seguem altas e se "
                            "aparecem junto de intenção de voltar.",
            "apoia_em": ["c1.s1"],
        },
        acompanhar={
            "texto": "Observar se o atendimento segue citado com energia e se continua sendo o motivo do "
                     "vínculo.",
            "como_investigar": "Perguntar na próxima coleta o que o atendimento fez de diferente.",
            "porque": ["c1.s1", "c1.s2"],
        },
    )
    c2 = _capitulo(
        2, "Limpeza", "Padrão", 5, 13, _pol(positiva=3, negativa=1, mista=1),
        _voz(True, "habitual", "media", "", "", 5),
        subcapitulos=[
            _sub("c2", "2.1", "Sanitários", 2, _pol(positiva=1, negativa=1),
                 "Um cliente elogia a limpeza dos sanitários, e outro critica a falta de papel higiênico.",
                 ["t4"], {"resumo": "Falta de papel higiênico no banheiro.", "falas": ["t4"]}),
            _sub("c2", "2.2", "Áreas de treino", 2, _pol(positiva=2),
                 "As áreas de treino são descritas como limpas e organizadas.", ["t5"]),
        ],
        acompanhar={
            "texto": "Monitorar a reposição de itens básicos nos sanitários, para evitar que a crítica "
                     "pontual vire padrão.",
            "como_investigar": "Perguntar na próxima coleta em que horário a falta foi percebida.",
            "porque": ["c2.s1"],
        },
    )
    c3 = _capitulo(
        3, "Estrutura da loja", "Sinal", 3, 13, _pol(positiva=2, mista=1), _voz(False, com_voz=2),
        subcapitulos=[
            _sub("c3", "3.1", "Estrutura física", 3, _pol(positiva=2, mista=1),
                 "A estrutura é descrita como boa, e um dos relatos traz uma ressalva sem detalhar.",
                 ["t6"]),
        ],
        acompanhar={
            "texto": "Acompanhar se o tema ganha mais menções e se a ressalva ganha detalhe.",
            "como_investigar": "Perguntar o que o cliente mudaria na estrutura.",
            "porque": ["c3.s1"],
        },
    )
    c4 = _capitulo(4, "Equipamentos", "Menção isolada", 1, 13, _pol(negativa=1), _voz(False, com_voz=1),
                   texto="Um cliente critica o estado das esteiras da unidade.", falas=["t7"])
    c5 = _capitulo(5, "Climatização", "Menção isolada", 1, 13, _pol(negativa=1), _voz(False, com_voz=1),
                   texto="Um cliente relata que o ar-condicionado quebra nos dias de calor.", falas=["t8"])
    c6 = _capitulo(
        6, "Avaliação geral e outros assuntos", "", 4, 13, _pol(positiva=3, mista=1), _voz(False, com_voz=4),
        tipo="outros", selo="Fora da taxonomia validada com a cliente",
        texto="Elogios gerais à academia e ao treino, sem tema específico.",
        subcapitulos=[
            _sub("c6", "6.1", "Avaliação geral", 3, _pol(positiva=3),
                 "Elogios curtos à experiência na unidade.", ["t9"]),
        ],
    )
    capitulos = [c1, c2, c3, c4, c5, c6]
    return {
        "versao": 2,
        "cabecalho": {
            "projeto": "Lava Já — Voz do Cliente por QR Code",
            "recorte": "Unidade Moema (LJ-MOE-01)",
            "periodo": ["14/09/2026", "24/11/2026"],
            "respondentes": 13, "audios": 15, "unidades": 1, "exploratoria": True,
            "gerado_em": "24/11/2026 19:23", "modelo": "gpt-4.1",
            "contexto": "Pesquisa de opinião com clientes de lavanderias self-service. O cliente escaneia um "
                        "QR Code na loja e envia um áudio espontâneo pelo WhatsApp.",
        },
        "alertas": [],
        "resumo": "Nesta amostra exploratória de 13 respondentes da unidade Moema, o atendimento é o tema mais "
                  "citado e vem em elogios (capítulo 1). A limpeza aparece com elogios e uma crítica pontual à "
                  "falta de papel higiênico (capítulo 2). A estrutura da loja surge como sinal, com poucos "
                  "relatos (capítulo 3). Com menos de 20 respondentes, nada aqui é recomendação: são pontos a "
                  "acompanhar nas próximas análises.",
        "termometro": {
            "nota": 2, "media": 0.31, "positivos": 6, "neutros": 2, "negativos": 1, "avaliaveis": 9,
            "leitura": "O saldo positivo vem dos elogios ao atendimento (capítulo 1). A limpeza e a estrutura "
                       "aparecem com menções mistas e puxam o saldo para o meio.",
        },
        "como_ler": [
            {"observamos": "13 respondentes, abaixo de 20.",
             "significa": "Os resultados são sinais iniciais, a acompanhar nas próximas análises, e não "
                          "conclusões. Por isso esta análise não traz recomendações de ação."},
            {"observamos": "Os relatos são curtos (mediana de 22 s).",
             "significa": "Cada relato traz pouco contexto: um tema citado pode ter sido dito de passagem."},
            {"observamos": "5 dos 11 temas da taxonomia não foram citados.",
             "significa": "Numa coleta aberta, a ausência de menções não indica que o tema seja irrelevante."},
        ],
        "visao_geral": _visao(capitulos),
        "sem_mencao": ["Lotação", "Aulas coletivas", "Aplicativo", "Financeiro", "Entrega em domicílio"],
        "capitulos": capitulos,
        "trechos": {
            "t1": _trecho("Áudio 003", "00:15", "Todo mundo na recepção é muito simpático, entro e já sou atendido.", 0.9, "positiva"),
            "t2": _trecho("Áudio 007", "00:17", "A recepção é rápida, nunca tive que esperar para entrar.", 0.7, "positiva"),
            "t3": _trecho("Áudio 075", "00:54", "O professor é muito atencioso e sempre disponível para tirar dúvida.", 0.9, "positiva"),
            "t4": _trecho("Áudio 013", "00:06", "Um absurdo virar o banheiro e não ter papel para se secar.", -0.7, "negativa"),
            "t5": _trecho("Áudio 007", "00:22", "A área de treino está sempre limpa e organizada.", 0.6, "positiva"),
            "t6": _trecho("Áudio 075", "00:09", "É uma boa unidade, a estrutura é boa, só falta cuidar mais.", 0.5, "mista"),
            "t7": _trecho("Áudio 006", "00:28", "As esteiras estão sempre ruins, precisavam ser renovadas.", -0.5, "negativa"),
            "t8": _trecho("Áudio 006", "00:36", "O ar-condicionado vive com problema, no calor quebra sempre.", -0.5, "negativa"),
            "t9": _trecho("Áudio 010", "00:08", "Gosto muito da academia, estou evoluindo a cada semana.", 0.7, "positiva"),
        },
        "transversais": [],
        "matriz": {
            "avaliavel": True,
            "grupos": [
                {
                    "chave": "vinculo", "titulo": "Os relatos mais positivos",
                    "descricao": "Os elogios mais fortes do projeto, somando o que foi dito e o tom de voz.",
                    "linhas": [
                        {"alias": "Áudio 003", "unidade": None, "temas": "Atendimento", "frase": "Elogia o atendimento da equipe e dá nota dez."},
                        {"alias": "Áudio 010", "unidade": None, "temas": "Outros", "frase": "Conta a evolução no treino e diz que gosta da academia."},
                    ],
                    "fora_da_tabela": 0,
                    "voz_no_grupo": "Tom de voz no grupo: 0 de 2 com tom mais positivo que a média do projeto e 0 com tom mais negativo.",
                    "perfil": "Relatos que combinam elogio e tom de voz firme, sobretudo sobre o atendimento da equipe.",
                    "acao": "Acompanhar e valorizar esses depoimentos para fortalecer a reputação da unidade.",
                },
                {
                    "chave": "risco", "titulo": "Críticas ditas com resignação",
                    "descricao": "Relatos negativos ditos com menos energia na voz que a maioria do projeto: risco de abandono silencioso.",
                    "linhas": [], "fora_da_tabela": 0, "voz_no_grupo": None, "perfil": None, "acao": None,
                },
            ],
        },
        "reclamacoes": {
            "abertura": "As críticas desta unidade são poucas e pontuais: a reposição de papel higiênico "
                        "(capítulo 2) e itens isolados de equipamento e climatização.",
            "itens": [],
            "isoladas": [
                {"tema": "Limpeza", "assunto": "Falta de papel higiênico no banheiro", "unidade": None, "fala": "t4", "origem": "2.1"},
                {"tema": "Equipamentos", "assunto": "Esteiras em mau estado", "unidade": None, "fala": "t7", "origem": "4"},
                {"tema": "Climatização", "assunto": "Ar-condicionado quebra no calor", "unidade": None, "fala": "t8", "origem": "5"},
            ],
        },
        "notas": [
            "Respondente é a unidade de contagem: áudios do mesmo número de WhatsApp contam como uma pessoa.",
            "Leitura exploratória: com menos de 20 respondentes os números descrevem esta amostra e não se "
            "estendem ao público.",
            "As tabelas de voz, o índice combinado e as médias por áudio estão no Anexo técnico do projeto.",
        ],
        "anexo": {"arquivo": "Anexo_Tecnico_Lava_Ja_2026-11-24.pdf"},
        "problemas": [],
    }
