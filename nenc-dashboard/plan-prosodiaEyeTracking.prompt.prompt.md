## Plan: Prosodia e Eye Tracking no NENC Dashboard

Adicionar duas novas páginas Streamlit focadas em visualização sincronizada por Tempo_global com mídia via upload: uma para Prosódia + transcrição e outra para Eye Tracking + vídeo + tabela de fixações. A estratégia reutiliza o pipeline atual (session_state, filtros por participante/etapa, e convenções Tempo/Etapa) sem extração automática de features na V1.

**Steps**
1. Fase 1 - Extensão do carregamento de dados (base comum)
2. Em c:\Users\pcnen\Documents\GitHub\NENC-Dashboard\nenc-dashboard\utils\data_loader.py, incluir novos datasets no fluxo com chaves prosodia e eye_tracking, validação de colunas obrigatórias e carregamento por upload.
3. Em c:\Users\pcnen\Documents\GitHub\NENC-Dashboard\nenc-dashboard\app.py, expandir a sidebar de upload para aceitar: CSV de prosódia, CSV de eye tracking, arquivo de transcrição com timestamps e arquivos de mídia (áudio e vídeo). Dependência: passo 2.
4. Definir convenção mínima V1 para sincronização: filename + Etapa + Tempo em todos os CSVs; transcrição com colunas inicio_s e fim_s (ou Tempo + duracao_s). Dependência: passo 2.
5. Fase 2 - Utilitários de sincronização
6. Em c:\Users\pcnen\Documents\GitHub\NENC-Dashboard\nenc-dashboard\utils\resampler.py, adicionar helpers não destrutivos para filtrar e alinhar dados de prosódia e eye tracking por participante/etapa mantendo Tempo como eixo principal.
7. Em c:\Users\pcnen\Documents\GitHub\NENC-Dashboard\nenc-dashboard\utils\charts.py, adicionar funções para gráficos temporais de prosódia (linhas multi-métricas) e para agregados de fixação (barras por AOI/etapa). Pode rodar em paralelo com passo 6.
8. Fase 3 - Nova página Prosódia
9. Criar pages/3_Prosodia.py com layout em colunas: mídia (vídeo/áudio), gráfico temporal de prosódia e tabela de transcrição sincronizada.
10. Implementar controles: participante, etapa, métricas de prosódia, slider de tempo e botão de playhead manual. Dependência: passos 2 e 6.
11. Exibir transcrição filtrada por tempo corrente (trecho ativo destacado) e tabela completa no expander. Dependência: passo 4.
12. Fase 4 - Nova página Eye Tracking
13. Criar pages/4_Eye_Tracking.py com vídeo principal e painel analítico (tabela de fixações + agregados por AOI e etapa).
14. Implementar modo de visualização por etapa e modo contínuo por tempo, ambos usando os mesmos filtros globais de participante e etapa. Dependência: passos 2 e 6.
15. Incluir tabela de tempos de fixação (campos mínimos: AOI, inicio_s, fim_s, duracao_s, etapa) e sumarização por AOI. Dependência: passo 13.
16. Fase 5 - Integração e robustez
17. Atualizar mensagens de estado sem dados para cada página e validar colunas faltantes com alertas claros no UI.
18. Garantir que uploads adicionais não quebrem páginas atuais Timeline e Média Geral (feature flag por presença de chave no session_state). Dependência: passos 2 a 15.
19. Fase 6 - Documentação e execução local
20. Em c:\Users\pcnen\Documents\GitHub\NENC-Dashboard\nenc-dashboard\README.md, documentar formatos esperados para prosódia, transcrição e eye tracking, além do fluxo de upload de mídia.
21. Em c:\Users\pcnen\Documents\GitHub\NENC-Dashboard\nenc-dashboard\requirements.txt, adicionar apenas dependências realmente usadas na V1 (evitar extras de processamento pesado).

**Relevant files**
- c:\Users\pcnen\Documents\GitHub\NENC-Dashboard\nenc-dashboard\app.py - ampliar upload e resumo para novos datasets.
- c:\Users\pcnen\Documents\GitHub\NENC-Dashboard\nenc-dashboard\utils\data_loader.py - validar e carregar prosodia, eye_tracking e transcrição.
- c:\Users\pcnen\Documents\GitHub\NENC-Dashboard\nenc-dashboard\utils\resampler.py - alinhamento temporal por participante e etapa.
- c:\Users\pcnen\Documents\GitHub\NENC-Dashboard\nenc-dashboard\utils\charts.py - novos gráficos de prosódia e fixação.
- c:\Users\pcnen\Documents\GitHub\NENC-Dashboard\nenc-dashboard\pages\1_Timeline.py - referência de filtros, organização e padrão visual.
- c:\Users\pcnen\Documents\GitHub\NENC-Dashboard\nenc-dashboard\pages\2_Media_Geral.py - referência de tabelas resumo e controles.
- pages/3_Prosodia.py - nova página proposta.
- pages/4_Eye_Tracking.py - nova página proposta.
- c:\Users\pcnen\Documents\GitHub\NENC-Dashboard\nenc-dashboard\README.md - documentação de formatos e uso.
- c:\Users\pcnen\Documents\GitHub\NENC-Dashboard\nenc-dashboard\requirements.txt - dependências da V1.

**Verification**
1. Subir app com streamlit run app.py e validar que as novas páginas aparecem no menu lateral.
2. Testar upload com dados completos (prosódia, eye tracking, transcrição, vídeo e áudio) e confirmar render sem exceções.
3. Validar sincronização por Tempo: ao mover slider, gráfico e transcrição devem refletir o mesmo instante.
4. Validar filtros de participante e etapa em Prosódia e Eye Tracking com mudança imediata dos gráficos e tabelas.
5. Testar cenários de ausência de arquivo (somente prosódia, somente eye tracking, sem transcrição) e confirmar mensagens amigáveis.
6. Regressão rápida nas páginas existentes Timeline e Média Geral para garantir que continuam funcionando com uploads antigos.

**Decisions**
- Incluído: visualização com dados já prontos, upload via Streamlit, sincronização por tempo com playhead único.
- Incluído: Prosódia com vídeo/áudio + transcrição com timestamps.
- Incluído: Eye Tracking com vídeo e tabela de tempos de fixação.
- Excluído da V1: extração automática de prosódia a partir de áudio e processamento avançado de gaze em tempo real.
- Excluído da V1: armazenamento persistente de mídia em backend; operação apenas em sessão atual.

**Further Considerations**
1. Priorizar volume de dados: limitar tamanho máximo de upload de vídeo para manter responsividade do Streamlit.
2. Definir padrão único de timestamps da transcrição (recomendado: inicio_s e fim_s em segundos).
3. Se houver arquivos muito grandes, considerar modo alternativo por caminho local além de upload na fase seguinte.
