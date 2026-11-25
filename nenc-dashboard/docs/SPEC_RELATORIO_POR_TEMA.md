# Relatório por tema e visual novo da Análise Geral — especificação

- **Status:** rascunho para revisão, com a revisão de 25/11/2026 incorporada (projetos sem tags, lançamento e ordem de execução: §2, itens 25 a 29)
- **Data:** 25/11/2026
- **Responsável:** Pedro Rocha
- **Escopo:** a Análise Geral do NencBoost (Prosódia) e a análise individual por áudio: estrutura do relatório, agregação, prompts, verificação, armazenamento e o PDF. Jornada de Compra e Teste Sensorial seguem com o `pdf_report` (fpdf2) e não mudam.
- **Origem:** os PDFs da Smart Fit gerados em 24/11/2026 (rede, 21 páginas, e Duque de Caxias 2, 10 páginas) e a sessão de decisões de 24 e 25/11/2026. Continua a [especificação v2](SPEC_ANALISE_GERAL.md) e a [de Unidades e tags](SPEC_UNIDADES_E_TAGS.md), que seguem valendo onde esta não muda nada.
- **Base do código:** a `main` com as fases 1 a 5 de Unidades e tags (PR #12). O texto cita funções pelo nome, e não por linha.

## 1. Por que mudar

| Observado nos PDFs de 24/11 | Causa | Resposta desta spec |
|---|---|---|
| As críticas ficam espalhadas: a falta de papel higiênico é uma frase dentro do achado de Limpeza. | Achados por tema, sem lugar próprio para a crítica. | Seção "Reclamações e feedbacks negativos" (§3.6). |
| Achados, Hipóteses, Recomendações e Para acompanhar percorrem todos os temas, e o leitor costura A2, H2, R3 e M2. | Estrutura por tipo de conteúdo. | Um capítulo por tema, com tudo dentro (§3.2). |
| Subtemas fragmentados: "atendimento na recepção 3 · recepção 2 · atendimento 1 · atendimento dos professores 1 · e mais 6". | A IA cria o subtema a cada áudio. | Consolidação de sinônimos antes de contar (§4). |
| O rodapé cobre a última linha da tabela, e o texto justificado abre vãos nas células. | `pdf_report` calcula a quebra de página à mão. | HTML + CSS com WeasyPrint (§8). |
| Marcação vazando: `*Apoia-se em:*`, `_Faixas…_`, `*Perfil:*`. | O PDF reinterpreta o markdown da IA. | O PDF sai de JSON, sem interpretar markdown (§5, §8). |
| Nove das 16 tags saem como "0/13 sem menções" e ocupam uma página inteira. | "Todas as tags, sempre", como linhas de tabela. | Gráfico de visão geral e uma linha compacta (§3.3). |
| A página 1 é o contexto do projeto (briefing). | `_build_project_analysis_pdf` o põe antes do resultado. | Capa com duas linhas de contexto (§3.1). |
| A auditoria vai no PDF do cliente: o apêndice técnico ocupa 6 das 10 páginas de Duque de Caxias (da 5 à 10) e 16 das 21 da rede (da 6 à 21). | Apêndice técnico no mesmo arquivo. | Anexo técnico separado (§9). |
| A mesma frase ("Sem destaque em relação aos demais relatos") aparece 21 vezes na rede: tabela, achado e apêndice de cada um dos 7 temas. | Frase fixa repetida em três lugares. | Voz em gráfico no capítulo, sem frase quando não há destaque (§3.2). |
| As falas de exemplo são curtas e de elogio ("Tá sempre bem.", "Graças a Deus, nota 10, todo mundo."). | `_escolher_trechos` ordena pela força da nota do texto. | A IA escolhe entre candidatos informativos, e o código insere a fala (§5.3). |
| Outros é 45 de 98 respondentes (46%) na rede e não tem leitura. | Linha sem faixa, sem achado e sem voz. | Último capítulo, com subcapítulos (§3.4). |
| A unidade sai só como código ("SBRPICLOU01") em áudios sem nome cadastrado. | Cadastro de unidade incompleto. | Rótulo "nome (código)" e pedido à Smart Fit (§11). |
| Nomes de funcionários ("Beto", "Simone e Jansen") e um dado financeiro ("R$90,90 … Nubank") aparecem em falas. | Falas literais sem tratamento. | Máscara em código (§3.6, §6). |
| A análise individual só chega à Geral pelo Sumário Executivo. | `sumarios_individuais` usa só `sumario_executivo`. | Ficha de contexto estruturada (§10). |

## 2. Decisões tomadas

| # | Tema | Decisão |
|---|---|---|
| 1 | Estrutura | Um capítulo por tema, com subcapítulos. Hipótese e recomendação ficam dentro do capítulo. Só o que cruza temas vai para "Leituras transversais". |
| 2 | Tema | O tema é a tag (taxonomia com tags abertas) ou a Categoria (taxonomia com subtemas fixos). O subcapítulo é o subtema consolidado (tag aberta) ou o subtema fixo. |
| 3 | Quais temas | Todo tema citado. Padrão e sinal ganham capítulo completo; menção isolada, capítulo curto; sem menção, uma linha compacta. |
| 4 | Subcapítulos | Um passo de IA consolida sinônimos dentro de cada tag aberta antes de contar. O código recalcula as contagens por respondente e guarda o mapeamento. |
| 5 | Outros | Último capítulo, "Avaliação geral e outros assuntos", rotulado como fora da taxonomia validada. Subtemas com 3 ou mais respondentes ganham subcapítulo. |
| 6 | Blocos do capítulo | Ficha, mini-gráfico de polaridade, leitura de voz em gráfico, subcapítulos com falas de exemplo, Δ quando houver e hipótese e recomendação. Sem distribuição por unidade. |
| 7 | Reclamações | Seção própria, depois dos capítulos e das leituras transversais. A crítica aparece nos dois lugares. |
| 8 | O que entra | Subtemas negativos, inclusive isolados, e a parte crítica dos mistos. Ficam fora sugestões e crítica silenciosa. |
| 9 | Ordem | Ranking por prioridade, com número de respondentes e intensidade da voz. |
| 10 | Nomes | Fala crítica é mascarada em código (pessoas, dados financeiros e de contato). Fala de elogio mantém o nome. |
| 11 | Unidade | Só como rótulo em cada reclamação, e só em relatório de várias unidades. A coluna "Unidades" e o "concentrada em" saem dos capítulos. |
| 12 | Termômetro e Matriz | O Termômetro vai para a capa, em medidor, e sua leitura logo após o Resumo. A Matriz vem depois dos capítulos, junto das leituras transversais. |
| 13 | IDs | Saem A/H/R/M do PDF. Valem a numeração (2, 2.1) e "Responde a 2.1". A rastreabilidade fica no JSON. |
| 14 | Público | O cliente. O PDF principal é só leitura de negócio. |
| 15 | Anexo técnico | Um por projeto, no mesmo zip, com todos os áudios e a coluna de unidade. |
| 16 | Geração | Uma chamada por tema, em JSON, mais uma de síntese. O código verifica cada capítulo e refaz só o que falhou. |
| 17 | Modelo | `gpt-4.1` escreve capítulos e síntese. A classificação por áudio continua no `gpt-4.1-mini`. |
| 18 | Qualidade do texto | Cortar enchimento e repetição, e escolher falas informativas. |
| 19 | PDF | HTML + CSS (Jinja, gráficos em SVG) convertido com WeasyPrint. |
| 20 | Elementos visuais | Capa e índice clicável, visão geral dos temas em barras empilhadas, Termômetro em medidor e leitura de voz do tema em gráfico. |
| 21 | App | A página mostra o mesmo HTML. O `.md` da base de conhecimento sai do JSON, que vira a fonte única no banco. |
| 22 | Análises antigas | Abrem no visual novo, sem capítulos. O Δ segue lendo o snapshot gravado. |
| 23 | Análise individual | Vira ficha de contexto estruturada, enviada inteira à Geral. |
| 24 | Alcance | Prosódia: Análise Geral e análise individual. |
| 25 | Projetos sem tags | **Seguem com o relatório v2** (Achados, Hipóteses, Recomendações, em markdown). Só a taxonomia com pelo menos uma tag aberta (`tem_tags_abertas`) gera capítulos. Em taxonomia mista, o subtema fixo vira capítulo de Categoria (§4). |
| 26 | PDF da v2 | O texto v2 e as análises antigas saem pelo mesmo WeasyPrint, no template legado (`markdown-it-py`), com a capa, o cabeçalho e o rodapé novos. O "Apêndice técnico" do texto v2 é separado e vira o Anexo técnico. O fpdf2 de Prosódia sai do código. |
| 27 | Lançamento | A geração por capítulos entra em produção atrás de um **interruptor por projeto**, desligado até o visual novo (Fase 3) estar pronto. A Smart Fit não recebe o estado intermediário. |
| 28 | Ordem de execução | **Visual primeiro**: Fase 0 e Fase 3, com fixtures fictícias montadas dos PDFs de 24/11. Depois as Fases 1 e 2, depois a 4. Uma fase por vez, uma PR cada, em worktree a partir de `origin/main`. |
| 29 | Ambiente | O WeasyPrint roda no Windows de desenvolvimento pelo Pango do MSYS2, instalado com `winget` (decidido em 25/11/2026; sem Docker nem WSL na máquina). |

## 3. Estrutura do relatório

### 3.1 Ordem fixa

Vale para o relatório de projetos com tag aberta (§2, item 25). O de projetos sem tags segue o texto da v2, no visual do template legado (§9).

1. **Capa** (código): projeto, recorte (unidade ou rede), período, respondentes, data, selo "Leitura exploratória" quando valer, Termômetro em medidor e duas linhas de contexto do projeto (a primeira frase da `especialidade`).
2. **Índice** (código): capítulos e subcapítulos com número de página e links.
3. **Alertas críticos** (código, só quando houver), como em `alertas_markdown`.
4. **Resumo Executivo** (IA, na síntese), com a **leitura do Termômetro** (IA) e o **Como ler** curto (código, §3.7).
5. **Visão geral dos temas** (código): uma barra horizontal por tema citado, com o número de respondentes dividido em elogios, críticas, mistas e neutras. Os temas sem menção entram numa linha: "Sem menção nesta amostra: A, B, C. Numa coleta aberta, a ausência de menções não indica que o tema seja irrelevante."
6. **Capítulos 1 a n** (§3.2), ordenados do mais citado ao menos citado, com "Outros" por último.
7. **Leituras transversais** e **Matriz de Destaques** (§3.5).
8. **Reclamações e feedbacks negativos** (§3.6).
9. **Notas metodológicas** (código, meia página): o que significam faixa, respondente e Δ, com remissão ao Anexo técnico.

Os capítulos são numerados de 1 a n. "Leituras transversais" e "Reclamações" recebem n+1 e n+2.

### 3.2 O capítulo

| Faixa do tema | Conteúdo |
|---|---|
| **Padrão** | Cabeçalho (nome, n/N com percentual, faixa e mini-gráfico de polaridade), leitura de voz em gráfico, subcapítulos, hipótese e recomendação. |
| **Sinal** | O mesmo, com "O que acompanhar" no lugar da recomendação. A hipótese é permitida. |
| **Menção isolada** | Cabeçalho e um parágrafo com o que foi dito e uma fala. Sem hipótese, recomendação nem "O que acompanhar". |
| **Leitura exploratória** (menos de 20 respondentes) | Nenhum capítulo leva recomendação. O padrão e o sinal levam "O que acompanhar e investigar", com um *Como investigar*. |

Em cada subcapítulo, na ordem: título (o nome consolidado), n de respondentes e polaridade em palavras, **o que foi dito** (até duas frases), e **até duas falas** de respondentes diferentes, com Áudio NN, tempo mm:ss e a nota do texto. Se o subcapítulo tem crítica, ele traz também a linha **Crítica** (uma frase) e as falas críticas, que a seção de reclamações reaproveita.

- **Leitura de voz do tema.** Um marcador numa grade valência (abaixo, no habitual, acima) × ativação (baixa, média, alta). A regra é a de `ler_voz` e `sinal_emocional` e não muda: com menos de 5 respondentes com voz, o gráfico diz "voz não avaliável". Com voz no habitual e ativação média, o gráfico mostra o marcador no centro, **sem frase de destaque**. Só a leitura que diz algo (reforça, atenua, contradiz) ganha uma frase curta.
- **Δ.** "N anteriores → M novos", só com 5 ou mais respondentes novos; com menos de 20 vale como variação observada, não tendência (regra de `Delta`).
- **Hipótese.** Uma frase com "pode indicar" ou "sugere" e um *Como validar*. Nenhuma hipótese se apoia só em menção isolada.
- **Recomendação** (cartão): [Higiene|Encantamento], Driver (retenção, aquisição, custo operacional ou reputação), Área, a ação, *Responde a* (os subcapítulos, como "2.1") e *Como medir*. Só nasce de tema em padrão fora da leitura exploratória, e nunca de menção isolada.
- **Faixa só no tema.** O subcapítulo mostra o número de respondentes e a polaridade, sem faixa própria, como hoje. O Δ também é do tema.

Exemplo de cabeçalho, no setor fictício de lavanderias das specs anteriores:

```
2  Pagamento                              11/64 (17%) · Padrão
   ■■■■■■■■■□□ 9 críticas  ■ 1 mista  ■ 1 elogio     voz: ▪ abaixo do habitual · ativação baixa
   2.1 Cobrança que não libera a máquina ...
   2.2 Estorno demorado ...
   Hipótese ...      Recomendação [Higiene · Retenção · Produto digital] ...
```

### 3.3 Visão geral e temas sem menção

A visão geral substitui a tabela de taxonomia do corpo. A tabela completa, com todas as tags, a coluna Subtemas e a legenda, vai para o Anexo técnico (§9). A regra "todas as tags, sempre" de [Unidades §4.3](SPEC_UNIDADES_E_TAGS.md) continua valendo, agora como a linha de temas sem menção.

### 3.4 Outros

O último capítulo traz o título "Avaliação geral e outros assuntos" e o selo "Fora da taxonomia validada com a cliente". Subtemas de Outros com `MIN_CANDIDATO` (3) respondentes ou mais no projeto, os candidatos a tag nova, ganham subcapítulo; os demais entram numa linha. Sem hipótese nem recomendação. A leitura de voz segue a mesma regra do tema. Em taxonomia sem tag aberta, "Outros" é o conjunto dos emergentes.

### 3.5 Leituras transversais e Matriz

- **Leituras transversais.** De zero a três hipóteses que cruzam dois ou mais temas ("o encantamento depende de pessoas, não de processo"), cada uma citando os capítulos de origem e um *Como validar*. Só entra hipótese que se apoie em pelo menos dois capítulos. Sem nenhuma, a seção não aparece, nem no índice.
- **Matriz de Destaques.** O conteúdo é o de hoje (`matriz_markdown`): os dois grupos, com a tabela de áudios, o texto "Em uma frase", a linha de voz do grupo e a leitura da IA (*Perfil* e *Ação*). Os números continuam no Anexo técnico. A coluna Unidade segue como hoje, com o rótulo "nome (código)".

### 3.6 Reclamações e feedbacks negativos

**O que entra.** Cada par (capítulo, subcapítulo) com pelo menos um respondente com crítica:
- polaridade **negativa**, mesmo de um respondente só;
- polaridade **mista**, quando pelo menos um trecho citado tem nota do texto até −0,2 (`LIMIAR_TEXTO`). O trecho crítico é o que entra; a parte elogiosa fica só no capítulo. Mista sem trecho com nota negativa não entra e fica registrada no JSON como "mista sem trecho crítico", para a validação (§12).

**O que não entra.** Sugestões e pedidos de melhoria e a crítica silenciosa (texto positivo × voz negativa). A Matriz continua com "Críticas ditas com resignação" para o que a voz indica.

**Ordem.** Faixa do item (padrão, sinal, isolada, pelo mesmo corte de `faixa`, sobre os respondentes críticos), depois o número de respondentes críticos, depois a intensidade da voz (irritação perceptível antes de resignação, antes dos demais), depois a ordem dos capítulos. O critério é determinístico, não da IA, e o "Como ler" o diz em uma frase. Frequência e intensidade nem sempre apontam para o mesmo problema, então a ordem **não** as funde em uma nota: a intensidade aparece como etiqueta.

**O item** (cartão): posição, título (o nome do subcapítulo), tema, n/N de respondentes críticos e a faixa, a frase **Crítica** do capítulo, até duas falas (mascaradas), a etiqueta de voz, a unidade (em relatório de várias unidades) e "origem: 2.1".
- **Etiqueta de voz.** Sai de `sinal_emocional(voz, "negativo")`, sobre os trechos críticos dos respondentes do item. Só aparece com 5 ou mais respondentes com voz e quando a leitura não é "sem destaque". Na prática, só os itens maiores a terão; os demais saem sem etiqueta, e não com "não avaliável" repetido.
- **Isoladas.** Os itens de menção isolada saem numa tabela compacta (tema, assunto, unidade, fala curta), sem cartão.
- **Abertura.** Duas ou três frases da IA (na síntese) sobre o que une as reclamações, citando capítulos.

**Máscara** (`utils/prosodia_mascara.py`, §6). Vale para toda fala crítica, onde quer que ela apareça (capítulo, reclamações, Anexo). A fala de elogio mantém o nome da pessoa.

### 3.7 Como ler e notas metodológicas

O "Como ler esta análise" da v2 (`como_ler_markdown`) vira uma caixa de até três linhas na página do Resumo, só com as condições que valem, no formato *O que observamos → O que isso significa*, sem repetir blocos iguais. O resto (faixas, respondente, Δ, limiares) vai para as **Notas metodológicas**, em meia página. O detalhe técnico completo (`_limitacoes`) fica no Anexo.

## 4. Consolidação de subtemas

**Onde.** Etapa 2b da geração, depois da classificação e antes da agregação, só para tags abertas.

**Entrada, por tag:** os subtemas que a IA criou nos áudios do **projeto inteiro** (não só do recorte, para o mesmo grupo ter o mesmo nome em todos os relatórios do lote), com o número de respondentes de cada um e uma fala curta de exemplo, e a definição da tag.

**Saída (JSON):** de 2 a 5 grupos por tag, cada um com um nome neutro e curto (até cinco palavras, sem avaliação, como "Recepção") e a lista dos subtemas que reúne. **Regras validadas em código:**
- cada subtema de entrada está em exatamente um grupo; subtema que a IA inventou é descartado; subtema que sobrou entra em "Outros assuntos do tema";
- nome de grupo repetido é fundido;
- tag com até 3 subtemas distintos não passa pela IA: cada subtema vira o seu grupo.

**Contagem.** `agregar` aplica o mapeamento: o respondente de um grupo é a união dos respondentes dos subtemas dele, e a polaridade do grupo, a de `juntar_polaridades` sobre as polaridades deles. A contagem da **tag** não muda. As falas do grupo vêm das ocorrências dos subtemas que ele reúne.

**Cache.** Tabela `subtema_grupos` (`organization_id`, `project_id`, `tag`, `grupo`, `subtemas_json`, `chave`, `modelo`, `created_at`), com a `chave` igual ao hash dos subtemas da entrada e da definição da tag. A etapa roda de novo só quando a chave muda (subtema novo, definição da tag alterada), e a IA recebe os grupos existentes para reaproveitar os nomes. Falha da IA: o capítulo usa os subtemas crus, os 5 mais citados e "Outros assuntos do tema", com um aviso em `problemas`.

**Taxonomia sem tag aberta.** Não gera capítulos: o projeto segue com o relatório v2 (§2, itens 25 e 26). **Taxonomia mista** (pelo menos uma tag aberta e linhas "Categoria > Subtema" em outras categorias): usa o relatório por capítulos, e cada categoria de subtemas fixos vira um capítulo de tipo `categoria`, com o subtema fixo como subcapítulo, sem a etapa de consolidação. O n de respondentes do capítulo é a união dos respondentes dos subtemas da categoria, e a faixa do capítulo sai desse número.

## 5. Dados e geração

### 5.1 O relatório em JSON

O relatório vira um documento estruturado, fonte única do app, do PDF e do `.md`. Os campos marcados **[IA]** são escritos pelo modelo; os demais, calculados em código.

```json
{
  "versao": 2,
  "cabecalho": {"projeto": "", "recorte": "", "periodo": [], "respondentes": 0, "audios": 0,
                "exploratoria": false, "gerado_em": "", "modelo": "", "contexto": ""},
  "alertas": [{"alias": "", "tema": "", "unidade": "", "chegada": "", "tempo": "", "evidencia": ""}],
  "resumo": "[IA]",
  "termometro": {"nota": 2, "media": 0.31, "positivos": 6, "neutros": 2, "negativos": 1,
                 "avaliaveis": 9, "leitura": "[IA]"},
  "visao_geral": [{"capitulo": "c1", "respondentes": 9, "total": 13,
                   "polaridades": {"positiva": 9, "negativa": 0, "mista": 0, "neutra": 0}}],
  "sem_mencao": ["Lotação"],
  "capitulos": [{
    "id": "c2", "numero": 2, "tema": "Limpeza", "tipo": "tag|categoria|outros",
    "faixa": "Padrão", "respondentes": 5, "total": 13,
    "polaridades": {}, "voz": {"avaliavel": true, "direcao": "", "nivel": "", "qualificador": ""},
    "delta": null,
    "subcapitulos": [{
      "id": "c2.s1", "numero": "2.1", "nome": "Sanitários", "respondentes": 2,
      "polaridades": {}, "subtemas": ["falta de papel higiênico"],
      "o_que_foi_dito": "[IA]",
      "critica": {"resumo": "[IA]", "falas": ["t14"]},
      "falas": ["t12", "t14"]
    }],
    "hipotese": {"texto": "[IA]", "como_validar": "[IA]", "apoia_em": ["c2.s1"]},
    "recomendacao": {"tipo": "Higiene", "driver": "Retenção", "area": "Operações", "texto": "[IA]",
                     "como_medir": "[IA]", "responde_a": ["c2.s1"]},
    "acompanhar": {"texto": "[IA]", "como_investigar": "[IA]", "porque": ["c2.s1"]}
  }],
  "trechos": {"t12": {"alias": "Áudio 013", "tempo": "00:06", "texto": "", "nota": -0.7,
                      "polaridade": "negativa", "mascarado": true}},
  "transversais": [{"texto": "[IA]", "como_validar": "[IA]", "capitulos": ["c1", "c4"]}],
  "matriz": {},
  "reclamacoes": {"abertura": "[IA]", "itens": [], "isoladas": []},
  "notas": [], "anexo": {"arquivo": ""}, "problemas": []
}
```

- **Falas por id.** A IA recebe os trechos candidatos com um id (`t12`) e devolve só os ids. O código insere o texto literal, o alias, o tempo e a nota, e aplica a máscara. A IA nunca escreve uma fala, então não há citação inventada nem texto sem máscara.
- **Armazenamento.** `project_analyses` ganha a coluna `relatorio_json` (TEXT, via `_ensure_column`). `analysis_text` continua sendo preenchido, com o markdown gerado do JSON (`relatorio_markdown`), para a base de conhecimento, o `.md` e qualquer leitor antigo. `temas_json` (o snapshot do Δ) não muda de formato. Análise sem `relatorio_json` é legada (§9).

### 5.2 Etapas da geração

Mudanças sobre `gerar_analise_geral`:

1. Taxonomia, como hoje.
2. Classificação dos áudios, como hoje (`gpt-4.1-mini`, cache em `audio_temas`).
3. **Consolidação de subtemas** (§4), nova.
4. Agregação (`agregar`), agora com os grupos, o capítulo de cada tema, as reclamações e as falas candidatas.
5. **Capítulos**: uma chamada por tema com menção, em paralelo (até 4 ao mesmo tempo), cada uma com a evidência só do tema.
6. **Síntese**: uma chamada com os capítulos prontos, que escreve o Resumo, as leituras transversais, a abertura das reclamações, a leitura do Termômetro e a leitura de cada grupo da Matriz.
7. **Verificação** (§7) e remontagem do capítulo que falhou.
8. Gravação: `save_project_analysis` com o `relatorio_json`, o markdown e o snapshot.

`escrever_relatorio` passa a devolver o JSON; `montar_relatorio` e `separar_secoes` saem de cena (o markdown vem de `relatorio_markdown`).

### 5.3 Prompts e chamadas

- **Capítulo.** O sistema traz o papel, o rigor e as regras de voz e de redação que já valem (`_ANALISE_GERAL_RIGOR`, `_ANALISE_GERAL_VOZ`), mais o esquema JSON do capítulo e um exemplo (lavanderias). A mensagem traz: o contexto do projeto, a ficha do tema (n/N, faixa, polaridades, leitura de voz já em frase), os subcapítulos com contagem e polaridade, os **trechos candidatos** com id, as **fichas de contexto** dos áudios do tema (§10) e o destino do capítulo (pode ter recomendação, só "o que acompanhar", ou nenhum dos dois).
- **Trechos candidatos.** Até 12 por tema, de respondentes diferentes. O código descarta o que tem menos de 6 palavras, uma fala só de cortesia ou de aprovação genérica, e a fala de alerta crítico (que nunca é citada). Mantém equilíbrio por polaridade: se o tema tem crítica, há pelo menos um candidato crítico. A IA escolhe até duas falas por subcapítulo, de respondentes diferentes, pela informação que trazem, e escreve uma frase do que cada uma mostra.
- **Síntese.** Recebe os capítulos, os números do código e as fichas apenas por contagem. Não reescreve capítulos.
- **Regras de redação**, no sistema: sem repetir o título nem a ficha dentro do texto; uma ideia por frase; "o que foi dito" até 45 palavras; hipótese até 40 e recomendação até 50; sem superlativo sem número; achado e hipótese não usam "deve"; ausência de menção não é achado; sem "valência", "ativação", "habitual", "pitch" nem "loudness" no texto; nenhum nome de pessoa em texto de crítica ("o funcionário", "o gerente").
- **Parâmetros.** Temperatura 0,3, como hoje (`TEMPERATURA_ANALISE_GERAL`); limite de tokens por capítulo de 3 mil e da síntese de 4 mil (`MAX_TOKENS_ANALISE_GERAL` hoje é 8 mil para o texto inteiro).
- **Saída estruturada.** `generate_analysis` e `create_analysis` ganham o parâmetro `schema`. Na OpenAI, a chamada usa a Responses API com saída em JSON Schema estrito, junto com a busca na base quando ligada. Nos demais provedores, o esquema vai no prompt, a resposta é lida e validada, e uma tentativa a mais corrige JSON inválido. `create_analysis` ganha `client` para rodar nas threads do lote, como `responder_da_classificacao` já faz com os clientes que vêm do cache do Streamlit.
- **Base de conhecimento.** O seletor continua valendo para as chamadas de capítulo e de síntese, com `build_kb_filter(project_id, incluir_analises=False)` como hoje.

## 6. Máscara de nomes e dados pessoais

Função `mascarar_fala(texto, preservar)` em `utils/prosodia_mascara.py`:
- **Pessoas:** reconhecimento de entidades do `pt_core_news_sm` (o spaCy já está no `requirements.txt`; hoje o modelo é carregado sem o `ner`, em `prosodia_charts.py`) mais a heurística de nome próprio capitalizado depois de "o/a/seu/dona/professor(a)/gerente/atendente". O nome vira "[funcionário]" quando a pessoa é da unidade, e "[pessoa]" nos demais casos.
- **Dados financeiros e de contato:** valores em reais, números de cartão, CPF, telefone, e-mail e nomes de banco, que viram "[valor]", "[dado pessoal]" e "[banco]".
- **`preservar`:** o que nunca se mascara (nome do projeto e da marca, nomes das unidades, rótulos da taxonomia, nomes de cidade e de bairro), para "Smart Fit" e "Caxias Shopping" não virarem "[pessoa]".
- **Falha segura:** com dúvida entre pessoa e marca, mascara. A máscara roda sobre o texto literal da fala depois de escolhida, nunca sobre o texto da IA; o texto da IA é conferido pela verificação (§7).
- **Onde:** toda fala crítica (polaridade negativa ou o trecho crítico de um misto) e toda fala de qualquer tabela do Anexo. A fala de elogio no PDF principal mantém o nome da pessoa, mas perde o dado financeiro.

## 7. Verificação

`verificar_referencias` é reescrita sobre o JSON, no nível do capítulo. Falha em um capítulo manda uma segunda chamada **só daquele capítulo**, com a lista de problemas na mensagem. Se a segunda também falha, o relatório sai com o capítulo como está e o problema em `problemas`, mostrado na página do app e nunca no PDF.

| Regra | Origem |
|---|---|
| Todo número que o texto cita bate com a ficha do capítulo (n/N, polaridades, Δ). | `_fracoes_do_tema` |
| Todo Áudio NN citado existe e pertence à evidência do capítulo. | `_AUDIO_CITADO` |
| `responde_a`, `apoia_em` e `porque` apontam para subcapítulos do mesmo capítulo; `capitulos` das leituras transversais, para pelo menos dois capítulos. | nova |
| Recomendação só em tema em padrão fora da leitura exploratória; "O que acompanhar" só em padrão ou sinal, e "e investigar" só na exploratória; menção isolada sem nenhum dos dois. | `pode_recomendar` |
| Hipótese não se apoia só em menção isolada. | v2 |
| Termo técnico de voz fora do Anexo vira aviso. | `_conferir_linha` |
| O texto só fala da voz do tema quando a leitura é avaliável, e nunca contra a calculada (a grade mostra a leitura, e o texto a complementa). Hoje `_sinal_diferente` compara a frase "Sinal emocional:" do achado com a da tabela; como o capítulo deixa de repetir essa frase, a regra passa a comparar a direção citada com a de `sinal_emocional`. | `_sinal_diferente` |
| Frase que liga um tema com 0 respondentes a "não é relevante" ou "não incomoda" vira aviso. | Unidades §5.3 |
| Nome de pessoa em texto de crítica (reconhecimento de entidades) vira aviso. | nova |
| Enchimento: título do capítulo repetido no texto, "sem destaque em relação aos demais relatos" e as frases de abertura fixas de uma lista curta vão a aviso. | nova |
| Fala citada na síntese, no resumo ou na abertura: só por id existente. | nova |

## 8. O PDF e a página

### 8.1 Tecnologia

HTML + CSS com WeasyPrint. O JSON entra em templates Jinja, os gráficos são SVG inline, e o mesmo HTML serve ao PDF e à página do app. O `pdf_report` fica para Jornada e Sensorial.

| Arquivo | Papel |
|---|---|
| `utils/relatorio_html.py` | Renderiza o JSON em HTML (`corpo_html`, `documento_html` e `anexo_html`). |
| `utils/relatorio_graficos.py` | Gráficos em SVG: barras empilhadas, medidor do Termômetro, grade de voz, mini-gráfico de polaridade. |
| `utils/relatorio_pdf.py` | HTML em PDF, com o CSS de impressão e as fontes. |
| `templates/relatorio/*.html` | Capa, índice, capítulo, reclamações, anexo e a página legada. |
| `static/relatorio.css` | O CSS: tokens, componentes e `@page`. |
| `static/fonts/` | A fonte do texto em arquivos estáticos (Regular, Medium e Bold). |

### 8.2 Páginas e tipografia

- A4, retrato, margens de 18 mm. A capa não leva cabeçalho nem rodapé.
- **Cabeçalho corrido** com o capítulo atual (`string-set`) e rodapé "página X de Y" (`counter(page)`, `counter(pages)`).
- **Índice** com número de página (`target-counter`) e links. **Marcadores** do PDF (`bookmark-level`) em capítulos e subcapítulos.
- Cabeçalho de tabela repetido a cada página (`thead`), `break-inside: avoid` nos cartões e controle de viúvas e órfãs (`orphans`, `widows`). Hifenização em português (`lang="pt-BR"`, `hyphens: auto`).
- **Fonte.** A `Rubik.ttf` do repositório (OFL) tem 360 KB e provavelmente é a variável; o WeasyPrint usa arquivos estáticos, então a Fase 3 gera ou baixa Regular, Medium e Bold e os grava em `static/fonts/` com a licença ao lado.

### 8.3 Cores e componentes

Os tokens do `pdf_report` ficam: tinta `#0B0B0B`, secundário `#52514E`, apagado `#898781`, linha `#E1E0D9` e o violeta NENC `#5D5294`. As cores semânticas, a refinar com o desenho: elogio verde, crítica vermelho, mista âmbar, neutra cinza. **Toda cor leva rótulo ou ícone**, e o contraste do texto é de pelo menos 4,5:1; o PDF precisa continuar legível em escala de cinza.

| Componente | Onde |
|---|---|
| Capa com medidor do Termômetro | Página 1. |
| Faixa de capítulo (número grande, nome, chip de faixa) | Início de cada capítulo. |
| Barra empilhada de polaridade | Visão geral e cabeçalho do capítulo. |
| Grade de voz (valência × ativação) | Capítulo. |
| Cartão de recomendação e de hipótese | Capítulo. |
| Cartão de reclamação e tabela compacta | Reclamações. |
| Caixa de alerta crítico e caixa "Como ler" | Início do relatório. |
| Citação (fala) com Áudio, tempo e nota | Subcapítulo. |

### 8.4 A página do app

A página mostra o `corpo_html` com `st.html` (o `requirements.txt` pede o Streamlit 1.36 ou mais), sem paginação e sem a capa. O CSS vem com o HTML. Se o `st.html` não aceitar o `<style>` ou o SVG como o desenho pede, a página usa `components.html` num iframe (§12). A verificação (`problemas`) e a classificação com falha aparecem acima do relatório, como hoje.

### 8.5 Dependências

- `weasyprint`, `jinja2`, `markdown-it-py` e `jsonschema` no `requirements.txt` (os três últimos já vêm por transitividade, mas passam a ser declarados). O `markdown-it-py` serve ao template legado (com a extensão de tabelas) e ao `.md` da base de conhecimento.
- **Docker.** O `Dockerfile` instala os pacotes de sistema do WeasyPrint (`libpango-1.0-0`, `libpangoft2-1.0-0` e `libharfbuzz-subset0`, conforme a documentação dele) e a imagem cresce por algumas dezenas de MB. O deploy segue exigindo `--build` (`docs/OPERATIONS.md`).
- **Windows de desenvolvimento.** O WeasyPrint precisa do Pango do GTK; o caminho recomendado é o runtime GTK3 do MSYS2 com `WEASYPRINT_DLL_DIRECTORIES`, ou rodar os testes de PDF no Docker. Entra uma seção em `OPERATIONS.md`.
- **Testes.** Os de HTML rodam em qualquer máquina. Os de PDF usam um marcador `requer_weasyprint` e são pulados quando o Pango não está instalado.

## 9. Anexo técnico, zip e análises antigas

**Anexo técnico.** Um PDF por projeto, gerado uma vez por lote e incluído no zip, com o nome `Anexo_Tecnico_<Projeto>_<data>.pdf` (a mesma regra de `nome_do_arquivo`). Leva o que hoje é o `apendice_markdown`: a tabela de taxonomia completa com todas as tags, a voz por tema, os candidatos a tag nova, as médias acústicas por áudio, a distribuição de emoções, os momentos de maior ativação, o ranking de palavras, o sentimento do texto, o índice combinado, os números da Matriz, a conta do Termômetro, as divergências voz × texto, a legenda "Áudio NN → sessão" quando ligada e as limitações completas. Os áudios saem do projeto inteiro, com a coluna de unidade, porque o alias "Áudio NN" é do projeto (`aliases`). Toda fala dele passa pela máscara (§6). Cada relatório cita o anexo nas notas metodológicas. Fora do lote, a página ganha um botão para baixar o anexo do projeto.

**Zip.** `planejar_por_unidade` não muda. O zip ganha o anexo como mais um arquivo.

**Como ficou na Fase 3 (25/11/2026).** O caminho v2 é o único em produção até a Fase 2, então o anexo de hoje sai do "Apêndice técnico" do texto salvo (`relatorio_markdown.separar_anexo`):

- **Unidade.** As tabelas do apêndice saem de `prosodia_signals` e não mudam nesta fase. A unidade entra numa tabela "Áudios e unidades" (Áudio, Unidade, QR code, Chegada, sem a sessão), antes das limitações (`prosodia_geracao.anexo_do_texto`). A coluna de unidade em cada tabela fica para quando a Fase 1 gerar o anexo do JSON.
- **Escopo.** Na página, o anexo é o da análise mais recente, com os áudios do recorte dela, e o nome leva a unidade quando o recorte é de uma só (`nome_do_anexo_da_analise`). No lote, é o da análise "Todas as unidades", e o nome vai sem unidade (`nome_do_anexo(projeto, "", dia)`).
- **Máscara.** `prosodia_mascara.py` é da Fase 1: até lá, as falas do anexo saem como saíam no apêndice do PDF v2.
- **Página.** A v2 e as antigas seguem na tela pelo `st.markdown`, com o texto inteiro. O PDF é montado uma vez por análise (`st.cache_data`) e, sem o Pango, a página mostra o motivo no lugar do botão; o lote não começa sem ele.
- **Papel.** O anexo é A4 deitado, para as tabelas de 8 ou mais colunas; o corpo da v2 segue em A4 retrato, com capa, índice das seções `###` e as referências da base no fim.

**Análises v2 e antigas.** Valem para toda análise sem `relatorio_json`: as geradas antes desta spec e as de projetos sem tags (§2, itens 25 e 26). O texto markdown é convertido em HTML (`markdown-it-py`, com tabelas) e ganha a capa, a tipografia, o cabeçalho e o rodapé novos, sem capítulos. O título "Apêndice técnico" divide o texto: o que vem antes é o corpo, e o que vem depois vira o Anexo técnico do projeto. A conversão tira a marcação que vaza (`*Apoia-se em:*`, `_Faixas…_`). O gerador fpdf2 de Prosódia (`_build_project_analysis_pdf`) sai do código. O Δ lê o snapshot gravado, que não muda de formato.

## 10. Análise individual por áudio

A análise individual deixa de ser um relatório e vira um **documento de apoio de contexto** para a Geral.

**A ficha** (markdown com títulos fixos, gravado em `analyses.analysis_text`, uma chamada):

| Seção | Conteúdo |
|---|---|
| Situação | Quem fala e em que circunstância (unidade, momento, tipo de relato), em até duas frases. |
| Temas tocados | Um item por tema, com polaridade e uma paráfrase de até 20 palavras. Em Pesquisa de Opinião, a lista pode ser montada em código a partir da classificação já gravada em `audio_temas`, sem pedir à IA. |
| Gatilho e causa | O que o respondente diz ter causado o que relata. |
| Expectativa ou pedido | O que ele quer que aconteça. |
| Vínculo e risco | O que ele diz sobre voltar, indicar, cancelar ou trocar, só quando disse. |
| O que a fala explica | O que o texto esclarece e que os números não mostram. |
| Lacunas | O que não foi dito e limita a leitura. |

Cerca de 300 tokens. **Sem leitura da voz**: a voz é calculada em código e a ficha não a interpreta, o que também dispensa `sem_leitura_da_voz`. A ficha segue com o rótulo "interpretação prévia: contexto, não fonte de número ou achado", e a Geral continua sem tirar número nem achado dela.

**Como a Geral a usa.** Cada chamada de capítulo recebe as fichas dos áudios do tema, inteiras, com a sessão trocada pelo alias, como `sumarios_individuais` faz hoje. A síntese não recebe fichas. Análise antiga, sem ficha, cai no `sumario_executivo`.

**Prompt.** O de Pesquisa de Opinião (`PROMPT_PESQUISA_OPINIAO`) e o de Entrevista (`PROMPT_ENTREVISTA`) ganham a estrutura da ficha no lugar da "Estrutura de Resposta (flexível)". Os modos Estatística e Estratégica do áudio ficam como estão e deixam de alimentar a Geral.

**Tela e PDF.** A página do áudio mostra a ficha e o PDF individual usa o mesmo CSS (um template curto). Não há capítulos nem seção de reclamações na análise individual.

## 11. O que pedir à Smart Fit

- Nome de cada unidade no cadastro, ao lado do código: hoje alguns áudios saem só como "SBRPICLOU01", e o rótulo "nome (código)" das reclamações precisa dos dois.
- Confirmar que o PDF pode manter o nome do funcionário nos elogios, e que nas críticas ele sai (§6).
- Dizer onde cabem preço e valor percebido, como em [Unidades §11](SPEC_UNIDADES_E_TAGS.md).
- Confirmar a nomenclatura "Avaliação geral e outros assuntos" para o conjunto fora das 16 tags.

## 12. Decisões em aberto e a validar

| Ponto | Hipótese de trabalho | Quando fechar |
|---|---|---|
| Projetos sem tags (§4) | **Decidido em 25/11/2026:** seguem na v2; só a taxonomia com tag aberta gera capítulos. Fecha a dúvida sobre a faixa do capítulo por Categoria, que fica só para a taxonomia mista. | Fechado. |
| Ficha individual no caminho v2 (§10) | A ficha não tem "Sumário Executivo", então `sumario_executivo` não a lê. Na v2, a Geral recebe a ficha inteira no lugar do Sumário, com o mesmo limite de tamanho, e análise antiga segue no Sumário. | Fase 4. |
| Interruptor por projeto (§2, item 27) | Configuração do projeto, lida em `gerar_analise_geral`; sem ele ligado, a geração segue o caminho v2. Onde guardar (coluna em `projects` ou configuração existente) a decidir olhando `prosodia_db.py`. | Fase 2. |
| WeasyPrint no Python 3.14 e no Windows | **Fechado na Fase 0 (25/11/2026):** WeasyPrint 70.0 com o Pango do MSYS2 passou no spike (fonte própria, `@page`, `string-set`, `target-counter`, `bookmark-level`). | Fechado. |
| Mista sem trecho crítico (§3.6) | Não entra no ranking; o JSON registra quantas houve. | Fase 5, olhando quantas se perdem. |
| Peso da ordem das reclamações (§3.6) | Faixa, respondentes, intensidade. | Fase 5, com a leitura humana. |
| Mínimo de 6 palavras na fala candidata (§5.3) | Ponto de partida. | Fase 5. |
| `gpt-4.1` seguir o JSON estrito, as regras de evidência e escolher falas bem | Decisão do Pedro; validar no lote da Smart Fit. O seletor segue aberto para trocar por modelo Claude. | Fase 2. |
| Reconhecimento de nomes do `pt_core_news_sm` | A confirmar contra as falas reais; se for fraco, passar ao modelo `md`. Falha segura mascara na dúvida. | Fase 2. |
| `st.html` com `<style>` e SVG (§8.4) | Se não aceitar, `components.html` num iframe. A página só mostra o HTML novo quando houver `relatorio_json`, que a Fase 2 grava; até lá a v2 e as antigas seguem no `st.markdown` (§9). | Fase 2, no navegador, com a primeira análise por capítulos. |
| Branch `feat/resumo-por-tema` (1fe96a1, ainda não mesclada) | **Decidido em 25/11/2026:** os capítulos a substituem. O "Resumo por tema" some, a branch não é mesclada e não entra na Fase 1. A conta do Termômetro por tema (`termometro_do_tema`) não é reaproveitada, porque o Termômetro do tema não está nos capítulos. | Fechado. |
| Anexo fora do lote (§9) | **Fechado na Fase 3:** botão "Download Anexo técnico (.pdf)" na página, com o anexo da análise mais recente (o recorte dela). | Fechado. |

## 13. Mudanças por arquivo

| Arquivo | Mudança |
|---|---|
| `utils/prosodia_temas.py` | Consolidação de subtemas (`consolidar_subtemas`, com validação), `contar_detalhes` por projeto para a entrada. |
| `utils/prosodia_db.py` | Tabela `subtema_grupos`, coluna `project_analyses.relatorio_json`, funções de leitura e gravação, e `save_project_analysis` com o JSON. |
| `utils/prosodia_relatorio.py` | `agregar` com grupos, capítulos, reclamações e falas candidatas; JSON do relatório; `relatorio_markdown`; `escrever_relatorio` em etapas (capítulos e síntese) e `verificar_capitulo` e `verificar_sintese` novas. `montar_relatorio`, `separar_secoes` e `verificar_referencias` **ficam**, porque o caminho v2 dos projetos sem tags os usa. |
| `utils/prosodia_prompts.py` | Prompt de capítulo e de síntese com os esquemas, exemplo e regras de redação; prompt da consolidação; prompts da ficha individual. |
| `utils/prosodia_geracao.py` | Etapas 3, 5, 6 e 7 da §5.2; paralelismo; anexo do lote; `nome_do_anexo`; escolha entre o caminho por capítulos e o v2 pelo `tem_tags_abertas` e pelo interruptor do projeto. |
| `utils/prosodia_mascara.py` (novo) | `mascarar_fala` (§6). |
| `utils/ai_provider.py` | `schema` em `generate_analysis` e `create_analysis`, e `client` em `create_analysis`. |
| `utils/relatorio_html.py`, `utils/relatorio_graficos.py`, `utils/relatorio_pdf.py`, `templates/relatorio/`, `static/relatorio.css`, `static/fonts/` (novos) | O visual (§8). |
| `modules/prosodia/analise_geral.py` | A página mostra o HTML; remove `_build_project_analysis_pdf`; botões de PDF e do anexo; o lote junta o anexo ao zip. |
| `modules/prosodia/audio.py`, `utils/prosodia_audio.py` | Geração da ficha (`audio.py` chama a IA e grava com `save_analysis`), `build_analysis_markdown` com os títulos fixos e o PDF individual com o template novo (`_analysis_pdf` deixa o `pdf_report`). `modules/prosodia/audio_analise.py` é só um redirecionamento e não muda. |
| `requirements.txt`, `Dockerfile`, `docs/OPERATIONS.md` | Dependências, pacotes do sistema e a seção do Windows. |
| `tests/` | Testes da §14. |

## 14. Testes (pytest)

- **Consolidação:** cada subtema em exatamente um grupo, subtema inventado descartado, sobra em "Outros assuntos do tema", tag com até 3 subtemas sem IA, cache pela chave, e falha da IA caindo nos subtemas crus.
- **Agregação por capítulo:** a contagem de respondentes do grupo é a união, a da tag não muda, polaridades unidas, taxonomia de subtemas fixos agrupada por Categoria, e a faixa do capítulo.
- **Reclamações:** negativa isolada entra, mista entra só com trecho de nota até −0,2, sugestão e crítica silenciosa ficam fora, a ordem por faixa, respondentes e intensidade, a etiqueta de voz só com 5 respondentes com voz, e o rótulo de unidade só em recorte de várias unidades.
- **Máscara:** nome de funcionário, valor, banco, telefone e CPF; "Smart Fit", unidades e rótulos preservados; o elogio mantém o nome; a máscara na tabela do Anexo.
- **Falas:** a IA devolve só ids, o id inexistente é descartado, os candidatos têm pelo menos 6 palavras, e a fala de alerta nunca é candidata.
- **Verificação:** cada linha da §7, o reenvio de um capítulo só, e o limite de uma segunda chamada.
- **JSON e markdown:** o esquema valida, `relatorio_markdown` reproduz o texto (títulos, números e falas), e o snapshot do Δ não muda.
- **Análise antiga:** `relatorio_json` ausente abre no template legado.
- **HTML:** cada componente da §8.3, o índice com os links, a ordem da §3.1, o capítulo por faixa e a leitura exploratória sem recomendação.
- **PDF** (com `requer_weasyprint`): gera sem erro, tem marcadores e número de páginas, e a tabela não passa do rodapé.
- **Ficha individual:** seções fixas, sem leitura de voz, sessão trocada pelo alias, e Geral que recebe só as fichas dos áudios do tema.
- **Provedor:** `schema` na OpenAI e nos demais, e JSON inválido corrigido na segunda tentativa.

## 15. Fases

A ordem de execução é a do item 28 da §2: Fase 0, Fase 3, Fases 1 e 2, Fase 4 e Fase 5. A numeração abaixo é a de dependência, não a de calendário.

0. **Preparação.** Worktree a partir de `origin/main`, esta spec no repositório, Pango pelo MSYS2 e o spike do WeasyPrint (§12), e as dependências no `requirements.txt`.
1. **Agregação por capítulo.** Consolidação de subtemas, tabela `subtema_grupos`, `agregar` com capítulos, reclamações e falas candidatas, máscara, JSON e `relatorio_markdown`. Sem IA nova de texto. A branch `feat/resumo-por-tema` não entra: os capítulos a substituem (§12).
2. **Texto por capítulo.** Prompts, `schema` no `ai_provider`, as chamadas em paralelo, a síntese e a verificação nova. A geração passa a gravar o JSON, atrás do interruptor por projeto (§2, item 27), desligado até a Fase 3 estar em produção.
3. **O visual.** HTML, gráficos, CSS, PDF, a página do app, o anexo, o zip e o template legado, que também atende o texto v2. **Vem primeiro** (§2, item 28), a partir de fixtures fictícias montadas do formato dos dois PDFs de 24/11 (sem dado da Smart Fit no repositório), porque é a parte que mais pede iteração visual. O esquema JSON da §5.1 é o contrato: se a Fase 1 pedir mudança nele, as fixtures acompanham.
4. **Ficha individual.** O prompt e a página do áudio, e a Geral passando a recebê-la.
5. **Validação com a Smart Fit.** Cópia de produção (o script de geração fora da página, sem desligar o `_audit`), a rede e Duque de Caxias 2, e conferir:
   1. cada ponto da §1 tem resposta visível;
   2. nenhum capítulo cita valência, ativação, habitual, pitch nem loudness, e nenhuma frase se repete entre capítulos;
   3. a fala de elogio traz informação, e a crítica traz pelo menos uma fala;
   4. nenhuma fala crítica traz nome de pessoa, dado financeiro ou de contato;
   5. as contagens do capítulo batem com as da tabela do Anexo;
   6. o PDF não tem tabela sobre o rodapé nem marcação vazando, e o índice leva ao capítulo certo;
   7. os pontos da §12 marcados para esta fase;
   8. leitura humana: quem recebe o capítulo de Limpeza sabe o que fazer, e a seção de reclamações diz o que atacar primeiro.

A Fase 1 não depende de nenhuma outra. As Fases 2 e 4 dependem da 1. A Fase 3 depende só do esquema JSON da §5.1 e do spike da Fase 0.

## 16. Fora do escopo

- O PDF de Jornada de Compra e do Teste Sensorial (seguem no `pdf_report`).
- Capítulos por tema em projetos sem tags: seguem no relatório v2 (§2, item 25).
- Trocar o modelo da classificação por áudio, ou reclassificar.
- Distribuição por unidade nos capítulos, a coluna "Unidades" e o "concentrada em": saem do corpo e não voltam nesta spec. A tabela tema × unidade segue fora do escopo, como em [Unidades §10](SPEC_UNIDADES_E_TAGS.md).
- Sugestões e pedidos de melhoria como categoria própria, e a crítica silenciosa no ranking de reclamações. Ambos dependem de uma classificação por tipo de menção, o que reclassifica todos os áudios.
- Leitura de voz por fala nos itens de reclamação.
- Alerta crítico em tempo real, como em [Unidades §10](SPEC_UNIDADES_E_TAGS.md).
- PPTX e gráficos interativos.
- O Termômetro do tema (da branch `feat/resumo-por-tema`).
