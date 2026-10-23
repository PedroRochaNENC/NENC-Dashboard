# Unidades e taxonomia por tags no NencBoost — especificação

- **Status:** rascunho para revisão
- **Data:** 23/10/2026
- **Responsável:** Pedro Rocha
- **Escopo:** a entidade Unidade (banco, cadastro, vínculo com o QR code e as telas que mostram o áudio) e a Análise Geral (`modules/prosodia/analise_geral.py`). Prompts e páginas das análises individuais não mudam.
- **Origem:** documento "Feedback sobre o primeiro relatório NENC" da Smart Fit, planilha de tags da Smart Fit (`tags_mapping.xlsx`, 16 tags) e sessão de decisões de 23/10/2026. Continua a [especificação v2](SPEC_ANALISE_GERAL.md), que segue valendo onde esta não muda nada.

## 1. Por que mudar

A Smart Fit recebeu um relatório da v2 e mandou o feedback por escrito. A v2 foi desenhada a partir dos mesmos pontos, e o relatório ainda não respondeu a eles por completo:

| Ponto do feedback | O que a v2 já faz | O que falta |
|---|---|---|
| 2. Achados acionáveis: "o que observamos / o que isso significa" | A1/H1/R1 com rastreio | As limitações foram para o apêndice técnico, em linguagem técnica, e ninguém as traduz para o leitor de negócio. |
| 3. Taxonomia acordada entre Smart Fit e NENC | Campo por projeto, `Categoria > Subtema`, provisória ou validada | A Smart Fit mandou 16 tags *macro*, com descrição, e quer que a IA crie os subtemas dentro delas. A v2 exige subtemas fixos. Alerta Crítico pede tratamento próprio. Preço e valor percebido não cabem em nenhuma tag. |
| 4. Diferenciar achado, hipótese e recomendação | Três blocos com IDs e verificação | Um "sinal" (3–4 respondentes) ainda gera recomendação de ação, mesmo na leitura exploratória. |
| 5. Cuidado com a ausência de menções | Uma linha "Subtemas sem menção no recorte" | Nenhuma regra impede a IA de concluir algo a partir do que não foi dito. |
| 6. Voz como camada complementar | Voz como qualificador de cada tema | O vocabulário continua técnico: "abaixo do habitual, ativação alta → reforça", "índice no terço superior", "−0,18 × 5". |
| 7. Visão de CX por unidade, período e canal | Δ entre análises e recorte por QR code | Não existe unidade no sistema. O áudio só guarda o nome do QR (`audios.qr_code_name`), que pode ser renomeado. |

## 2. Decisões tomadas

| # | Tema | Decisão |
|---|---|---|
| 1 | Taxonomia da Smart Fit | As 16 tags da planilha são a taxonomia oficial e entram como **validada**. |
| 2 | Modelo | A tag e a descrição dela são fixas, e a IA cria os subtemas dentro de cada tag, reaproveitando os rótulos já usados no projeto. |
| 3 | Nível da contagem | Faixa, ID do achado, polaridade, Δ e voz ficam na **tag**. Os subtemas aparecem dentro dela, com contagem, sem faixa própria. |
| 4 | Formatos | Os dois convivem, decididos pelo formato da linha. Linha sem ">" é tag com subtemas da IA. Linha com ">" segue como na v2. O achado fica na linha da taxonomia: na tag ou no subtema fixo. |
| 5 | Alerta Crítico | Qualquer menção, mesmo uma só, entra num bloco "Alertas críticos" no topo do relatório, fora das regras de faixa, e o áudio ganha um selo. A tag de alerta é marcada com `!` no início da linha. |
| 6 | Fora das tags | Vira a linha "Outros", com os subtemas da IA. Subtema de Outros com 3 ou mais respondentes aparece como candidato a tag nova. |
| 7 | Ausência | A tabela sempre traz todas as tags. As que não tiveram menção saem com "sem menções espontâneas nesta amostra" e uma legenda fixa. O prompt proíbe interpretar a ausência. |
| 8 | Recomendação | Recomendação de ação só sai de tag em padrão, e nunca na leitura exploratória. Tag em sinal vai para "Para acompanhar". Abaixo de 20 respondentes, a seção de Recomendações dá lugar a "Para acompanhar e investigar". |
| 9 | Cuidados de leitura | Seção "Como ler esta análise", escrita pelo código, no formato *O que observamos → O que isso significa*. |
| 10 | Voz | "Sinal emocional" em frase fixa de negócio, escrita pelo código. Valência, ativação e "habitual" só no apêndice. |
| 11 | Matriz e Termômetro | Ficam no corpo, sem números. Os números e a conta vão para o apêndice. |
| 12 | Unidade | Entidade da **organização**, com nome, código interno, cidade e UF. |
| 13 | Vínculo | O áudio herda a unidade do QR code por onde entrou, os áudios antigos inclusive. Correção manual na página do áudio vence o vínculo. Áudio sem QR fica em "Sem unidade". |
| 14 | Cadastro | Página "Unidades" no nível do módulo, com importação por planilha e sugestão a partir dos nomes dos QRs. Na tela de QR codes, cada QR ganha a escolha da unidade. |
| 15 | Telas | Unidade na lista de Áudios, no Resumo, na página do áudio e na exportação Excel/Power BI. |
| 16 | Unidade na Análise Geral | Filtro do recorte, como QR e origem, e coluna "Unidades" na tabela de tags (em quantas unidades a tag aparece). |
| 17 | Ordem | Unidade primeiro, depois o relatório (§9). |

## 3. Unidade

### 3.1 Banco

Tabelas novas, criadas pelo `init_db` no padrão de hoje:

```sql
CREATE TABLE IF NOT EXISTS unidades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    codigo          TEXT,             -- código interno da cliente, opcional
    nome            TEXT    NOT NULL,
    cidade          TEXT,
    uf              TEXT,
    created_at      TEXT    DEFAULT (datetime('now','localtime'))
);
-- Código único dentro da organização, quando preenchido.
CREATE UNIQUE INDEX IF NOT EXISTS ux_unidades_codigo
    ON unidades(organization_id, codigo) WHERE codigo IS NOT NULL;

CREATE TABLE IF NOT EXISTS qr_unidades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    qr_code         TEXT    NOT NULL, -- o código do QR na API, ex. "16-03"
    unidade_id      INTEGER NOT NULL REFERENCES unidades(id) ON DELETE CASCADE,
    UNIQUE (project_id, qr_code)
);
```

Colunas novas em `audios`:

- `qr_code_code` (TEXT): o código do QR. O nome continua em `qr_code_name`, para exibição.
- `unidade_manual_id` (INTEGER, `REFERENCES unidades(id) ON DELETE SET NULL`): a correção feita à mão.

**A unidade do áudio é calculada na leitura**, e não gravada: `COALESCE(audios.unidade_manual_id, qr_unidades.unidade_id)`, com o join por `project_id` e `qr_code_code`. Assim o vínculo de um QR vale para os áudios antigos sem propagação, e trocar a unidade de um QR muda todos os áudios dele de uma vez. As funções de leitura de áudios de `prosodia_db` passam a devolver `unidade_id`, `unidade_nome`, `unidade_codigo` e `unidade_origem` (`qr`, `manual` ou vazio).

**Por que o código e não o nome do QR.** O nome pode ser editado na tela de QR codes, e o código não (o campo é desabilitado, `qr_codes.py`). Um vínculo pelo nome se perderia na primeira renomeação.

### 3.2 Código do QR nos áudios

- **Áudios novos.** Os dois pontos de entrada gravam `qr_code_code` junto com o nome: a chegada pelo Monitor (`whatsapp_monitor.py:494`) e a sincronização da lista de Áudios (`entrevistas.py:260`), que já leem `qr_code_code` da API.
- **Áudios existentes.** Script `scripts/backfill_audio_qr_code.py`, no molde de `backfill_interview_qr_codes.py`: para cada projeto, lê a lista de QRs da API e preenche `qr_code_code` casando `qr_code_name` com o nome ou o código do QR. Nome que casa com mais de um QR fica vazio e vai para o relatório do script. Faz backup antes, como os outros backfills, e roda dentro da imagem nova, antes do `up -d`, como o `backfill_kb_attributes.py` rodou em 14/09/2026.

### 3.3 Página Unidades

- Fica no nível 1 do menu do NencBoost, ao lado de Base de Conhecimento (`app.py:333`), porque a unidade é da organização e não do projeto.
- Lista: código, nome, cidade, UF e quantos QRs e áudios estão ligados a cada uma.
- **Nova** e **editar**, num formulário com os quatro campos.
- **Importar planilha** (CSV ou XLSX, colunas `codigo`, `nome`, `cidade`, `uf`): atualiza pelo código quando ele existe e cria o que não existe. Mostra a prévia antes de gravar.
- **Sugerir pelos QRs**: lista os nomes distintos dos QRs dos projetos da organização ainda sem unidade, cada um como uma unidade proposta, com o nome editável. A equipe confirma uma por uma, e a confirmação já liga o QR à unidade criada.
- **Excluir**: pede confirmação e diz quantos QRs e áudios vão ficar sem unidade.
- Quem só consulta vê a lista com os botões desligados (`_require_write`).

### 3.4 Onde a unidade aparece

| Tela | Mudança |
|---|---|
| QR codes (`qr_codes.py`) | Cada QR ganha a escolha da unidade, com a opção "Sem unidade". Trocar mostra quantos áudios mudam. |
| Página do áudio (`audio.py`) | Unidade no cabeçalho, com a origem ("pelo QR 16-03" ou "corrigida à mão"). Quem pode editar troca a unidade ou volta a seguir o QR. |
| Lista de Áudios (`entrevistas.py`) | Coluna e filtro por unidade. Selo de Alerta crítico (§4.4). |
| Resumo (`resumo.py`, `prosodia_summary.py`) | Ranking de participação por unidade, ao lado do ranking por QR que já existe. |
| Exportação Excel/Power BI (`prosodia_powerbi_export.py`) | Código, nome, cidade e UF da unidade em cada áudio. Planilha nova "Temas por áudio": áudio, data, unidade, categoria ou tag, subtema, polaridade e alerta, a partir da classificação gravada em `audio_temas`. |
| Análise Geral | Filtro e coluna "Unidades" (§3.5). |

### 3.5 Unidade na Análise Geral

- **Recorte.** Um filtro "Unidade" ao lado de QR code, período e origem (`analise_geral.py:575`). `Recorte` ganha o campo `unidade`, gravado no snapshot, e a análise anterior do Δ passa a exigir o mesmo QR, a mesma origem e a mesma unidade (`mesmo_qr_e_origem` vira `mesmo_filtro`). Snapshot antigo sem o campo conta como "todas as unidades".
- **Coluna "Unidades"** na tabela de temas, calculada em código sobre os respondentes com unidade:
  - "*k* de *m*", onde *m* é o número de unidades com respondente no recorte e *k*, as que têm o tema;
  - "concentrada em <unidade> (*a*/*b*)" quando uma unidade sozinha tem mais da metade dos respondentes do tema que têm unidade, e o tema tem pelo menos 3 deles;
  - um respondente da Opinião que mandou áudios por QRs de unidades diferentes conta em cada uma delas, e uma vez só no total;
  - a coluna não aparece quando o recorte é de uma unidade só ou quando nenhum respondente tem unidade. A legenda diz quantos respondentes ficaram fora por estarem sem unidade.
- **Cabeçalho**: o recorte por unidade entra na linha "Recorte".
- **Matriz**: a tabela de cada grupo ganha a coluna Unidade. Os terços continuam sobre todos os áudios do projeto.
- **Alertas críticos**: cada alerta traz a unidade (§4.4).

## 4. Taxonomia por tags

### 4.1 Formato

| Linha | Leitura |
|---|---|
| `Staff: atendimento na recepção, suporte dos professores, presença do líder/gerente` | Tag com subtemas da IA. A definição vai para o classificador. |
| `Equipamentos > Máquina fora de serviço: máquina parada ou quebrada` | Subtema fixo, como na v2. |
| `! Alerta Crítico: assédio (moral, sexual, discriminação), acidentes graves, furtos` | Tag de alerta (§4.4). O `!` vale nos dois formatos. |

- `parse_taxonomia` (`prosodia_temas.py:106`) passa a aceitar a linha sem ">" em vez de mandá-la para `linhas_invalidas`. O modelo ganha o tipo da linha (tag aberta ou subtema fixo) e o marcador de alerta.
- Uma tag aberta e um subtema fixo com a mesma categoria não convivem: "Staff" e "Staff > Recepção" no mesmo campo é linha inválida, porque não haveria como dizer em qual das duas a menção conta.
- A versão (`versao_taxonomia`) inclui o tipo da linha e o marcador, então trocar o formato ou marcar o alerta reclassifica os áudios. A tela já avisa quantos antes de gerar.
- A proposta provisória da IA (`propor_taxonomia`) continua gerando `Categoria > Subtema`. Tag aberta é só para taxonomia que a cliente escreveu.

Texto da Smart Fit para o campo, com o status "Taxonomia validada com a cliente":

```
! Alerta Crítico: Assédio (moral, sexual, discriminação), acidentes graves/lesões e furtos/roubos
Staff: Atendimento na recepção, suporte dos professores, presença do líder/gerente
Limpeza: Higiene e limpeza de áreas de treino, recepção, sanitários e vestiários
Lotação: Superlotação, filas e espera para aparelhos em horários de pico
Aulas Coletivas: Qualidade das aulas em grupo, grade de horários, instrutores de coletivas
Manutenção de Equipamentos de Treino: Defeitos mecânicos e manutenção de máquinas, esteiras, bikes, halteres
Quantidade e Qualidade de Equipamentos de Treino: Disponibilidade e qualidade de máquinas, esteiras, bikes, halteres
Facilities e Predial: Estrutura física: piso, infiltrações, pintura, portas, teto, iluminação, chuveiros
Climatização / Ar Condicionado: Calor, ambiente abafado, ventilação, ar-condicionado quebrado/desligado
Estrutura de Tecnologia Local: Wi-Fi, catracas, biometria, totens, balança digital, body scan, som
Sistemas Financeiro: Cobranças indevidas, reajustes, multas, cancelamento de plano
Aplicativo: Erros de login, bugs, falhas no agendamento, ficha de treino
Atendimento Canais Digital: Suporte via WhatsApp, Fale Conosco, Reclame Aqui, Redes Sociais
Addons: Serviços complementares: Nutri, Energy, Cadeira de Massagem, Coach
Serviços Agregados: Estacionamento, segurança patrimonial, vending machines
Total Pass: Problemas de acesso, validação de tokens, regras do TotalPass
```

A definição começa no primeiro dois-pontos, então os dois-pontos dentro de "Facilities e Predial" e "Addons" ficam na definição, como devem.

### 4.2 Classificação

- O prompt da classificação (`SISTEMA_CLASSIFICACAO`, `prosodia_temas.py:281`) ganha a regra da tag aberta: a IA devolve a tag e um subtema curto, que descreve o assunto dentro da tag, e repete um subtema já usado naquela tag quando ele couber. Vão até 15 subtemas conhecidos por tag, dos mais citados no projeto.
- Saída para tag aberta: `{"tag": "Staff", "subtema": "Cordialidade da recepção", "polaridade": "positiva", "linhas": [3], "evidencia": "..."}`. Subtema fixo segue como na v2.
- O que não cabe em nenhuma linha vai para **Outros**, com um subtema da IA. Na taxonomia com tag aberta, Outros substitui os emergentes, e a categoria proposta pela IA deixa de existir.
- A mesma fala pode entrar em mais de uma tag. Uma menção de alerta também entra na tag do assunto, quando houver (furto no vestiário: Alerta Crítico e Serviços Agregados).
- A validação em código segue a §6 da v2: tag fora da taxonomia vira Outros, polaridade fora da lista vira neutra, linha inexistente é descartada e tema sem linha cai.
- `VERSAO_CLASSIFICACAO` sobe para "2", e todos os áudios voltam à IA na geração seguinte. Na Smart Fit são cerca de 53 chamadas ao `gpt-4.1-mini`.

### 4.3 Agregação e tabela

- A linha da tabela é a linha da taxonomia: a tag aberta ou o subtema fixo. Contagem por respondente, faixas, voz, Δ e a coluna "Unidades" seguem as regras da v2, agora no nível da tag.
- Coluna "Subtemas": os subtemas da tag com o número de respondentes, do mais citado ao menos, até 5, e "e mais *n*". Não têm faixa nem ID.
- **Todas as tags, sempre.** As mencionadas vêm primeiro, da mais citada à menos, e as sem menção depois, na ordem da taxonomia, com "0/N" e "sem menções espontâneas nesta amostra" no lugar das outras colunas. A legenda fixa, escrita pelo código: "Numa coleta aberta, a ausência de menções não indica que o tema seja irrelevante. Ele segue em acompanhamento nas próximas análises." A linha "Subtemas da taxonomia sem menção no recorte" de hoje (`prosodia_relatorio.py:1476`) sai.
- **Outros** é a última linha das mencionadas, com a contagem e os subtemas, sem faixa, ID nem voz.
- **Candidatos a tag nova**: subtemas de Outros com 3 ou mais respondentes no projeto. Aparecem na Preparação, no lugar da lista de emergentes de hoje, e no apêndice, para levar à Smart Fit.
- **Polaridade em palavras**: "15 elogios · 3 críticas · 2 mistas · 1 neutra" no lugar de "15 pos · 3 neg".

Exemplo, num recorte de 51 respondentes em 6 unidades:

| Tag | Respondentes | Faixa | O que disseram | Sinal emocional | Unidades | Subtemas |
|---|---|---|---|---|---|---|
| Staff | 18/51 (35%) | Padrão | 15 elogios · 3 críticas | Fala mais animada que a média; confirma o elogio | 6 de 6 | Cordialidade da recepção 11 · Professor ausente no salão 4 · Gerente resolveu problema 3 |
| Lotação | 9/51 (18%) | Padrão | 9 críticas | Crítica dita com resignação, sem irritação na voz | 5 de 6 | Fila nos aparelhos à noite 6 · Academia cheia no sábado 3 |
| Limpeza | 6/51 (12%) | Padrão | 6 críticas | Sem destaque em relação aos demais relatos | concentrada em Moema (5/6) | Vestiário sujo à noite 5 · Falta de papel 1 |
| Aulas Coletivas | 3/51 (6%) | Sinal | 2 elogios · 1 crítica | Poucos relatos com voz para uma leitura | 2 de 6 | Grade de horários 2 · Professora de spinning 1 |
| Outros | 4/51 | — | 3 críticas · 1 neutra | — | — | Preço da mensalidade 3 · Horário de funcionamento 1 |
| Aplicativo | 0/51 | — | sem menções espontâneas nesta amostra | | | |
| Total Pass | 0/51 | — | sem menções espontâneas nesta amostra | | | |

### 4.4 Alerta Crítico

- **Bloco "Alertas críticos"**, escrito pelo código, logo depois do cabeçalho. Um item por menção: alias do áudio, unidade, data de chegada, tempo do trecho e a evidência (a paráfrase da classificação). Nunca a fala literal, porque ela pode trazer nomes e detalhes do caso.
- Entra qualquer menção a uma tag com `!`, mesmo de um respondente só, fora das faixas. A tag continua na tabela com a contagem.
- Sem alerta, o bloco não aparece.
- A IA recebe só o número de alertas e pode citá-lo no Resumo ("há 2 alertas críticos, listados no topo"), sem comentar o conteúdo nem tirar hipótese deles.
- **Selo** "Alerta crítico" no áudio, na lista de Áudios e na página do áudio, lido de `audio_temas`. O selo só aparece depois que uma geração da Análise Geral classifica o áudio: a classificação na chegada ficou de fora nesta versão (§10).

## 5. Redação do relatório

### 5.1 Como ler esta análise

Seção nova, escrita pelo código, entre o bloco de alertas e o Resumo Executivo. Cada item segue o formato do feedback e só entra quando a condição vale:

| Condição (calculada em código) | O que observamos | O que isso significa |
|---|---|---|
| Sempre | *N* respondentes, numa coleta aberta e espontânea. | Os temas descrevem esta amostra. As frações não são a proporção do público. |
| Menos de 20 respondentes | A base ainda é pequena. | Os resultados são sinais iniciais, a acompanhar nas próximas análises, e não conclusões. |
| Duração mediana dos áudios contados abaixo de 30 s | Os relatos são curtos (mediana de *x* s). | Cada relato traz pouco contexto: um tema citado pode ter sido dito de passagem. |
| Pelo menos 70% das tags com voz avaliável saem "sem destaque" | O tom de voz varia pouco de um tema para outro. | Os sinais emocionais devem ser lidos com cautela, e seguimos observando conforme o volume cresce. |
| Alguma tag sem menção | *k* das 16 tags não foram citadas. | A ausência numa coleta aberta não indica que o tema seja irrelevante. |
| Áudios fora da contagem (sem fala, classificação que falhou, cópias) | *a* dos *b* áudios recebidos ficaram fora. | O total de respondentes é menor que o de áudios recebidos. |
| Respondentes sem unidade | *s* respondentes chegaram sem QR de unidade. | A coluna Unidades lê só os demais. |
| Taxonomia provisória | Os temas foram propostos pela IA. | Eles ainda serão revisados com a cliente e podem mudar de nome na próxima análise. |

Os limiares de 30 s e 70% são um ponto de partida, calibrado na validação (§9).

### 5.2 Sinal emocional

A regra da v2 (`prosodia_relatorio.py:109-121`, limiares, estabilidade e mínimo de 5 respondentes com voz) não muda. Muda o texto que o código escreve no corpo:

| Texto do tema | Leitura da voz (v2) | Sinal emocional |
|---|---|---|
| Positivo | acima do habitual → reforça, ativação alta | Fala mais animada que a média; confirma o elogio. |
| Positivo | acima do habitual → reforça, ativação média ou baixa | Tom de voz mais positivo que a média; confirma o elogio. |
| Positivo | no habitual, ativação baixa → atenua | Elogio dito sem entusiasmo. |
| Positivo | no habitual, ativação alta → atenua | Fala mais enérgica que a média, sem tom mais positivo. |
| Positivo | abaixo do habitual → contradiz | Tom de voz menos positivo que as palavras. |
| Negativo | abaixo do habitual → reforça, ativação alta | Irritação perceptível na voz. |
| Negativo | abaixo do habitual → reforça, ativação média ou baixa | Tom de voz mais negativo que a média; confirma a crítica. |
| Negativo | no habitual, ativação baixa → atenua | Crítica dita com resignação, sem irritação na voz. |
| Negativo | no habitual, ativação alta → atenua | Crítica dita com energia, sem tom mais negativo. |
| Negativo | acima do habitual → contradiz | Tom de voz mais leve que a crítica. |
| Misto ou neutro | acima / abaixo do habitual | Tom de voz mais positivo (ou mais negativo) que a média. |
| Qualquer | no habitual, ativação média | Sem destaque em relação aos demais relatos. |
| Qualquer | não avaliável | Poucos relatos com voz para uma leitura. |

- O prompt recebe a frase e a leitura técnica, e a orientação de ler cada combinação (`_ANALISE_GERAL_VOZ`, `prosodia_prompts.py:479`) continua, mas a IA escreve no texto só a frase. "Valência", "ativação", "habitual", "pitch" e "loudness" ficam proibidos fora do apêndice.
- A tabela "Voz por tema" do apêndice mostra as duas colunas lado a lado.

### 5.3 Achados, hipóteses, recomendações e "Para acompanhar"

- **Achados** como na v2, no nível da tag, com o subtema mais citado como exemplo: `- **A1** · Staff · padrão, 18/51 — ... Sinal emocional: ...`.
- **Hipóteses** como na v2. Hipótese continua podendo se apoiar em sinal.
- **Recomendações** só a partir de tag em **padrão**, e só fora da leitura exploratória.
- **Para acompanhar**, seção nova depois das Recomendações: um item por tag em sinal, mais todas as tags com ID na leitura exploratória.
  `- **M1** · Aulas Coletivas · sinal, 3/51 — o que observar na próxima análise. *Por quê:* A4.`
- Na leitura exploratória, a seção de Recomendações não aparece, e "Para acompanhar" vira "Para acompanhar e investigar", com um *Como investigar* por item.
- A "Ação" de cada grupo da Matriz segue a mesma regra: na leitura exploratória, ela vira o que acompanhar.
- **Regras de redação**, no bloco de rigor do prompt (`_ANALISE_GERAL_RIGOR`):
  - nada de superlativo sem número ("principal diferencial", "maior problema"): a ordem vem da fração;
  - achado e hipótese não usam "deve"; a hipótese usa "pode indicar" e "sugere";
  - ausência de menção não é achado nem base de hipótese.
- **Verificação** (`verificar_referencias`), além das regras da v2:
  - R que cita A de tag fora do padrão vira aviso;
  - R na leitura exploratória vira aviso;
  - todo M cita um A;
  - termo técnico de voz fora do apêndice vira aviso;
  - frase que liga uma tag com 0/N a "não é tema", "não é relevante" ou "não incomoda" vira aviso.

### 5.4 Matriz e Termômetro

- **Matriz.** Os grupos ganham um nome descritivo, e o critério técnico vai para o apêndice:
  - "Vínculo forte" → **Elogios ditos com entusiasmo**;
  - "Risco de abandono silencioso" → **Críticas ditas com resignação** (risco de abandono silencioso).
  A tabela de cada grupo fica com Áudio, Unidade, Tags e "Em uma frase", a evidência da menção mais forte do áudio. Texto, voz, índice e ativação vão para a tabela "Matriz — números" do apêndice. A linha "Voz no grupo" vira frase: "11 dos 15 falam com tom mais positivo que a média".
- **Termômetro.** O corpo fica com o número e o saldo: "**−1** (de −5 a +5) · 20 respondentes com relato positivo, 19 neutros e 25 negativos. Leitura do que foi dito." A conta (média × 5 e o arredondamento) vai para o apêndice.

### 5.5 Ordem do relatório

1. Cabeçalho (código)
2. Alertas críticos (código, só quando houver)
3. Como ler esta análise (código)
4. Resumo Executivo (IA)
5. Taxonomia de temas (código)
6. Achados (IA)
7. Hipóteses (IA)
8. Recomendações (IA, só fora da leitura exploratória)
9. Para acompanhar (IA)
10. Matriz de Destaques (tabelas do código, leitura da IA)
11. Termômetro de Experiência (número do código, leitura da IA)
12. Apêndice técnico (código)
13. Referências da base de conhecimento

## 6. Prompt

- `ESTRUTURA_ANALISE_GERAL` (`prosodia_prompts.py:586`) ganha a seção "Para acompanhar" e a regra das Recomendações (§5.3).
- O exemplo de saída (`EXEMPLO_ANALISE_GERAL`) passa para o formato com tags, Sinal emocional e "Para acompanhar", no mesmo setor fictício de lavanderias.
- `tarefa_analise_geral` diz, por achado, se ele pode gerar recomendação ou vai para "Para acompanhar", e manda o número de alertas críticos.
- A mensagem recebe a seção "Como ler esta análise" já pronta, como contexto, para a IA não repetir os avisos nem contradizê-los.

## 7. Mudanças por arquivo

| Arquivo | Mudança |
|---|---|
| `utils/prosodia_db.py` | Tabelas `unidades` e `qr_unidades`. Colunas `audios.qr_code_code` e `audios.unidade_manual_id`. Funções de unidades (listar, criar, editar, excluir, importar), de vínculo (QR → unidade, correção manual) e a unidade calculada nas leituras de áudio. |
| `scripts/backfill_audio_qr_code.py` (novo) | Preenche `qr_code_code` dos áudios existentes a partir da API (§3.2). |
| `modules/prosodia/unidades.py` (novo) | Página Unidades (§3.3). |
| `app.py` | Página Unidades no nível 1 do NencBoost. |
| `modules/prosodia/qr_codes.py` | Escolha da unidade em cada QR. |
| `modules/prosodia/whatsapp_monitor.py`, `modules/prosodia/entrevistas.py` | Gravar `qr_code_code` na chegada. Coluna, filtro e selo na lista de Áudios. |
| `modules/prosodia/audio.py` | Unidade e correção manual. Selo de alerta. |
| `modules/prosodia/resumo.py`, `utils/prosodia_summary.py` | Ranking por unidade. |
| `utils/prosodia_overview.py` | Opções e filtro por unidade, usados no Resumo, na lista e na Análise Geral. |
| `utils/prosodia_powerbi_export.py` | Colunas da unidade e a planilha "Temas por áudio". |
| `utils/prosodia_temas.py` | Linha sem ">" como tag aberta, marcador `!`, versão, regra da tag aberta e de Outros no prompt, validação da saída, `VERSAO_CLASSIFICACAO = "2"`. |
| `utils/prosodia_relatorio.py` | `Recorte.unidade` e `mesmo_filtro`. Agregação por linha da taxonomia, subtemas, Outros, candidatos a tag, todas as tags na tabela, coluna Unidades, alertas, Como ler, Sinal emocional, Matriz e Termômetro sem números no corpo, apêndice com os números, verificação nova. |
| `utils/prosodia_prompts.py` | Estrutura com "Para acompanhar", regras de redação, exemplo novo, tarefa com alertas e com o destino de cada achado. |
| `utils/prosodia_geracao.py` | Passa a unidade de cada áudio para a agregação. |
| `modules/prosodia/analise_geral.py` | Filtro por unidade. |
| `modules/prosodia/preparacao.py` | Ajuda do campo de taxonomia com os dois formatos e o `!`. Candidatos a tag nova no lugar da lista de emergentes. |
| `tests/` | Testes da §8. |

## 8. Testes (pytest)

- Unidade calculada: pelo QR, pela correção manual (que vence), sem QR, e QR religado a outra unidade mudando os áudios antigos.
- Importação de planilha: cria, atualiza pelo código e recusa código repetido na mesma organização.
- Isolamento: uma organização não vê nem liga unidades de outra (no padrão de `test_organization_isolation.py`).
- Backfill do código do QR: casa pelo nome e pelo código, deixa vazio o nome ambíguo e não sobrescreve o que já está preenchido.
- Parse da taxonomia: tag aberta, subtema fixo, os dois no mesmo campo, `!` nos dois formatos, tag aberta e subtema da mesma categoria como linha inválida, dois-pontos dentro da definição e a versão mudando com o tipo e o marcador.
- Validação da classificação com tag aberta: tag desconhecida vai para Outros e o subtema conhecido é reaproveitado.
- Agregação por tag: contagem por respondente, subtemas dentro da tag, Outros sem faixa, candidatos a tag a partir de 3 respondentes, todas as tags na tabela.
- Coluna Unidades: "*k* de *m*", "concentrada", respondente em duas unidades, coluna escondida no recorte de uma unidade só.
- Δ com recorte por unidade, e snapshot antigo sem o campo.
- Alertas: uma menção basta, o bloco traz a unidade e a evidência e nunca a fala literal, sem alerta o bloco some.
- Como ler: cada condição liga e desliga o seu item.
- Sinal emocional: cada linha da tabela da §5.2.
- Recomendações: tag em sinal não gera R, leitura exploratória troca a seção, M cita A, e os avisos novos da verificação.
- Exportação: colunas da unidade e a planilha "Temas por áudio".

## 9. Fases

1. **Unidade no banco e no cadastro.** Tabelas, colunas, backfill do código do QR, página Unidades, escolha da unidade no QR e correção na página do áudio.
2. **Unidade nas telas e na Análise Geral.** Lista de Áudios, Resumo, exportação, filtro do recorte, Δ com unidade e coluna Unidades, sobre a tabela da v2.
3. **Taxonomia por tags.** Parse, classificação, Outros, candidatos a tag, todas as tags na tabela, alertas e selo.
4. **Redação do relatório.** Como ler, Sinal emocional, Matriz e Termômetro sem números, Para acompanhar, regras de redação, prompt, exemplo e verificação.
5. **Validação com a Smart Fit.** Cópia de produção, como na validação da v2 (§15 de `SPEC_ANALISE_GERAL.md`), as 16 tags no campo e as unidades cadastradas pela sugestão dos QRs. Gerar o relatório e conferir:
   1. cada ponto do feedback (§1) tem resposta visível no relatório;
   2. nenhuma recomendação sai de tag em sinal nem na leitura exploratória;
   3. o corpo não tem "valência", "ativação", "habitual", "pitch", "loudness" nem números da Matriz e do Termômetro;
   4. toda tag aparece na tabela, e nenhuma frase interpreta a ausência;
   5. os limiares da seção "Como ler" (30 s e 70%) e o critério de "concentrada" dão leituras que fazem sentido na Smart Fit;
   6. os checklists da §15 da v2 continuam passando;
   7. leitura humana: o relatório responde "o que isso significa para a Smart Fit" sem exigir que o leitor interprete métricas de voz?

A Fase 2 não depende da 3: a coluna Unidades e o filtro funcionam na tabela por subtema da v2 e passam para a tabela por tag quando a Fase 3 entrar.

## 10. Fora do escopo

- Classificar o áudio na chegada e avisar alguém de um alerta crítico na hora. O relatório é periódico, e um alerta pode chegar tarde por ele; fica como evolução, depois de definir quem recebe o aviso.
- Tabela tag × unidade completa no relatório: com cerca de 50 respondentes, quase toda célula seria menção isolada.
- Região, regional e formato da unidade.
- Conceito de "onda", com nome e período, no projeto.
- Mudanças na API do WhatsApp (outro repositório): o vínculo QR → unidade fica no banco do dashboard.
- Prompts e páginas das análises individuais.

## 11. O que pedir à Smart Fit

- Conferir a lista de unidades sugerida pelos nomes dos QRs e preencher o código interno, a cidade e a UF de cada uma (a importação por planilha aceita o arquivo deles).
- Dizer onde cabem preço e valor percebido: hoje vão para Outros, porque "Sistemas Financeiro" fala de cobrança indevida, reajuste, multa e cancelamento. O mesmo vale para os candidatos a tag nova que aparecerem.
