# NENC Insights

Dashboard Streamlit para visualização de dados de Neuromarketing do pipeline NENC.

## Funcionalidades

- **Timeline Sincronizada**: Gráficos de EEG (engagement, atenção, WTP, memória, assimetria) e periféricos (BPM, GSR) alinhados no tempo por participante
- **Média Geral**: Médias por Etapa entre todos os participantes
- **Dados Brutos**: Explorador interativo dos dados tabulares com filtros e download

## Instalação

```bash
pip install -r requirements.txt
```

## Uso

```bash
streamlit run app.py
```

### Formato dos dados

O dashboard espera os arquivos de saída do pipeline NENC:

| Arquivo | Descrição | Obrigatório |
|---------|-----------|-------------|
| `indicadores.xlsx` / `.csv` | Indicadores neurofisiológicos (EEG) | Sim |
| `perifericos_metrics.csv` / `.xlsx` | Métricas de periféricos (BPM, GSR) | Sim |
| `psd_results.xlsx` / `.csv` | PSD bruto por janela temporal | Opcional |

### Modos de carregamento

1. **Upload**: Envie os arquivos diretamente pelo sidebar
2. **Pasta**: Aponte para o diretório `2.2.Dados Processados/` do pipeline

## Deploy

Para deploy no Streamlit Community Cloud:

1. Push deste repositório no GitHub
2. Acesse [share.streamlit.io](https://share.streamlit.io)
3. Conecte o repositório e configure `app.py` como arquivo principal

## Segurança e organizações

O dashboard exige autenticação e isola dados por organização. Contas regulares
recebem acesso explícito a cada módulo; administradores da organização recebem
todos os módulos da própria organização; administradores globais podem trocar a
organização ativa e administrar a plataforma.

Senhas, chaves de API e tokens de sessão nunca devem ser versionados. Defina os
segredos no ambiente de implantação ou em um `.env` ignorado pelo Git.

### Banco de dados

Por padrão, autenticação e dados de Prosódia usam `prosodia.db` dentro de
`nenc-dashboard`. Em produção, configure um caminho persistente e acessível ao
processo com:

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

## Exportação NencLex para Power BI

Na tela **Análise Geral** de um projeto NencLex, use **Exportar para Power BI
(.xlsx)**. O arquivo contém todo o conteúdo persistido que pertence ao projeto
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
