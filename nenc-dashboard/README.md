# NENC Insights

Dashboard Streamlit para visualização de dados de Neuromarketing do pipeline NENC.

## Funcionalidades

- **Teste Sensorial**: testes de fragrância e produto organizados em projetos — EEG, periféricos e teste de
  associação de claims calculados a partir das saídas do pipeline, com estatística pareada, síntese por
  amostra e claim, análise por IA e exportação em PDF, PPTX e Excel/Power BI (ver
  [Teste Sensorial](#teste-sensorial))
- **Jornada de Compra**: eye tracking no ponto de venda, organizado em projetos — atenção por marca, produto, preço e embalagem, com análise por IA e exportação em PDF, PPTX e Excel/Power BI (ver [Jornada de Compra](#jornada-de-compra))
- **NencBoost**: prosódia e transcrições de entrevistas, com análise por IA

## Instalação

```bash
pip install -r requirements.txt
```

## Uso

```bash
streamlit run app.py
```

## Jornada de Compra

Cada estudo é um projeto: **Projetos → Uploads → Participantes → Análise
Geral**. Quem só consulta vê projetos, participantes, análises salvas e
exportações; criar projeto, enviar arquivos, excluir gravações e gerar análise
de IA exigem conta de administrador.

### Arquivos aceitos em Uploads

O tipo é reconhecido pelo nome e pelo cabeçalho; a prévia mostra o que foi
entendido (participante, tarefa, loja, unidade) antes de gravar, e o que o nome
não diz é completado ali.

| Tipo | O que é | Exemplo de nome |
| --- | --- | --- |
| Quadros do rastreador | `frame,timestamp,x,y` de uma gravação (Kexxu) | `Pt04-JEstimulada-ASSAI.csv` |
| Blickshift individual | Gaze Statistics participante × AOI (`;`, decimal com vírgula) | `DSP2250-INDIVIDUAL2.csv` |
| Blickshift agregado | O mesmo export por grupo | `..._Gaze Statistics_PERFIL 1.csv` |
| Planilha enriquecida | Colunas do Blickshift mais canal, perfil e o "Tempo" da planilha | `...-todos.xlsx` |
| Registro de campo | Abas Controle e Estimuladas: produto escolhido, marcas consideradas, tempo de compra, observações | `Relação Coletas.xlsx` |
| Entrevistas | `arquivo, ep, identificacao, texto` | `entrevistas.csv` |
| Formato antigo | `Banco_Tabelas` da versão anterior do módulo | `Banco_Tabelas.csv` |
| Imagem | Foto de gôndola (com a loja), heatmap ou embalagem (com a marca) | `.png`, `.jpg` |

As tabelas `Banco_PorMarca`, `Banco_medias`, `Banco_TBVisualShare`,
`Banco_ANOVA` e `Banco_Consolidado` da versão anterior são recusadas com
orientação: o app calcula esses números a partir do `Banco_Tabelas`. Tabelas
gravadas pela versão anterior aparecem, só para consulta, em **Dados do
Projeto → Versão anterior**, com o `Banco_Tabelas` pronto para baixar e reenviar.

Vídeos (`.mp4`, `.mov`, até 200 MB) têm seção própria em Uploads e ficam no
disco do servidor, fora do banco — ver [docs/OPERATIONS.md](docs/OPERATIONS.md).
Cada gravação pode ter o vídeo de cena (`-out.mp4`) e o de heatmap; o player de
Participantes troca entre os dois.

### Enviar a pasta inteira do projeto

Para um estudo completo, o script `scripts/jornada_enviar.py` lê a pasta do
projeto no computador de quem a tem (ex.: `X:\ALS\1234-Estudo`) e envia de uma
vez exports, quadros, vídeos de cena e de heatmap, fotos, registro de campo e o
texto do relatório. Nada entra direto na análise: o envio aparece em **Uploads →
Importações pendentes**, onde alguém com escrita confere a prévia (inclusive as
escolhas como a análise vai lê-las) e grava ou descarta.

1. Crie o projeto no app, com as marcas. O script não cria projeto e para se
   não achar um com o nome da pasta (ou o de `--projeto`).
2. Peça o token da organização a quem administra o servidor e guarde-o num
   arquivo fora do repositório, ou na variável `NENC_IMPORT_TOKEN`. Nunca o
   coloque na linha de comando nem no Git.
3. Instale o [ffmpeg](https://ffmpeg.org/download.html) e deixe-o no `PATH`
   (ou passe `--ffmpeg C:\caminho\ffmpeg.exe`): os vídeos vão compactados.
4. Dentro de `nenc-dashboard`, com a `.venv` do repositório, simule e envie
   (`scripts/nenc_enviar.py --modulo jornada_compra` faz o mesmo; o
   `jornada_enviar.py` continua como atalho):

```powershell
..\.venv\Scripts\python scripts\jornada_enviar.py "X:\ALS\1234-Estudo" --simular
..\.venv\Scripts\python scripts\jornada_enviar.py "X:\ALS\1234-Estudo" --token-arquivo C:\nenc\token.txt
```

- `--simular` mostra o que vai e o que fica de fora, com o motivo, sem rede e
  sem token. Confira antes do primeiro envio de um projeto.
- Rodar de novo envia só o que o projeto ainda não tem; um envio interrompido
  continua de onde parou. Os hashes e os vídeos compactados ficam em cache em
  `%LOCALAPPDATA%\nenc\jornada_cache`.
- Vídeos: H.264 até 720p, com a mesma linha do tempo (o salto para a primeira
  olhada continua certo); no 1060, cerca de 35% menores. Sem ffmpeg, vão os
  originais, até 500 MB cada. `--sem-heatmap`, `--sem-videos` e `--sem-compactar`
  reduzem o envio.
- Documentos: vai só o texto do relatório mais recente (a maior versão `V<n>`)
  e dos documentos preenchidos; modelos vazios ficam de fora. Na revisão, o
  briefing preenchido vira o Contexto do projeto se ele estiver vazio, e o
  texto pode ir para a base de conhecimento da Jornada.
- **Nunca saem do computador**: fotos de participantes e planilhas de
  recrutamento (dado pessoal), backups dos dados originais, cópias de trabalho,
  arquivos do Blickshift e travas do Office.

Estrutura reconhecida (nomes comparados sem acento e sem caixa):

| Caminho | Vai como |
| --- | --- |
| `2.DADOS/2.3*/**` (`.csv`, `.xlsx`; sem os `-convertido`) | exports de eye tracking |
| `2.DADOS/2.2*/Eyetracking/**.csv` e `**-out.mp4` | quadros e vídeos de cena |
| `2.DADOS/2.2*/Videos Processados Heatmap/**.mp4` | vídeos de heatmap |
| `**/heatmap-<loja>.png` | heatmap da loja |
| `4.ARQUIVOS AUXILIARES/Fotos Gôndolas/<loja>/*` | fotos de gôndola |
| `4.ARQUIVOS AUXILIARES/Fotos pacotes/<marca>/*` | fotos de embalagem (vista e "editada" pelo nome) |
| `**/Relação Coletas*.xlsx` | registro de campo |
| `1.GESTAO_PROJETOS/1.1*/*` e o fluxo experimental | briefing (texto) |
| `3.DRAFTS RELATÓRIOS/3.2*/*V<n>.pptx` | relatório (texto da versão mais recente) |

Pasta fora do padrão? Um `jornada_import.toml` opcional na raiz do projeto
acrescenta pastas, nomes de loja e de marca e exclusões:

```toml
ignorar = ["**/rascunho*"]

[lojas]          # subpasta ou rótulo -> código da loja
"DSP-2250" = "2250"

[marcas]         # subpasta de fotos de embalagem -> marca
"GL" = "Gama Livre"

[pastas]         # pastas extras por papel, relativas à raiz
dados = ["2.DADOS/Outros exports"]
fotos_gondola = ["Fotos lojas"]
```

Papéis aceitos em `[pastas]`: `dados`, `quadros`, `video_cena`, `video_heatmap`,
`fotos_gondola`, `fotos_embalagem`, `registro_campo` e `documento`.

### Unidades e o que entra na análise

- A unidade de tempo é decidida **por gravação**: exports em amostras são
  convertidos para segundos pelos quadros daquela gravação, e o tempo até o
  primeiro olhar usa o timestamp do quadro. A unidade pode ser fixada à mão.
- Só entram as gravações **incluídas**. Gravação com todas as AOIs zeradas é
  marcada como não codificada e fica fora de qualquer denominador; a exclusão
  manual exige motivo.
- Contagem de fixações, sacadas, pupila e medidas em pixels não são usadas: a
  ~23 Hz o rastreador não separa fixações.

### Análise Geral

Filtros por tarefa, loja e perfil valem para todas as seções. Cada gráfico tem
a tabela equivalente.

- **Gôndola**: share visual por marca (média por participante; a ponderada
  pelo tempo fica na tabela), funil notou → examinou → retornou, primeira marca
  notada, tempo até a primeira olhada (absoluto e relativo à primeira marca
  vista), índice de presença (share ÷ fração da gôndola), ranking de produtos e
  a foto e o heatmap da loja de cada célula.
- **Navegação e decisão**: atributos das AOIs (ex.: Diurno × Noturno) com a
  presença de cada valor na gôndola, etiquetas de preço e tempo até a decisão
  por tarefa e fonte — o tempo de compra do registro de campo e o "Tempo" da
  planilha enriquecida aparecem separados, cada um na sua tarefa.
- **Escolha** (com registro de campo): marca escolhida por tarefa, loja, canal e
  perfil, com a contagem ("4/6"); variante; da atenção à escolha (a marca
  escolhida foi notada, examinada, a primeira, a mais vista?); conjunto
  considerado e embalagens citadas; tempo de compra. Na jornada livre a escolha
  é a compra observada; o texto da equipe é normalizado pelas marcas do projeto.
- **Embalagens**: elementos por perfil (dados agregados), com o alcance do logo
  e as fotos de cada marca (a versão editada primeiro).
- **Canal e perfil**: comparações com δ de Cliff e permutação exata quando cada
  grupo tem 5 ou mais participantes; abaixo disso, descritivo. Variáveis que
  andam juntas na amostra (ex.: canal = tarefa) são avisadas e não são
  comparadas como causa.
- **IA**: relatório rápido ou aprofundado (leitura estatística + estratégica),
  histórico com aviso quando os dados mudaram, chat sobre a análise e envio à
  base de conhecimento só por clique. A IA recebe as métricas calculadas, nunca
  os dados brutos.
- **Exportar**: PDF (relatório), PPTX (gráficos nativos editáveis) e Excel para
  Power BI, sempre no recorte da página; o PDF e o PPTX levam a seção Escolha e
  uma imagem por loja e por marca. Cada download vai para o `audit_log`.

### Excel para Power BI

Uma aba por tabela, em formato longo. As tabelas de dados (`Gravacoes`,
`Olhar_AOI`, `Olhar_AOI_bruto`, `Catalogo_AOI`, `Agregados_Grupo`) vão
completas, com a situação de cada gravação; as de métricas seguem o recorte,
descrito na aba `Projeto`. A aba `Dicionario` explica cada coluna.

```text
Participantes[participant] ── Gravacoes[participant]
Lojas[store] ──────────────── Gravacoes[store]
Gravacoes[recording_key]
  ├── Olhar_AOI[recording_key]
  ├── Olhar_AOI_bruto[recording_key]
  ├── Marca_por_Gravacao[recording_key]
  ├── Resumo_Gravacao[recording_key]
  ├── Escolhas[recording_key]
  └── Qualidade[recording_key]
Participantes[participant] ── Tempos[participant]
Catalogo_AOI[aoi_key]
  ├── Olhar_AOI[aoi_key]
  └── Agregados_Grupo[aoi_key]
Analises_IA[id] ── Citacoes[analysis_id]
```

### Recomendações para os próximos estudos

- Exportar sempre em segundos, ou registrar a unidade de cada export.
- Codificar ou excluir explicitamente cada gravação, sem deixar linhas zeradas.
- Exportar Embalagens também por participante, e as sequências de AOI.
- Não editar os agregados `TODOS` à mão.
- Padronizar nomes: `DSP`/`DGSP`, `JEstimulada`/`Estimulada`, `Assai`/`ASSAI`.
- Documentar o "Tempo" e o alvo da tarefa estimulada, e registrar os facings
  por marca e loja (o índice de presença usa o número de AOIs como aproximação).
- Separar canal e tarefa no desenho: no 1060 eles coincidem e a comparação
  entre canais não isola nenhum dos dois.
- Conferir a taxa do rastreador antes de cada sessão (houve gravação a ~13 Hz).

## Teste Sensorial

Cada estudo é um projeto: **Projetos → Dados do Projeto → Uploads →
Participantes → Análise Geral e Sinais**. Quem só consulta vê projetos,
participantes, análises salvas e exportações; criar projeto, enviar arquivos,
decidir sobre sessões e gerar análise de IA exigem escrita.

A fonte são as saídas do pipeline (`2.2 Dados Processados`): o app calcula os
índices do relatório a partir da potência por janela (as fórmulas da sintaxe
SPSS), as médias, as comparações e o teste de associação. Do `2.3` só entram as
chaves da aba **BASE LIMPA** de cada camada (EEG, periféricos, teste de
associação), que decidem quais sessões, janelas e tentativas ficam. O resto do
2.3 é resultado do SPSS, que o app recalcula.

**Nome de participante nunca aparece.** O app mostra, exporta e manda à IA só
o código (`P07`, tirado do número no início de `participante`); as colunas com
nome são descartadas na leitura e o texto livre passa por um filtro.
Recrutamento, fotos e vídeos das coletas nem saem do computador de quem envia.

### Enviar a pasta do projeto

```powershell
..\.venv\Scripts\python scripts\nenc_enviar.py "X:\Cliente\1234 - Estudo" --modulo teste_sensorial --simular
..\.venv\Scripts\python scripts\nenc_enviar.py "X:\Cliente\1234 - Estudo" --modulo teste_sensorial --token-arquivo C:\nenc\token.txt
```

O projeto precisa existir no app (o script procura pelo nome da pasta ou por
`--projeto`), e o token é o mesmo da Jornada, guardado num arquivo fora do
repositório. Como na Jornada, nada entra direto: o envio vira uma importação
pendente em **Uploads**, onde a prévia mostra só resumos (papel, rodada,
sessões, códigos, etapas, condições, cobertura da BASE LIMPA e avisos) antes de
gravar.

- Vai só a rodada mais nova (`run_<data>`) de cada modalidade que não falhou.
- Tabelas acima de 5 MB vão em gzip (o PSD por janela cai de ~170 MB para
  ~65 MB); a chave continua sendo o hash do original, então reenviar a pasta
  manda só o que é novo. Cache em `%LOCALAPPDATA%\nenc\sensorial_cache`.
- A planilha do 2.3 (de ~170 MB) não sai inteira: o script lê em streaming só
  as colunas-chave da BASE LIMPA, sem tocar nas colunas de nome.
- **Nunca saem do computador**: recrutamento, fotos de participantes, registros
  e vídeos das coletas, as cópias dos dados brutos (2.0 e 2.1), áudio,
  transcrição e prosódia. A lista "Fica de fora" agrupa por pasta e motivo, sem
  nome de arquivo.

| Caminho | Vai como |
| --- | --- |
| `2.DADOS/2.2*/<modalidade>/run_*/` | indicadores, PSD por janela e médio, qualidade, topomapas, métricas e qualidade dos periféricos, tentativas do teste de associação, manifesto |
| `2.DADOS/2.2*/_inventarios/` | inventário mais recente das sessões |
| `2.DADOS/2.3*/**` com a aba BASE LIMPA | chaves da BASE LIMPA (uma por camada) |
| `1.GESTAO_PROJETOS/1.4*/` | registro de campo da qualidade do sinal |
| `1.GESTAO_PROJETOS/1.1*/` e o relatório final mais recente | documentos |
| `1.2 Estímulos`, `6.ESTIMULOS` / `Artigos` | estímulos / literatura |

Pasta fora do padrão? Um `sensorial_import.toml` opcional na raiz:

```toml
ignorar = ["**/rascunho*"]
aba_base_limpa = "BASE LIMPA"

[pastas]         # pastas extras por papel, relativas à raiz
perfil = ["1.GESTAO_PROJETOS/1.4.Campo/Perfil"]
literatura = ["Referencias"]
```

Papéis aceitos em `[pastas]`: `perfil`, `campo_qualidade`, `documento`,
`literatura` e `estimulo`. Arquivos pequenos (até 25 MB) também podem ir pela
tela de Uploads.

### O que entra na análise

- **Desenho** (Dados do Projeto): o app deduz as condições (basal, controle,
  amostras) e as etapas pelo experimento e pela amostra das sessões; o
  mapeamento, os nomes de negócio dos índices e os pesos do PPI ficam
  editáveis por projeto. O PPI usa os componentes padronizados (z) por padrão;
  "como o SPSS" fica disponível.
- **Sessões**: sem código de participante ou fora do desenho ficam excluídas;
  depois vale a BASE LIMPA de cada camada; por último a decisão de quem edita,
  em Participantes, sempre com motivo. Repetições aparecem marcadas por camada.
- **Limpeza**: a regra de outliers da sintaxe (ln → z, |z| > 3,29 e Mahalanobis
  p < 0,001) fica desligada por padrão; ligada, o Excel lista as janelas que
  saem.
- **Estatística**: média por participante, condição e etapa; Wilcoxon pareado
  com r de postos e diferença mediana, contra o basal, contra o controle e entre
  amostras. "Diferença" só quando passa na correção de Holm (família = medida ×
  etapa); p bruto abaixo de alfa sem passar no Holm é "tendência"; abaixo de 5
  pares, só descritivo.
- **Teste de associação**: CR = (média da sessão − RT) / DP; Score = % de Sim ×
  CR médio das respostas Sim; faixas ≥ 0,2 Muito alta, > 0 Alta, > −0,1 Baixa,
  abaixo Muito baixa; quadrantes por adesão (50% de Sim) × convicção (CR 0),
  ajustáveis por projeto.
- **Periféricos**: frequência cardíaca, RMSSD, condutância, Emotional_Index e
  Comfort_Score, só nas janelas com sinal bom (ou as da BASE LIMPA).

### Páginas

- **Análise Geral**: Resumo (síntese por amostra e claim, achados,
  limitações), EEG, Periféricos, Associação, Amostra e qualidade, IA e
  Exportar. O recorte e a comparação por perfil valem para todas as seções.
- **Sinais**: curva média por condição, alinhada ao início da etapa ou à
  primeira cheirada, com o basal de referência; e a linha do tempo de uma
  sessão.
- **Participantes**: matriz participante × condição com completude e
  qualidade por camada, janelas que saíram, incluir/excluir com motivo e o
  perfil editável.
- **IA**: igual à Jornada (rápida ou aprofundada, histórico, chat, envio à base
  só por clique), com a base de conhecimento do Teste Sensorial da organização
  filtrada pelo projeto.
- **Exportar**: PDF, PPTX com gráficos nativos e Excel para Power BI, no
  recorte da página; cada download vai para o `audit_log`
  (`sensorial.export.pdf`, `sensorial.export.pptx`, `sensorial.export.excel`).

### Excel para Power BI do Teste Sensorial

Uma aba por tabela. `Janelas_EEG` vai completa (todas as janelas incluídas, com
os índices); as abas de métricas seguem o recorte, descrito na aba `Projeto`. A
aba `Dicionario` explica cada coluna.

```text
Sessoes[sessao_id]
  ├── Janelas_EEG[sessao_id]
  └── Janelas_Fora[sessao_id]
Participantes[participant_code]
  ├── Sessoes[participant_code]
  └── Medias_Participante[participant_code]
Condicoes[codigo] ── Resumo[condicao], Comparacoes[condicao_a], Associacao[condicao]
Analises_IA[id] ── Citacoes_IA[analysis_id]
```

## Deploy

Produção roda em Docker Compose atrás do Caddy, que termina o TLS e publica
`insights.nenc.in`. Nenhuma porta da aplicação é exposta no host: o acesso
entra apenas pelo proxy.

No servidor, a partir do diretório `nenc-dashboard/`:

```bash
git pull origin main
docker compose up -d --build nenc-dashboard nenc-import-api
docker compose logs -f --tail=50 nenc-dashboard
```

Nomear os serviços é proposital: sem isso o compose reconstrói também
`nenc-whatsapp-api`, que vem de outro repositório (`../../whatsapp-api`).

`nenc-import-api` é a API que recebe a pasta enviada pelo `nenc_enviar.py`
(FastAPI, mesma imagem, mesmo banco), para os dois módulos. O Caddy manda
`insights.nenc.in/api/importacao/*` (e o caminho antigo `/api/jornada/*`) para
ela e o resto para o dashboard. O `Caddyfile` é montado como arquivo único:
depois de um `git pull` que o muda, recrie o proxy para ele ler a versão nova
(`docker compose up -d --force-recreate --no-deps caddy`). Tokens, inbox e
expiração estão em [docs/OPERATIONS.md](docs/OPERATIONS.md).

O healthcheck consulta `/_stcore/health` a cada 30s; `docker compose ps`
mostra `healthy` quando a aplicação sobe.

O banco vive no volume `./data:/app/data`, apontado por `NENC_DB_PATH`, e não
é tocado pelo build. `init_db()` aplica o esquema na subida, então mudanças
que apenas acrescentam tabelas ou colunas não exigem passo de migração — as
demais estão em [docs/OPERATIONS.md](docs/OPERATIONS.md).

As rotas das páginas derivam do caminho do arquivo (`/prosodia-entrevistas`,
`/teste-sensorial-preparacao`). Elas mudaram na revisão de interface, então
links salvos para páginas internas de versões anteriores não resolvem mais.

## Segurança e organizações

O dashboard exige autenticação e isola dados por organização. Contas regulares
recebem acesso explícito a cada módulo; administradores da organização recebem
todos os módulos da própria organização; administradores globais podem trocar a
organização ativa e administrar a plataforma.

Senhas, chaves de API e tokens de sessão nunca devem ser versionados. Defina os
segredos no ambiente de implantação ou em um `.env` ignorado pelo Git.

### Banco de dados

Por padrão, autenticação e dados de Prosódia, da Jornada de Compra e do Teste
Sensorial usam `prosodia.db` dentro de `nenc-dashboard`. As tabelas grandes do
Teste Sensorial (Parquet) e os originais enviados ficam fora do banco, em
`NENC_SENSORIAL_DIR` (padrão: `sensorial_data/` ao lado do banco). Em produção,
configure um caminho persistente e acessível ao processo com:

```text
NENC_DB_PATH=/caminho/persistente/nenc-insights.db
```

Todas as instâncias da aplicação devem usar o mesmo valor de `NENC_DB_PATH`.

### Primeiro administrador

Antes do primeiro acesso, configure estas variáveis no ambiente do servidor:

```text
NENC_BOOTSTRAP_ORGANIZATION=Organizacao inicial
NENC_BOOTSTRAP_NAME=Nome do administrador
NENC_BOOTSTRAP_EMAIL=admin@example.com
NENC_BOOTSTRAP_PHONE=5511999999999
NENC_BOOTSTRAP_PASSWORD=uma-senha-com-pelo-menos-12-caracteres
```

O bootstrap cria a organização e o administrador global inicial. Depois do
primeiro login, crie as demais organizações, usuários e permissões na tela de
Administração.

### Migração de dados legados

Dados existentes de Prosódia não são atribuídos automaticamente a uma
organização. Após criar a organização proprietária, configure temporariamente:

```text
NENC_LEGACY_ORGANIZATION_ID=123
```

Inicie a aplicação com esse valor para migrar os registros SQLite e, quando
aplicável, adotar o vector store legado somente para essa organização. Valide os
dados migrados e remova a variável do ambiente.

Recursos antigos da API de WhatsApp seguem a mesma regra: um administrador
global deve selecionar a organização proprietária e registrá-los na tela
`Configuração da WhatsApp API`. A migração só é aceita quando
`NENC_LEGACY_ORGANIZATION_ID` corresponde à organização ativa. Recursos novos
são registrados automaticamente a partir de sua criação ou de uma origem já
pertencente à organização.

O procedimento de backup, verificação e aplicação controlada está em
[docs/OPERATIONS.md](docs/OPERATIONS.md). O script de migração usa `dry-run` por
padrão e só altera o banco com a opção explícita `--apply`.

### WhatsApp e bases de conhecimento

`WHATSAPP_API_URL` e `WHATSAPP_API_KEY` são credenciais globais do servidor e
podem ser alteradas somente por administradores globais. A tela correspondente
grava no mesmo `.env` carregado pela aplicação: primeiro `nenc-dashboard/.env`,
depois o `.env` da raiz do workspace, quando existir.

Vector stores OpenAI são armazenados por organização e módulo. Não defina um
novo `PROSODIA_VECTOR_STORE_ID` ou `VECTOR_STORE_ID` compartilhado para uso
normal; essas variáveis servem apenas como entrada da migração legada acima.

## Exportação NencBoost para Power BI

> **Desativado na interface.** O bloco que expunha o botão em **Análise
> Geral** está comentado em `modules/prosodia/analise_geral.py`. O módulo
> `utils/prosodia_powerbi_export.py` e a suíte
> `tests/test_prosodia_powerbi_export.py` seguem ativos e verificados; para
> reativar, descomente o bloco e o import no topo do arquivo. O restante
> desta seção descreve o comportamento quando ativo.

O arquivo gerado contém todo o conteúdo persistido que pertence ao projeto
da organização ativa, inclusive históricos de análises de IA, verificações de
qualidade, momentos de alta ativação e o conteúdo bruto decodificado de cada
entrevista/audio (JSON de prosódia, CSV de transcrição e CSV sincronizado).
Credenciais e chaves de API nunca são exportadas.
No Power BI Desktop, selecione **Obter Dados > Excel**, escolha o arquivo e
importe todas as abas. As tabelas são normalizadas para que os relacionamentos
sejam criados pelos identificadores numéricos, sem relacionar dados por nomes
ou texto.

### Catálogo de abas

| Aba | Conteúdo e colunas principais |
| --- | --- |
| `Projeto` | Uma linha com `id`, `organization_id`, contexto do estudo, briefing, IDs funcionais de WhatsApp/API, thresholds e criação. |
| `Perguntas_Projeto` | Perguntas do roteiro: `project_id`, `question_index`, `question_text`. |
| `Entrevistas` | Uma linha por áudio: `id`, `project_id`, `organization_id`, sessão, IDs OpenAI, metadados WhatsApp/QR, duração, qualidade, cobertura e contagem de análises. |
| `Segmentos_VAD` | Segmentos de fala por áudio: `audio_id`, `project_id`, `session_id`, `start`, `end`, `duration`. |
| `Transcricoes` | Turnos transcritos: `audio_id`, `project_id`, `session_id`, `SpeakerName`, `Timestamp`, `seconds`, `word_count`, `Text`. |
| `Dados_Sincronizados` | Chaves da entrevista mais todas as colunas originais do CSV sincronizado, inclusive métricas acústicas. |
| `Analises_Entrevista` | Histórico de IA por áudio: `id`, `audio_id`, `project_id`, `model`, `analysis_text`, `created_at`. |
| `Citacoes_Analise_Entrevista` | Citações de análises individuais: `analysis_id`, `audio_id`, `project_id`, índice, arquivo, trecho e contexto disponível. |
| `Analises_Projeto` | Histórico de IA consolidada: `id`, `project_id`, `model`, `analysis_text`, `created_at`. |
| `Citacoes_Analise_Projeto` | Citações de análises consolidadas: `project_analysis_id`, `project_id`, índice, arquivo, trecho e contexto disponível. |
| `Verificacoes_Qualidade` | Histórico mestre: `id`, `audio_id`, `project_id`, `overall_status`, `created_at`. |
| `Checks_Qualidade` | Itens das verificações: `quality_check_id`, `audio_id`, `project_id`, ID/título/categoria/status/mensagem/valor. |
| `Cobertura_Perguntas` | Cobertura por pergunta: `quality_check_id`, `audio_id`, `project_id`, índice/texto, flags de IA/keywords e evidências. |
| `Momentos_Alta_Ativacao` | Histórico de momentos: `high_activation_id`, `audio_id`, `project_id`, índice, tempo, locutor, texto, score, motivo e criação. |
| `Dados_Brutos_Entrevistas` | Resumo do conteúdo bruto por entrevista: `audio_id`, `project_id`, `session_id`, tipo do artefato, nome, tamanho, hash SHA-256, preview, truncamento e quantidade de chunks. |
| `Chunks_Dados_Brutos_Entrevistas` | Chunks do conteúdo bruto para preservar o texto completo de cada artefato: `audio_id`, `project_id`, `session_id`, tipo, nome, índice do chunk e texto. |

As abas filhas também preservam um campo JSON de detalhes quando a origem
contém atributos adicionais, evitando perda de informação de versões futuras
do pipeline.

### Relacionamentos recomendados

```text
Projeto[id]
  ├── Perguntas_Projeto[project_id]
  ├── Entrevistas[project_id]
  │     ├── Segmentos_VAD[audio_id]
  │     ├── Transcricoes[audio_id]
  │     ├── Dados_Sincronizados[audio_id]
  │     ├── Dados_Brutos_Entrevistas[audio_id]
  │     │     └── Chunks_Dados_Brutos_Entrevistas[audio_id]
  │     ├── Analises_Entrevista[audio_id]
  │     │     └── Citacoes_Analise_Entrevista[analysis_id]
  │     ├── Verificacoes_Qualidade[audio_id]
  │     │     ├── Checks_Qualidade[quality_check_id]
  │     │     └── Cobertura_Perguntas[quality_check_id]
  │     └── Momentos_Alta_Ativacao[audio_id]
  └── Analises_Projeto[project_id]
        └── Citacoes_Analise_Projeto[project_analysis_id]
```

Configure todos os relacionamentos como um-para-muitos, do identificador da
tabela pai para a chave estrangeira da tabela filha. Quando uma tabela excede
1.000.000 de linhas, o exportador gera continuações com sufixo numérico, como
`Dados_Sincronizados_2`. No Power Query, anexe essas abas antes de criar os
relacionamentos.
