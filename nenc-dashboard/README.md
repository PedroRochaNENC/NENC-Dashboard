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
