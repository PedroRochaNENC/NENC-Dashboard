# Análise Geral do NencBoost — especificação v2

- **Status:** pronta para revisão final; os pontos em aberto foram resolvidos em 15/10/2026 (§13)
- **Data:** 14/10/2026, atualizada em 15/10/2026
- **Responsável:** Pedro Rocha
- **Escopo:** só a Análise Geral do projeto (`modules/prosodia/analise_geral.py`). Prompts e páginas das análises individuais não mudam.
- **Origem:** feedback da cliente sobre os prompts, relatório da Smart Fit de 14/10/2026 (gpt-4.1, 52 áudios) e sessões de decisões de 14 e 15/10/2026.

## 1. Por que mudar

O que o relatório da Smart Fit de 14/10 mostra:

| Problema | Onde aparece |
|---|---|
| Números de outra amostra | A etapa estratégica traz 74,5% / 17,6% / 3,3% / 4,6% de emoções, idênticos até a casa decimal aos do relatório de 30/09 que a base de conhecimento devolveu como referência. A entrada dessa etapa só tem a distribuição por áudio, não a do projeto. |
| Números que não batem entre si | "Tristeza predominante em 25% dos áudios" e, logo abaixo, "Tristeza: 9/52" (17%). |
| Matriz que contraria a própria regra | "Vínculo forte (alta ativação + valência positiva)" lista `wa_+5521998548459_39`, com arousal de −0,02. |
| ID que não existe | `wa_+552192689455_62` no lugar de `wa_+556192689455_62`. |
| Tema sem frequência | "aparelhos, lotação, limpeza" sem dizer quantos dos 52 citaram cada um. Sem isso não há taxonomia nem priorização. |
| Generalização de 2 ou 3 casos | "Tópicos de maior ativação: 'Sempre' (3 ocorrências), 'Experiência boa' (2)". |
| Leitura que não se aplica | Comparação "Locutor A × B" num projeto de opinião, que tem um locutor por áudio. |
| Recomendação genérica | "Valorizar e evidenciar a equipe". Nos próximos passos, "implementar análise prosódica em tempo real", que é tarefa da Nenc, não da Smart Fit. |
| Termômetro sem critério | "+1" sem dizer como foi calculado. |
| Telefone no corpo do relatório | Todo áudio é citado pelo `session_id`, que carrega o telefone do aluno. |

Duas causas no código:

- A etapa estratégica não recebe as análises individuais nem as transcrições (`analise_geral.py:1467`). Ela só vê o texto da estatística, as médias, os 15 momentos de pico e o ranking de palavras, e por isso não tem como contar quantos citaram "lotação".
- Cada Análise Geral salva vai para a base de conhecimento com `escopo="analise"` (`analise_geral.py:220`), e o filtro da busca aceita qualquer documento do projeto (`kb_attributes.py:60`). A regeneração seguinte recebe o relatório anterior como evidência.

## 2. Objetivos da cliente e como cada um é atendido

| Objetivo | Mecanismo |
|---|---|
| Achados mais acionáveis para CX e negócio | Cada recomendação traz classificação, driver, área dona, rastreio até o achado e a hipótese, e uma métrica (§8). Exemplo de saída num setor fictício (§10). |
| Taxonomia de temas construída junto | Campo de taxonomia por projeto, editado pela equipe com a cliente. Temas fora dele aparecem como emergentes e viram proposta para a versão seguinte (§5). |
| Diferenciar achados, hipóteses e recomendações | Três blocos com IDs (A1, H1, R1), formato de linha fixo e verificação automática das referências (§8). |
| Cautela com amostra pequena | Faixas por número de respondentes, calculadas em código. Só "sinal" e "padrão" geram recomendação, e o relatório sai como exploratório abaixo de 20 respondentes (§7). |
| Voz como complemento dos temas | A voz entra como qualificador de cada tema, calculado em código. As métricas acústicas soltas vão para o apêndice (§7). |

## 3. Decisões tomadas

| # | Tema | Decisão |
|---|---|---|
| 1 | Escopo | Só a Análise Geral. As análises individuais continuam como estão. |
| 2 | Etapas | A etapa estatística deixa de ser texto de IA e vira apêndice técnico gerado pelo código. |
| 3 | Taxonomia | Híbrida: cadastrada por projeto, e o que não couber vai para "Temas emergentes". |
| 4 | Onde fica | Campo próprio no projeto, uma linha por `Categoria > Subtema`. |
| 5 | Contagem | Classificação por áudio em JSON, guardada no banco, com a contagem feita em código. |
| 6 | Base de conhecimento | Não traz mais análises anteriores. A comparação com a onda anterior vem do banco. |
| 7 | Onda anterior | Snapshot salvo com cada análise (tabela de temas e lista de áudios). A coluna Δ compara os respondentes novos com os da análise anterior. |
| 8 | Amostra | 1–2 respondentes: menção isolada. 3–4: sinal. 5 ou mais e pelo menos 10%: padrão. Voz por tema a partir de 5 trechos. Abaixo de 20 respondentes, relatório exploratório. |
| 9 | Voz | Qualificador em cada tema. Números prosódicos no apêndice. |
| 10 | Seções extras | Ficam Resumo Executivo, Matriz de Destaques e Termômetro. O Δ vira coluna da taxonomia e as limitações vão para o apêndice. |
| 11 | Termômetro | Sentimento médio do texto × 5, calculado em código. A IA só explica, e a voz entra pelos qualificadores dos temas. |
| 12 | Matriz | Montada pelo código, com corte relativo ao projeto. |
| 13 | Recomendação | Classificação, driver, área dona, ação, "Responde a" e "Como medir". |
| 14 | Exemplo (few-shot) | Setor fictício, marcado como "só formato". |
| 15 | Tipos e modos | Entrevista e Opinião com o mesmo esqueleto, num modo só. Some o seletor Rápida / duas etapas. |
| 16 | Hipótese | Base ("Apoia-se em") e "Como validar", sem nível de confiança. |
| 17 | Sem taxonomia | Roda como emergente: a IA monta uma árvore provisória antes de classificar. |
| 18 | Validação | Smart Fit e Demonstração ABIHPEC (Entrevista), lado a lado com os relatórios atuais, mais testes. |
| 19 | Análises individuais | Entra só o Sumário Executivo de cada uma, rotulado como interpretação prévia, que nunca é fonte de achado. |

## 4. Pipeline

```
Gerar Análise Geral
 1. Taxonomia vazia? A IA propõe uma árvore provisória (1 chamada) e o código salva no projeto como "provisória".
 2. Classificação por áudio, só para os áudios sem classificação na versão atual da taxonomia
    (1 chamada curta por áudio, modelo barato), gravada em audio_temas.
 3. Agregação em código sobre os áudios do recorte: contagem e faixas, voz por tema, Matriz,
    Termômetro, Δ, aliases e apêndice.
 4. Uma chamada de IA recebe as tabelas prontas, os trechos de evidência, os Sumários Executivos das
    análises individuais e a base de conhecimento, e escreve só o texto: Resumo, Achados, Hipóteses,
    Recomendações e as leituras da Matriz e do Termômetro.
 5. O código monta o relatório na ordem fixa, insere as tabelas e verifica as referências.
 6. Salva a análise e o snapshot dos temas (project_analyses.temas_json).
```

Quem escreve o quê:

| Parte do relatório | Código | IA |
|---|---|---|
| Cabeçalho (N, recorte, status da taxonomia, aviso exploratório) | ✔ | |
| Resumo Executivo | | ✔ |
| Tabela de taxonomia, faixas, voz por tema e Δ | ✔ | |
| Achados, Hipóteses e Recomendações | | ✔ |
| Tabelas da Matriz | ✔ | |
| Perfil e ação de cada grupo da Matriz | | ✔ |
| Número do Termômetro e a conta | ✔ | |
| Leitura do Termômetro | | ✔ |
| Apêndice técnico e limitações | ✔ | |
| Verificação das referências | ✔ | |

## 5. Taxonomia

- Colunas novas: `projects.taxonomia` (texto) e `projects.taxonomia_status` (`provisoria` ou `validada`).
- Formato: uma linha por subtema, `Categoria > Subtema`, com uma definição opcional depois de dois-pontos. Exemplo: `Equipamentos > Máquina fora de serviço: máquina parada, quebrada ou com aviso de manutenção`. A definição vai para o classificador e reduz a ambiguidade entre subtemas vizinhos.
- Na Preparação (`preparacao.py`, depois de "Entidades prioritárias") entram o campo de texto, a caixa "Taxonomia validada com a cliente" e, abaixo, a lista dos temas emergentes do projeto com contagem, para copiar para o campo.
- Com a taxonomia vazia, a geração faz uma chamada à IA com as falas dos respondentes (com limite de palavras por áudio nas entrevistas longas). A IA propõe de 3 a 6 categorias e de 8 a 15 subtemas, com definição. O código salva no campo com status `provisoria` e segue, e o cabeçalho do relatório avisa "Taxonomia provisória, gerada pela IA — a validar com a cliente".
- A versão é o hash do texto normalizado. Se o texto muda, a versão muda e os áudios são reclassificados. A tela avisa quantos antes de gerar.

## 6. Classificação por áudio

- Segue o mesmo padrão da cobertura de perguntas (`_coverage_ai_batch` em `prosodia_quality.py:580` e `coverage_client` em `ai_provider.py:299`): funciona com OpenAI, Groq e Anthropic, e na OpenAI usa o modelo barato (`gpt-4.1-mini`).
- Entrada: a taxonomia numerada com as definições, os rótulos emergentes já usados no projeto (para repetir o mesmo nome) e a transcrição numerada por segmento, no formato `[12] (00:14) Locutor: texto`.
- Saída em JSON:

```json
{
  "locutor_respondente": "Entrevistado",
  "temas": [
    {"subtema": "Equipamentos > Máquina fora de serviço", "polaridade": "negativa",
     "segmentos": [3, 4], "evidencia": "paráfrase curta"}
  ],
  "emergentes": [
    {"rotulo": "Estacionamento", "categoria_sugerida": "Ambiente", "polaridade": "negativa",
     "segmentos": [7], "evidencia": "paráfrase curta"}
  ]
}
```

- A polaridade (`positiva`, `negativa`, `mista` ou `neutra`) é a do tema naquele áudio. Cada subtema aparece no máximo uma vez por áudio.
- O código valida a saída:
  - subtema fora da lista vira emergente;
  - segmento inexistente é descartado;
  - em entrevista, ficam só os segmentos do locutor respondente, porque pergunta do entrevistador não é menção.
- Tabela nova `audio_temas` (organization_id, audio_id, versão da taxonomia, modelo, JSON, data), no padrão multi-organização das demais. Áudio já classificado na versão atual não é reclassificado, então regenerar sem áudio novo devolve as mesmas tabelas.
- Áudios sem `segmento_idx` (os antigos e os de upload manual, `prosodia_loader.py:204`) usam a linha da transcrição como trecho, e a voz é casada pelo tempo. Sem tempo, o tema entra na contagem mas não na leitura de voz.

## 7. Agregação em código

**Faixas.** A unidade de contagem é o respondente (ver §14).

| Respondentes no subtema | Faixa | Uso no relatório |
|---|---|---|
| 1–2 | Menção isolada | Citada numa linha final, sem ID, sem hipótese nem recomendação própria. |
| 3–4, ou 5 ou mais com menos de 10% de N | Sinal | Achado com ID. Pode sustentar hipótese e recomendação, sempre chamado de sinal. |
| 5 ou mais e pelo menos 10% de N | Padrão | Achado com ID. |

Com menos de 20 respondentes no recorte, o cabeçalho diz "Leitura exploratória" e o prompt proíbe generalizar para o público.

**Voz por tema.** É calculada sobre os trechos classificados no subtema que têm dado de voz:

- **Valência:** média da metade de voz do índice combinado, que é o z da valência em relação ao projeto, dividido por 2 e limitado a ±1 (`prosodia_signals.py:567`). A partir de +0,15 é "acima do habitual", até −0,15 é "abaixo", e entre os dois é "no habitual".
- **Ativação:** z de `dim_arousal` em relação ao projeto. A partir de +0,3 é "alta", até −0,3 é "baixa", e entre os dois é "média".
- **Qualificador:** depende da polaridade predominante do texto no tema.

| Texto do tema | Voz acima | Voz no habitual | Voz abaixo |
|---|---|---|---|
| Positivo | reforça | atenua | contradiz |
| Negativo | contradiz | atenua | reforça |
| Misto ou neutro | só a direção da voz | só a direção da voz | só a direção da voz |

Com menos de 5 trechos com voz, a leitura é "não avaliável (n trechos)". Os limiares ±0,15 e ±0,3 são iniciais e serão calibrados na validação com a Smart Fit.

**Matriz de Destaques.** É montada por áudio. Os terços são calculados sobre todos os áudios do projeto (a mesma régua do índice combinado), e a tabela lista só os do recorte.

- **Vínculo forte:** índice combinado no terço superior e texto acima de +0,2.
- **Risco de abandono silencioso:** texto abaixo de −0,2 e ativação média no terço inferior.
- Cada linha traz o alias do áudio, texto, voz, índice ou ativação, e os temas do áudio. Abaixo de cada grupo vêm os temas mais citados nele (n/total).
- Com menos de 10 áudios com índice, a Matriz sai como "não avaliável".

**Termômetro.** Sentimento médio do texto × 5, arredondado para inteiro, de −5 a +5. O relatório o declara como leitura do texto; a voz entra pelos qualificadores dos temas.

- A média dá o mesmo peso a cada respondente. Primeiro vem a média de cada um, ponderada pela duração dos trechos (`sentimento_por_grupo`, `prosodia_signals.py:256`), depois a média entre respondentes. Assim uma entrevista longa não domina o número.
- A conta fica à mostra: a média, a multiplicação, o arredondamento e quantos respondentes ficam positivos, neutros e negativos (corte de ±0,2).
- Se nenhum áudio do recorte tem nota de texto (áudios antigos, anteriores ao sentimento do texto), o Termômetro sai como "não avaliável".

**Δ vs. análise anterior.** É a coluna "Anteriores → novos" da tabela de taxonomia.

- Cada análise salva um snapshot com a tabela de temas e a lista de áudios que cobriu (`project_analyses.temas_json`).
- A análise anterior é a última com o mesmo recorte de QR code e origem. "Anteriores" são os respondentes dos áudios que ela cobriu, e "novos" são os respondentes dos áudios que chegaram depois. Quem mandou áudio nos dois momentos conta nos dois grupos, cada vez só com os áudios daquele grupo.
- As duas fatias usam as classificações atuais, do cache, então a comparação continua válida mesmo que a taxonomia mude.
- Com menos de 5 respondentes novos, a coluna não aparece. Com menos de 20, o prompt trata a diferença como variação observada, nunca como tendência.

**Trechos de evidência para a IA.** Até 6 por subtema, de respondentes diferentes, ordenados pela força da nota do texto. Os que estão na tabela de divergências voz × texto vêm marcados.

**Apêndice técnico.** Texto gerado pelo código a partir das tabelas que já existem hoje:

- médias acústicas por áudio;
- distribuição de emoções;
- momentos de maior ativação;
- perguntas com maior ativação (entrevista);
- ranking de palavras;
- sentimento do texto;
- índice combinado;
- divergências voz × texto;
- legenda dos aliases;
- limitações e cortes aplicados.

## 8. Estrutura do relatório

1. Cabeçalho (código)
2. Resumo Executivo (IA)
3. Taxonomia de Temas e temas emergentes (código)
4. Achados (IA)
5. Hipóteses (IA)
6. Recomendações (IA)
7. Matriz de Destaques (tabelas do código, perfil e ação da IA)
8. Termômetro de Experiência (número do código, leitura da IA)
9. Apêndice técnico (código)
10. Referências da base de conhecimento (como hoje)

O código depende destes formatos de linha para fazer a verificação:

```
- **A1** · Categoria > Subtema · faixa, n/N — o que foi dito (Áudio NN, mm:ss). Voz: leitura da tabela.
- **H1** — enunciado. *Apoia-se em:* A1, A3. *Como validar:* …
- **R1** [Higiene] (Driver: Retenção · Área: Manutenção) — ação. *Responde a:* A1, H2. *Como medir:* …
```

Depois da geração, a verificação automática aparece na tela (não impede salvar) e confere que:

- todo A ou H citado existe;
- toda recomendação tem "Responde a";
- toda hipótese se apoia em pelo menos um achado;
- todo "Áudio NN" citado está no recorte;
- todo subtema citado está na tabela;
- toda fração n/N citada bate com a tabela.

## 9. Prompt

- A Análise Geral passa a ter um system prompt só, com adendo por tipo (Entrevista e Opinião). Saem `PROSODIA_PROJECT_SYSTEM_PROMPT_STATISTICAL` e `PROSODIA_PROJECT_SYSTEM_PROMPT_STRATEGIC`.
- Os blocos compartilhados com os prompts individuais (`PROSODIA_EVIDENCE_RULES`, `PROSODIA_CX_FRAMEWORK` e `PROSODIA_LEITURA_MULTIMODAL`) não mudam para os individuais. A Análise Geral usa versões próprias:
  - **Rigor:** sai "Inclua uma seção de Limitações", que vai para o apêndice, e entra "use só números das tabelas recebidas". Os sumários das análises individuais são interpretação prévia: podem inspirar hipóteses, mas não sustentam achado nem número.
  - **Referencial CX:** sai o parágrafo que pede para a IA dar a nota do Termômetro, e sai a lente Pico e fim (não há mais seção para ela). Ficam demanda emocional × racional, higiene × encantamento, esforço percebido, jornada, vínculo com o negócio e calibração ao setor.
  - **Leitura multimodal:** a leitura de voz chega pronta por tema. A IA a usa para escrever hipóteses, nunca como achado isolado.
- A ordem aproveita o viés de recência: papel → leitura multimodal → referencial CX → rigor e regras de amostra → **Estrutura de Resposta Obrigatória** → `<exemplo_de_saida>` → lembrete final de três linhas (achado é fato da tabela; hipótese é inferência com base e validação; recomendação é ação rastreada).
- A mensagem do usuário traz, nesta ordem:
  1. contexto do projeto;
  2. cabeçalho (N, recorte, exploratório);
  3. tabela de taxonomia;
  4. trechos por subtema;
  5. emergentes;
  6. tabelas da Matriz;
  7. Termômetro;
  8. divergências voz × texto;
  9. Sumários Executivos das análises individuais, rotulados "interpretação prévia — não é fonte de achado";
  10. por último, a tarefa, repetindo a lista de seções.
- A temperatura cai para 0,3 (hoje é 0,5). A base de conhecimento continua, com o filtro novo.

Texto proposto da Estrutura de Resposta Obrigatória:

```markdown
## Estrutura de Resposta Obrigatória
Escreva somente as seções abaixo, com estes títulos e nesta ordem. Não reescreva as
tabelas que você recebeu: o sistema as insere no relatório. Não escreva introdução,
conclusão nem limitações.

### Resumo Executivo
De 3 a 5 linhas: os três achados mais relevantes e a ação prioritária, citando os IDs.

### Achados
Um item por subtema em "padrão" ou "sinal", na ordem da tabela:
- **A<n>** · <Categoria > Subtema> · <faixa>, <n/N> — o que foi dito, com até dois
  exemplos (Áudio NN, mm:ss). Voz: <leitura da tabela>.
Use só números e leituras de voz que estão nas tabelas. Não some contagens de temas
diferentes: o mesmo respondente pode estar nos dois. Feche com uma linha, sem ID, para
as menções isoladas e os temas emergentes.

### Hipóteses
- **H<n>** — enunciado. *Apoia-se em:* A<n>[, A<n>]. *Como validar:* dado operacional
  ou pergunta para a próxima coleta.
Nenhuma hipótese se apoia só em menção isolada.

### Recomendações
De três a cinco, em ordem de prioridade, só a partir de temas em "padrão" ou "sinal":
- **R<n>** [Higiene|Encantamento] (Driver: <retenção, aquisição, custo operacional ou
  reputação> · Área: <área dona>) — ação específica. *Responde a:* A<n>, H<n>.
  *Como medir:* indicador observável, de preferência a fatia do tema na próxima análise.

### Matriz de Destaques — leitura
Para cada grupo da tabela recebida: *Perfil:* uma ou duas frases. *Ação:* uma frase.

### Termômetro — leitura
Duas ou três frases sobre o que puxa o saldo, citando IDs. Não altere o número.
```

## 10. Exemplo de saída (setor fictício)

Os blocos marcados **[código]** são gerados pelo sistema. Os marcados **[IA]** são o que o modelo escreve. O `<exemplo_de_saida>` do prompt leva só os blocos [IA], com os títulos da Estrutura Obrigatória, precedidos de: "Ilustra formato e profundidade num setor fictício. Não reutilize temas, termos nem números."

---

**[código]**

> **Rede de lavanderias self-service — Voz do Cliente por QR Code** · 64 respondentes (52 da análise de 30/09 e 12 novos) · taxonomia validada com a cliente

### Resumo Executivo **[IA]**

Máquina fora de serviço é a dor mais citada (A1, 19/64) e a única dor dita com ativação alta. A falha de pagamento no app (A3, 11/64) é relatada sem energia na voz e aparece em 5 dos 12 respondentes novos. O encantamento vem do atendente da unidade, citado pelo nome (A2, 13/64). A ação prioritária é mostrar o status da máquina antes do pagamento e estornar automaticamente o ciclo interrompido (R1); em seguida, corrigir o fluxo de pagamento (R2).

### 1. Taxonomia de Temas **[código]**

| Categoria > Subtema | Respondentes | Faixa | Texto | Voz nos trechos | Anteriores → novos |
|---|---|---|---|---|---|
| Equipamentos > Máquina fora de serviço | 19/64 (30%) | Padrão | 17 neg · 2 mistas | abaixo do habitual, ativação alta → reforça | 13/52 → 6/12 |
| Atendimento > Atendente da unidade | 13/64 (20%) | Padrão | 12 pos · 1 mista | acima do habitual, ativação alta → reforça | 11/52 → 2/12 |
| Pagamento > Falha no app de pagamento | 11/64 (17%) | Padrão | 11 neg | no habitual, ativação baixa → atenua | 6/52 → 5/12 |
| Ambiente > Limpeza da loja | 9/64 (14%) | Padrão | 8 pos · 1 neg | no habitual, ativação baixa → atenua | 8/52 → 1/12 |
| Ambiente > Espera por máquina livre | 6/64 (9%) | Sinal | 6 neg | abaixo do habitual, ativação média → reforça | 5/52 → 1/12 |
| Pagamento > Preço do ciclo | 4/64 (6%) | Sinal | 3 neg · 1 pos | não avaliável (4 trechos) | 4/52 → 0/12 |
| Atendimento > Suporte pelo WhatsApp | 3/64 (5%) | Sinal | 2 neg · 1 pos | não avaliável (3 trechos) | 2/52 → 1/12 |
| Equipamentos > Secagem incompleta | 2/64 (3%) | Menção isolada | 2 neg | — | 2/52 → 0/12 |

Temas emergentes (fora da taxonomia): Estacionamento (Ambiente), 2/64, 2 neg · Cheiro do sabão (Produtos), 1/64, 1 pos.

_Faixas: 1–2 respondentes, menção isolada; 3–4, sinal; 5 ou mais e pelo menos 10% do total, padrão. "Anteriores → novos" compara os respondentes da análise de 30/09 com os que chegaram depois; 12 novos não sustentam tendência._

### 2. Achados **[IA]**

- **A1** · Equipamentos > Máquina fora de serviço · padrão, 19/64 — 17 dos 19 descrevem a máquina parada de forma negativa. Os trechos falam em aviso de manutenção recorrente e em ciclo pago e perdido (Áudio 12, 00:14; Áudio 31, 00:09). Voz: abaixo do habitual, com ativação alta — reforça o texto.
- **A2** · Atendimento > Atendente da unidade · padrão, 13/64 — 12 dos 13 elogiam o atendente. Os trechos o citam pelo nome e falam de uma ajuda fora do roteiro, como ensinar a usar o app na primeira lavagem (Áudio 03, 00:21; Áudio 40, 00:05). Voz: acima do habitual, com ativação alta — reforça.
- **A3** · Pagamento > Falha no app de pagamento · padrão, 11/64 — as 11 menções são negativas: cobrança que não libera a máquina e estorno demorado (Áudio 08, 00:11; Áudio 45, 00:17). Voz: no habitual, com ativação baixa — atenua. O tema aparece em 5 dos 12 respondentes novos e em 6 dos 52 anteriores, o que é variação observada, não tendência.
- **A4** · Ambiente > Limpeza da loja · padrão, 9/64 — 8 dos 9 elogiam a limpeza em frases curtas, sem detalhe (Áudio 22, 00:03). Voz: no habitual, com ativação baixa — atenua.
- **A5** · Ambiente > Espera por máquina livre · sinal, 6/64 (menos de 10% dos respondentes) — as 6 menções são negativas, e os trechos citam sábado e domingo (Áudio 19, 00:08; Áudio 52, 00:12). Voz: abaixo do habitual — reforça.
- **A6** · Pagamento > Preço do ciclo · sinal, 4/64 — 3 menções negativas e 1 positiva. Leitura de voz não avaliável (4 trechos).
- **A7** · Atendimento > Suporte pelo WhatsApp · sinal, 3/64 — 2 menções negativas, sobre demora na resposta (Áudio 37, 00:10), e 1 positiva. Leitura de voz não avaliável (3 trechos).
- Menções isoladas, sem hipótese própria: Secagem incompleta (2/64) e os emergentes Estacionamento (2/64) e Cheiro do sabão (1/64).

### 3. Hipóteses **[IA]**

- **H1** — A falha de pagamento virou atrito normalizado: o cliente relata o problema sem energia na voz, o que combina mais com desistência silenciosa do que com reclamação ativa. *Apoia-se em:* A3. *Como validar:* comparar a recompra em 30 dias de quem teve estorno com a de quem não teve, e perguntar na próxima coleta se o cliente deixou de usar a loja por causa do pagamento.
- **H2** — Máquina parada é a dor com mais chance de virar reclamação pública: é a mais citada e a única dita com ativação alta e valência abaixo do habitual. *Apoia-se em:* A1. *Como validar:* cruzar, por unidade, as menções ao tema com o tempo médio de reparo e com as reclamações em canais públicos.
- **H3** — O encantamento depende de pessoas, não de processo: o elogio ao atendente vem com nome e com voz acima do habitual, enquanto a limpeza, item de higiene, recebe elogio protocolar. *Apoia-se em:* A2, A4. *Como validar:* verificar, no recorte por QR code, se os elogios ao atendente se concentram em poucas unidades ou turnos.

### 4. Recomendações **[IA]**

- **R1** [Higiene] (Driver: Retenção · Área: Manutenção) — Mostrar no app e na própria máquina o status "fora de serviço" antes do pagamento, e estornar automaticamente o ciclo interrompido. *Responde a:* A1, H2. *Como medir:* fatia de respondentes no tema na próxima análise (hoje 19/64) e estornos manuais por unidade.
- **R2** [Higiene] (Driver: Retenção · Área: Produto digital) — Corrigir o ponto do fluxo em que a cobrança não libera a máquina; até a correção, estornar em até 24 horas com aviso no app. *Responde a:* A3, H1. *Como medir:* menções ao tema entre os respondentes novos (hoje 5/12) e prazo médio de estorno.
- **R3** [Encantamento] (Driver: Reputação · Área: Operações das unidades) — Transformar o que os atendentes elogiados fazem em rotina de turno (cumprimentar pelo nome, oferecer ajuda na primeira lavagem) e levar a rotina às unidades sem elogio. *Responde a:* A2, H3. *Como medir:* elogios ao atendimento por unidade, no recorte por QR code, na próxima análise.
- **R4** [Higiene] (Driver: Retenção · Área: Operações) — Mostrar no app a ocupação das máquinas por horário, para o cliente fugir do pico de fim de semana. *Responde a:* A5. *Como medir:* menções a espera na próxima análise e uso fora do pico.

### 5. Matriz de Destaques

**Vínculo forte** — índice combinado no terço superior do projeto e texto acima de +0,2 **[código]**

| Respondente | Texto | Voz | Índice | Temas |
|---|---|---|---|---|
| Áudio 03 | +0,82 | +0,36 | +0,59 | Atendente da unidade; Limpeza da loja |
| Áudio 11 | +0,74 | +0,28 | +0,51 | Atendente da unidade |
| Áudio 27 | +0,66 | +0,30 | +0,48 | Atendente da unidade; Preço do ciclo |
| Áudio 40 | +0,70 | +0,18 | +0,44 | Atendente da unidade; Limpeza da loja |
| Áudio 58 | +0,61 | +0,23 | +0,42 | Limpeza da loja |

Temas mais citados no grupo: Atendente da unidade (4/5), Limpeza da loja (3/5).

**[IA]** *Perfil:* clientes que citam o atendente pelo nome e falam com voz acima do habitual; o vínculo é com a pessoa, não com a marca (H3). *Ação:* convidar para depoimento e indicação, e avisar a unidade antes de trocar de turno o atendente citado.

**Risco de abandono silencioso** — texto abaixo de −0,2 e ativação no terço inferior do projeto **[código]**

| Respondente | Texto | Ativação | Temas |
|---|---|---|---|
| Áudio 08 | −0,64 | −0,48 | Falha no app de pagamento |
| Áudio 19 | −0,52 | −0,41 | Espera por máquina livre; Máquina fora de serviço |
| Áudio 33 | −0,45 | −0,39 | Falha no app de pagamento; Preço do ciclo |
| Áudio 45 | −0,58 | −0,44 | Falha no app de pagamento |

Temas mais citados no grupo: Falha no app de pagamento (3/4).

**[IA]** *Perfil:* reclamação de pagamento dita sem energia, o retrato de H1. *Ação:* contato da unidade em até 7 dias, com confirmação do estorno e um ciclo de cortesia, acompanhando se o cliente volta.

### 6. Termômetro de Experiência

**[código]** **−1** (de −5 a +5): sentimento médio do texto, −0,18 × 5 = −0,9, arredondado. Respondentes pela média do texto: 20 positivos, 19 neutros e 25 negativos. _Leitura do texto; a voz entra pelos qualificadores dos temas._

**[IA]** O saldo fica negativo por duas dores de higiene em padrão: máquina parada (A1, 19/64) e pagamento (A3, 11/64). O que puxa para cima é o elogio ao atendente (A2, 13/64); a limpeza (A4) é elogiada sem energia e pesa pouco.

### Apêndice técnico **[código]**

Inclui médias acústicas por respondente, distribuição de emoções, momentos de maior ativação, ranking de palavras, sentimento do texto, índice combinado, divergências voz × texto, legenda "Áudio NN → sessão", limitações e cortes aplicados.

---

## 11. Base de conhecimento

- `build_kb_filter` ganha o parâmetro `incluir_analises=True`, e a Análise Geral passa `False`. A cláusula do projeto vira "documento do projeto com `escopo` diferente de `analise`". Literatura (`referencia`) e material do projeto (briefing, documentos) continuam entrando.
- Como os relatórios individuais também têm `escopo="analise"` (`audio.py:519`), eles saem da busca da Análise Geral. O Sumário Executivo de cada um entra pelo banco (§9).
- A Análise Geral continua sendo enviada para a base, porque o chat e outras telas podem usá-la. Ela só deixa de voltar como evidência da próxima.

## 12. Mudanças por arquivo

| Arquivo | Mudança |
|---|---|
| `utils/prosodia_db.py` | Colunas `projects.taxonomia` e `projects.taxonomia_status`. Tabela `audio_temas`. Coluna `project_analyses.temas_json`, com o snapshot (tabela, lista de áudios, recorte e N). Funções de leitura e gravação, e migração incremental no padrão do `init_db`. |
| `modules/prosodia/preparacao.py` | Seção "Taxonomia de temas" depois de "Entidades prioritárias". |
| `utils/prosodia_temas.py` (novo) | Taxonomia (parse e versão), proposta provisória, classificação por áudio e validação do JSON. |
| `utils/prosodia_relatorio.py` (novo) | Agregação (contagem, faixas, voz por tema, Matriz, Termômetro, Δ e aliases), extração dos Sumários Executivos das análises individuais, tabelas em markdown, apêndice, montagem e verificação do relatório. |
| `utils/prosodia_prompts.py` | Prompt único da Análise Geral, com adendo por tipo. Saem os prompts estatístico e estratégico de projeto, e entra um `build_project_user_prompt` novo. Os prompts individuais ficam com o texto atual. |
| `utils/kb_attributes.py` | `build_kb_filter(..., incluir_analises=True)`. |
| `modules/prosodia/analise_geral.py` | Sai o seletor de modo (`:1155`). Novo fluxo de geração (`:1363`) com as etapas visíveis na tela, e gravação do snapshot. |
| `tests/` | Testes novos (§15) e ajuste de `test_project_type.py`. |

Ordem de implementação:

1. Banco e Preparação (taxonomia).
2. Classificação por áudio, com cache.
3. Agregação e apêndice, com testes.
4. Prompt e montagem do relatório.
5. Fluxo na página e filtro da base.
6. Validação com a Smart Fit e com uma Entrevista.

## 13. Pontos resolvidos em 15/10/2026

A primeira versão deixou quatro pontos em aberto. O registro fica aqui porque explica escolhas que, sem o contexto, parecem arbitrárias.

**1. Termômetro: texto × 5.** A metade de voz do índice combinado é o z da valência em relação a todas as linhas do VAD do próprio projeto (`prosodia_signals.py:490`, com a régua montada em `analise_geral.py:815`). No projeto inteiro, a média desse z é zero por definição, então "índice × 5" daria cerca de 2,5 × o sentimento do texto, com a voz quase sem peso. Ficou o sentimento do texto × 5, declarado como tal (§7).
- Descartado: régua fixa de voz a partir do acervo da organização, que depende das condições de gravação e dá mais trabalho.
- Descartado: manter índice × 5.

**2. Δ: respondentes novos × anteriores.** A coleta é contínua e cada Análise Geral cobre todos os áudios até ali. Comparar a tabela de hoje com a salva mostraria mais diluição do que mudança. Ficou a comparação entre quem chegou depois da última análise e quem ela cobriu (§7).
- Descartado: total × total salvo.
- Descartado: comparar só períodos disjuntos.

**3. Análises individuais: só o Sumário Executivo.** As análises individuais são geradas na entrada de cada áudio (`audios.py:263` e `whatsapp_monitor.py:611`). Inteiras, somariam algo como 50 a 100 mil tokens na Smart Fit, com interpretações que a IA tende a repetir como fato. Entra só o Sumário Executivo de cada uma, rotulado como interpretação prévia (§9).
- Descartado: não usar as análises individuais.
- Descartado: usar as análises inteiras.

**4. Validação com a Demonstração ABIHPEC.** É o projeto 6 na cópia local de 09/10, com 11 áudios, todos com análise individual. Com menos de 20 respondentes, exercita o modo exploratório e ainda permite tema em "padrão". Antes de validar, falta confirmar o id em produção e se as transcrições separam entrevistador e entrevistado; a leitura de produção foi bloqueada nesta sessão.

## 14. Detalhes propostos — revise se discordar

- **Unidade de contagem:** a pessoa.
  - Na Pesquisa de Opinião, áudios do WhatsApp com o mesmo número (`wa_+<número>_<seq>`) contam como um respondente, que menciona o tema se qualquer áudio dele mencionar.
  - Na Entrevista, cada áudio é um respondente, porque o número que envia é o do entrevistador.
  - Nas demais origens, cada áudio é um respondente.
  - A Matriz continua por áudio.
- **Aliases:** o corpo cita "Áudio 07" em vez do `session_id`, que traz o telefone. O código numera os áudios, a IA copia o alias (curto e difícil de errar) e o código confere. A legenda alias → sessão fica no apêndice, ou só na tela se a cliente não deve receber telefones.
- **Limiares de voz por tema:** ±0,15 na valência (cerca de 0,3 desvio) e ±0,3 desvio na ativação. São iniciais e serão calibrados com a Smart Fit.
- **Matriz:** terços calculados sobre todos os áudios do projeto, com mínimo de 10 áudios com índice.
- **Trechos para a IA:** até 6 por subtema, de respondentes diferentes.
- **Emergentes:** contados pelo rótulo exato. O classificador recebe os rótulos já usados no projeto para repetir o nome.
- **Recorte:** a classificação não depende do filtro, mas a contagem usa só os áudios do recorte. O Δ só compara com análise do mesmo recorte de QR code e origem.
- **Verificação automática:** mostra os problemas na tela e não impede salvar.
- **Termômetro por respondente:** cada respondente pesa igual na média (§7), para uma entrevista longa não dominar o número.
- **Sumário Executivo das individuais:** é extraído do texto salvo, do título "Sumário Executivo" até a seção seguinte. Análise individual sem esse título (gerada com prompt antigo) fica de fora, e o apêndice informa quantas ficaram.
- **Análises anteriores sem snapshot** (todas as de hoje): a lista de áudios cobertos é reconstruída com os áudios do projeto criados antes da análise, desde que o texto salvo não traga "Recorte analisado". Se trouxer, a coluna Δ só aparece a partir da primeira análise com snapshot.

## 15. Validação

- **Dados:** cópia do `prosodia.db` de produção, só para leitura, fora do repositório (tem telefones), com `NENC_DB_PATH` apontando para ela. Quem faz a cópia é você: o modo automático do Claude Code bloqueia leitura de produção.
- **Comparação:** gerar com o código novo a Smart Fit (projeto 16) e a Demonstração ABIHPEC (Entrevista, 11 áudios), e comparar com os relatórios atuais por este checklist:
  1. toda fração n/N do texto bate com a tabela;
  2. todo áudio citado existe;
  3. toda recomendação cita A ou H, e toda hipótese cita A;
  4. nenhum tema com menos de 3 respondentes vira padrão ou base de hipótese;
  5. o corpo não tem seção de métricas acústicas, e a voz só aparece como qualificador;
  6. regenerar sem áudio novo devolve as mesmas tabelas e o mesmo Termômetro;
  7. na entrevista, nenhuma fala do entrevistador conta como menção;
  8. na ABIHPEC, o cabeçalho sai como leitura exploratória (11 respondentes);
  9. leitura humana: as recomendações são mais específicas que as dos relatórios atuais?
- **Revisão:** Pedro revisa os dois relatórios antes do deploy.
- **Testes (pytest):**
  - parse e versão da taxonomia;
  - validação do JSON da classificação;
  - contagem por respondente (várias menções no mesmo áudio, ou do mesmo número, contam uma vez);
  - faixas nos limites (2/3, 4/5, e 5 com menos de 10%);
  - qualificador de voz nos limiares e com menos de 5 trechos;
  - Matriz (terços e mínimo de áudios);
  - Termômetro (média por respondente e "não avaliável" sem nota de texto);
  - Δ (novos × anteriores, reconstrução para análise sem snapshot, limites de 5 e 20 novos);
  - extração do Sumário Executivo das análises individuais;
  - filtro da base sem análises;
  - verificação das referências;
  - ordem do prompt (estrutura e exemplo no fim);
  - ajuste de `tests/test_project_type.py`.

## 16. Fora do escopo

- Prompts e páginas das análises individuais.
- Conceito de "onda", com nome e período, no projeto.
- Régua fixa de voz entre projetos, descartada para o Termômetro em 15/10.
- Exportação dos temas para o Power BI.
- Limpeza dos documentos fora do lugar na base de conhecimento de produção, que é uma pendência separada.
