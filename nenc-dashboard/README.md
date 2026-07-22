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
