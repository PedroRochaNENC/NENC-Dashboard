# Operacao Segura

## Bootstrap inicial

Antes de iniciar a aplicacao pela primeira vez, defina no ambiente do servidor:

```text
NENC_DB_PATH=/caminho/persistente/nenc-insights.db
NENC_BOOTSTRAP_ORGANIZATION=Organizacao inicial
NENC_BOOTSTRAP_NAME=Nome do administrador
NENC_BOOTSTRAP_EMAIL=admin@example.com
NENC_BOOTSTRAP_PHONE=5511999999999
NENC_BOOTSTRAP_PASSWORD=uma-senha-com-pelo-menos-12-caracteres
```

Inicie o Streamlit e entre com essa conta. O bootstrap so cria a primeira conta
global quando ainda nao existe um administrador global. Remova a senha de
bootstrap do ambiente apos a inicializacao.

## Migracao de banco legado

Pare todas as instancias do Streamlit que usam o banco antes da migracao. A
organizacao proprietaria precisa existir e estar ativa no mesmo SQLite.

Primeiro execute a verificacao sem alterar o banco:

```powershell
py scripts/migrate_legacy_data.py `
  --database C:\dados\nenc-insights.db `
  --organization-id 123
```

Depois aplique a migracao. O comando cria uma copia do banco antes de alterar
qualquer tabela:

```powershell
py scripts/migrate_legacy_data.py `
  --database C:\dados\nenc-insights.db `
  --organization-id 123 `
  --backup-dir C:\dados\backups `
  --apply
```

O comando falha se a organizacao nao existir, estiver inativa ou se a verificacao
final encontrar registros sem organizacao. Em uma falha, restaure o arquivo criado
em `--backup-dir` antes de investigar ou repetir a operacao.

Recursos remotos legados nao sao adotados pelo script: um administrador global
deve selecionar a organizacao proprietaria e registralos na tela de configuracao
da API WhatsApp. Vector stores legados devem ser adotados somente com
`NENC_LEGACY_ORGANIZATION_ID` apontando para a mesma organizacao.

## Base de conhecimento: atributos e limpeza

A analise filtra a base pelo projeto aberto: cada documento carrega `escopo`
(`referencia`, `projeto` ou `analise`) e, quando e material de projeto,
`project_id`. Documento sem atributo nao casa com filtro nenhum e some da busca,
entao o acervo anterior precisa ser carimbado **antes** de o filtro entrar em
producao. Confira o dry-run e so entao aplique:

```powershell
py scripts/backfill_kb_attributes.py --database C:\dados\nenc-insights.db
py scripts/backfill_kb_attributes.py --database C:\dados\nenc-insights.db --apply
```

O relatorio marca com `SEM PROJETO` o material que parece de projeto mas nao tem
projeto correspondente no banco: ele fica fora de qualquer busca filtrada ate ser
removido.

Apagar projeto ou audio ja remove os documentos correspondentes da OpenAI. Para o
que ficou para tras — inclusive vector stores de projetos do Teste Sensorial ja
excluidos — rode a limpeza, tambem em dry-run primeiro:

```powershell
py scripts/cleanup_orphan_kb_files.py --database C:\dados\nenc-insights.db
py scripts/cleanup_orphan_kb_files.py --database C:\dados\nenc-insights.db --apply
```

Acrescente `--apagar-stores` ao `--apply` para apagar tambem os vector stores sem
dono no banco. Transcricao de entrevista e dado pessoal: esta limpeza e o que
garante que ela sai da OpenAI quando sai do banco.

Os documentos de projeto da Jornada de Compra levam tambem `modulo =
jornada_compra`, e a busca da Jornada exige esse modulo para material de
projeto: os ids de projeto da Prosodia e da Jornada vem de tabelas diferentes e
podem coincidir. Briefings e analises da Jornada entram na base com os prefixos
`briefing_jc_` e `analise_geral_jc_`; a analise so vai para a base quando alguem
clica em "Enviar para a base", e sai de la quando e excluida.

O Teste Sensorial segue a mesma regra, com a base propria do modulo (uma por
organizacao, configurada na pagina Base de Conhecimento do Teste Sensorial):
material de projeto leva `modulo = teste_sensorial`, e briefings e analises
entram com os prefixos `briefing_ts_` e `analise_geral_ts_`. Excluir um projeto
apaga da base os documentos dele.

## Jornada de Compra: videos, espaco e cache

Os videos das gravacoes (`-out.mp4`) nao entram no SQLite. Ficam em
`NENC_MEDIA_DIR`; sem a variavel, em `jornada_media/` ao lado do arquivo de
`NENC_DB_PATH` — em producao, dentro do volume `./data`. A estrutura e
`<organizacao>/<projeto>/<sha256>.mp4`, e a tabela `jc_media` guarda o caminho
relativo. So o projeto 1060 soma cerca de 1,6 GB de video: acompanhe o espaco do
volume antes de subir um estudo novo.

- O limite por video e o `server.maxUploadSize` do `.streamlit/config.toml`
  (200 MB); o maior video do 1060 tem 120 MB.
- Para tocar um video, a pagina copia o arquivo para `static/video_<token>.mp4`,
  servido com suporte a range; as copias expiram em 2 horas e sao apagadas na
  proxima reproducao. Nada disso vai para o Git nem para a imagem Docker.
- Excluir um video ou um projeto apaga os arquivos (o banco e a verdade; se o
  disco falhar, o arquivo orfao pode ser removido a mao).

A Analise Geral guarda modelo e metricas em `st.cache_data` com a chave
(projeto, organizacao, `data_version`). Toda escrita que muda os dados sobe a
versao na mesma transacao, entao o cache nao precisa ser limpo por operacao. Em
desenvolvimento, mudar o codigo das metricas sem reiniciar o Streamlit mantem
os numeros antigos em cache: reinicie o app.

Videos de heatmap ficam no mesmo lugar, com `jc_media.kind = 'heatmap'`; os que
chegam compactados pelo script guardam em `source_sha256` o hash do original,
para um reenvio da pasta nao subir o mesmo video de novo.

## Teste Sensorial: arquivos, espaco e cache

As tabelas do pipeline nao entram no SQLite. Ficam em `NENC_SENSORIAL_DIR`; sem
a variavel, em `sensorial_data/` ao lado do arquivo de `NENC_DB_PATH` — em
producao, dentro do volume `./data`:

- `<organizacao>/<projeto>/orig/<sha256><extensao>`: o arquivo como chegou
  (tabelas grandes comprimidas pelo script);
- `<organizacao>/<projeto>/tab/<sha256>.v<versao>.parquet`: a tabela lida, sem
  as colunas de nome, com as bandas do PSD em float32. A `<versao>` e a do
  leitor que gerou a tabela (`sensorial_ingest.PARSER_VERSION`).

A tabela `sens_files` guarda os caminhos relativos e o hash. Um estudo completo
(cerca de 35 participantes, 5 condicoes) ocupa ~125 MB: ~80 MB de originais (o
PSD por janela comprimido e a maior parte) e ~45 MB de Parquet. Excluir um
arquivo ou um projeto apaga o que ele tinha no disco.

O modelo e as metricas ficam em `st.cache_data` com a chave (projeto,
organizacao, `data_version`), como na Jornada; a primeira montagem de um estudo
completo leva cerca de 2 s. A estatistica usa `scipy` (Wilcoxon pareado) e a
leitura usa `pyarrow`, os dois no `requirements.txt` e na imagem.

As tabelas antigas `ts_*` e o estado por organizacao da versao anterior do
modulo ficam intocados no banco e nao sao lidos: os estudos antigos voltam
reenviando a pasta pelo script.

## Importacao da pasta (API): Jornada e Teste Sensorial

O script `scripts/nenc_enviar.py --modulo {jornada_compra,teste_sensorial}`
(o `jornada_enviar.py` e um atalho para a Jornada) envia a pasta de um projeto
para o servico `nenc-import-api` (FastAPI, mesma imagem e mesmo banco do
dashboard), atras do Caddy. Rotas: `/api/importacao/jornada_compra/*` e
`/api/importacao/teste_sensorial/*`, mais o caminho antigo `/api/jornada/*`
(Jornada), mantido para scripts de versoes anteriores. Os dois modulos usam
tabelas de importacao proprias (`jc_import_*` e `sens_import_*`), entao
projetos com o mesmo id nos dois modulos nunca se cruzam.

A API so cria importacoes pendentes: quem grava nos dados do projeto e a
revisao em Uploads, com a conta de quem revisou. No `audit_log`,
`<modulo>.import.received`, `.resumed` e `.closed` ficam sem autor (vieram pelo
token); `.apply` e `.discard` levam o revisor (`jornada.*` e `sensorial.*`).

### Token por organizacao

- Cada organizacao que envia tem um token no `.env` do servidor:
  `NENC_IMPORT_TOKEN_<id da organizacao>=<token>`, com ao menos 32 caracteres
  (mais curto e ignorado). Gere com
  `python -c "import secrets; print(secrets.token_urlsafe(36))"`.
- O token so enxerga os projetos da propria organizacao e so cria pendencias.
  Entregue-o por canal seguro; no computador de quem envia ele fica num arquivo
  (`--token-arquivo`) ou em `NENC_IMPORT_TOKEN`, nunca no Git nem na linha de
  comando.
- Rotacao (alguem saiu da equipe, suspeita de vazamento): troque o valor no
  `.env` e recrie so a API, `docker compose up -d nenc-import-api`. O token
  antigo deixa de valer na hora; um envio em andamento falha com 401 e continua
  de onde parou quando rodado de novo com o token novo.
- O mesmo token vale para os dois modulos da organizacao.
- A documentacao automatica do FastAPI (`/docs`, `/openapi.json`) fica
  desligada; o `GET .../health` de cada prefixo e a unica rota sem token.

### Inbox, limites e expiracao

- O que chega fica em `NENC_IMPORT_INBOX` ou, sem a variavel, em
  `jornada_inbox/` ao lado de `NENC_DB_PATH` (em producao, no volume `./data`):
  `<organizacao>/<projeto>/<lote>/<arquivo>.bin` para a Jornada e a mesma
  estrutura dentro de `teste_sensorial/` para o Teste Sensorial. O caminho
  enviado e so metadado e nunca vira caminho no disco.
- Blocos de ate 8 MB (o Caddy aceita ate 10 MB por requisicao); ate 10 GB por
  lote. Jornada: arquivo de ate 25 MB, video de ate 500 MB, texto de documento
  de ate 5 MB. Teste Sensorial: PSD por janela ate 1 GB, indicadores e
  metricas dos perifericos ate 512 MB, chaves da BASE LIMPA ate 256 MB,
  documentos e literatura ate 100 MB, imagens ate 25 MB (o script comprime as
  tabelas acima de 5 MB; o PSD de um estudo completo chega com ~65 MB). O
  sha256 de cada arquivo e conferido no fim.
- Lote parado ha mais de `NENC_IMPORT_TTL_DAYS` dias (30 por padrao) vira
  `expirada` e perde os arquivos. Gravar ou descartar apaga o inbox do lote, e
  excluir o projeto apaga o inbox dele.
- Envio interrompido retoma o mesmo lote (ele fica "ainda recebendo" em
  Uploads). Lote fechado sem nenhum arquivo novo e descartado sozinho, em vez de
  virar pendencia vazia.
- Espaco: o 1060 completo ocupa cerca de 3 GB no inbox ate a revisao (videos ja
  compactados) e o mesmo em `jornada_media` depois de gravado — os videos sao
  movidos do inbox, nao copiados. Um Teste Sensorial completo ocupa ~80 MB no
  inbox ate a revisao.
- A gravacao de um Teste Sensorial completo le o PSD inteiro e leva cerca de
  40 s; a revisao em Uploads espera ate o fim.

### SQLite com dois processos

O dashboard e a API escrevem no mesmo arquivo. As conexoes usam `timeout=30`,
esperando pelo lock em vez de falhar com "database is locked", e o
recebimento de cada bloco toma o lock de escrita (`BEGIN IMMEDIATE`) enquanto
anexa ao arquivo do inbox, para dois blocos do mesmo arquivo nunca se
cruzarem. Com o banco em disco local (o volume `./data`), nao ha outro ajuste.

## Historico de analises

Cada geracao de analise insere uma linha nova, com o texto inteiro, e nada e
removido: o historico de um audio ou de um projeto cresce sem limite. A poda
mantem as N mais recentes de cada dono e nunca remove a ultima de ninguem.

```powershell
py scripts/prune_analysis_history.py --database C:\dados\nenc-insights.db
py scripts/prune_analysis_history.py --database C:\dados\nenc-insights.db --keep 10 --apply
```

Faca a copia do banco antes do `--apply`: o texto das analises antigas nao tem
outra copia.

## Sentimento do texto e alinhamento da fala

A API passou a gravar, para cada trecho da transcricao, uma nota de sentimento
do texto (`text_sentiment`). O dashboard casa cada segmento do VAD com a fala
pelo tempo (antes era pela posicao, e os momentos de alta ativacao citavam a
fala de outro minuto). Ordem de implantacao:

1. Deploy da API com a fila `analysis` ociosa (`/health/queues`) e smoke test
   de um audio curto: `/audios/{id}/status` deve trazer `has_text_sentiment`.
2. Deploy do dashboard. Ele aceita resultado sem `text_sentiment` e os CSVs
   antigos.
3. Em cada projeto, **Atualizar dados da API (sem nova analise)** na pagina
   Audios. O botao so rebaixa o resultado da API, refaz os CSVs e recalcula os
   momentos de maior ativacao: nao reprocessa, nao chama IA e nao refaz a
   verificacao de qualidade. Pode rodar antes do aval do DPO.
4. Os audios antigos so ganham sentimento depois do aval escrito do DPO (e uma
   finalidade nova sobre dados coletados sob a politica anterior): rode o
   `scripts/backfill_sentimento_texto.py` da API em dry-run, depois com
   `--apply --aprovacao-dpo "<referencia>" --limit N`, e repita o passo 3.

As medias por locutor e as tabelas de ativacao mudam depois do passo 3: e
correcao do alinhamento, nao regressao. Verificacoes de qualidade e analises de
IA ja salvas ficam como estavam ate um novo Reverificar ou Regenerar.

## Recuperacao de administrador

O bootstrap nao deve ser reutilizado para recuperar acesso. Um administrador
global existente deve criar ou redefinir a senha de outro administrador pela tela
de Administracao. Se nao houver nenhum administrador global ativo, a recuperacao
e uma operacao de emergencia:

1. Pare a aplicacao e crie uma copia verificada do SQLite.
2. Use um procedimento administrativo controlado para reativar ou promover uma
   conta existente; registre o motivo, o operador e o horario no processo de
   operacao da plataforma.
3. Inicie a aplicacao, entre com a conta recuperada e redefina senhas/revogue
   sessoes comprometidas.
4. Revise o `audit_log` e mantenha a copia anterior ate concluir a revisao.

Nao altere hashes de senha manualmente nem recoloque credenciais no banco.

## Backup e restauracao

Mantenha `NENC_DB_PATH` em um volume persistente e faca backups consistentes do
SQLite com a aplicacao parada ou usando o mecanismo de backup SQLite do ambiente
de hospedagem. Teste a restauracao periodicamente em uma copia isolada. O banco
inclui usuarios, sessoes, auditoria, dados de Prosodia e da Jornada de Compra
(arquivos enviados, participantes, decisoes sobre gravacoes e analises), estado
dos modulos e identificadores de recursos externos.

Os videos da Jornada ficam fora do banco (`NENC_MEDIA_DIR`, ver acima): o backup
precisa levar a pasta `jornada_media` junto com o SQLite, no mesmo momento, para
o indice `jc_media` continuar apontando para arquivos que existem.

As tabelas e os originais do Teste Sensorial tambem ficam fora do banco
(`NENC_SENSORIAL_DIR`): o backup leva a pasta `sensorial_data` junto com o
SQLite, no mesmo momento, pelo mesmo motivo. Sem ela, os projetos abrem sem
dados e precisam ser reenviados.

O inbox das importacoes (`jornada_inbox`) guarda so envios ainda nao revisados.
Pode ficar fora do backup: depois de restaurar sem ele, os lotes pendentes
aparecem em Uploads sem os arquivos — descarte-os e peca o reenvio da pasta.

## Teste Sensorial: implantacao

1. Deploy de `nenc-dashboard` e `nenc-import-api` (a imagem nova traz `scipy` e
   `pyarrow`).
2. Recrie o Caddy, que le o `Caddyfile` montado como arquivo unico:
   `docker compose up -d --force-recreate --no-deps caddy`. Sem isso,
   `/api/importacao/*` cai no dashboard.
3. Confira `GET https://insights.nenc.in/api/importacao/teste_sensorial/health`
   e `GET https://insights.nenc.in/api/jornada/health`, e abra um projeto da
   Jornada para ver que nada mudou.
4. Envie primeiro uma pasta pequena de teste; so depois um estudo completo.

## Roteiro de aceitacao manual

Execute estes testes em uma base nao produtiva apos cada deploy relevante:

1. Entre como usuario regular com apenas um modulo e confirme que as demais
   paginas, inclusive URLs diretas, exibem bloqueio de autorizacao.
2. Entre como administrador de organizacao e crie, desative, redefina senha e
   reduza permissoes de um usuario da propria organizacao. Confirme que sessoes
   antigas deixam de funcionar na proxima execucao de pagina.
3. Entre como administrador global, troque a organizacao ativa e confirme que
   projetos, estados de modulo, vector stores e recursos WhatsApp mudam junto.
4. Em cada organizacao, crie um projeto e um recurso remoto de teste. Confirme
   que ele nao aparece nem pode ser solicitado quando a outra organizacao esta
   ativa.
5. Confirme que somente o administrador global pode abrir a configuracao de
   WhatsApp e que as credenciais nao aparecem em telas de usuarios comuns.
6. Com dois projetos na mesma organizacao, gere a analise de um deles com a base
   ligada e confirme, na aba Referencias, que nenhum documento do outro projeto
   aparece.
7. Exclua um audio de teste e confirme, pela pagina de Base de Conhecimento, que
   a transcricao dele saiu da lista.
8. Jornada de Compra, com conta so de leitura: Uploads e Novo projeto nao
   aparecem no menu, a lista de projetos so oferece Abrir, e a secao IA mostra
   as analises salvas sem Gerar, Excluir, Enviar para a base nem chat. As
   exportacoes continuam disponiveis.
9. Jornada de Compra, com administrador: envie os arquivos de um estudo de
   teste, confira a previa (unidade, tarefa, loja) antes de gravar, baixe PDF,
   PPTX e Excel em Exportar e confirme no `audit_log` as linhas
   `jornada.export.pdf`, `jornada.export.pptx` e `jornada.export.excel`.
10. Importacao da pasta: com o token da organizacao de teste, rode
    `jornada_enviar.py --simular` numa pasta pequena e confira que fotos de
    participantes aparecem em "Fica de fora". Envie de verdade, confirme que o
    lote aparece em Uploads → Importacoes pendentes e nada muda na analise
    antes de Gravar; grave e veja `jornada.import.apply` com o revisor no
    `audit_log`. Rode o envio de novo: o script responde "Nada novo". Um token
    de outra organizacao nao lista o projeto, e um token errado recebe 401.
11. Teste Sensorial, com administrador: crie o projeto, rode
    `nenc_enviar.py --modulo teste_sensorial --simular` e confira que
    recrutamento, fotos e as copias 2.0/2.1 aparecem em "Fica de fora" sem nome
    de arquivo. Envie, grave em Uploads e confira em Participantes que so
    aparecem codigos (P01, P02...). Baixe PDF, PPTX e Excel em Exportar e veja
    `sensorial.export.pdf`, `.pptx` e `.excel` no `audit_log`.
12. Teste Sensorial, com conta so de leitura: Uploads e Novo projeto nao
    aparecem, Participantes nao deixa decidir sobre sessoes, a secao IA so mostra
    as analises salvas e as exportacoes continuam disponiveis.
