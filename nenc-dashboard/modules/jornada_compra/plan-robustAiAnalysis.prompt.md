# Plan: Robust AI Analysis with OpenAI Knowledge Base for Neuromarketing Dashboard

## TL;DR
Replace the Groq/Llama single-call AI with a Full OpenAI pipeline:
1. OpenAI Vector Store as persistent knowledge base (30-100 relatórios + artigos científicos)
2. OpenAI GPT model for generation with `file_search` tool (automatic citations)
3. Curated neuromarketing system prompt with key scientific frameworks
4. New dedicated "Base de Conhecimento" page for managing the knowledge base
5. Enhanced 2-step analysis pipeline (statistical → strategic) with literature grounding

## Decisions Made
- **Provider**: Full OpenAI (vector store search + generation via OpenAI Responses API)
- **Config storage**: `.env` file for `OPENAI_API_KEY` and `VECTOR_STORE_ID`
- **Knowledge base UI**: Dedicated page "Base de Conhecimento" in Jornada de Compra module
- **Volume**: 30-100 documents (mix PPTX/PDF) + scientific articles

---

## Steps

### Phase 1: Foundation — OpenAI Provider + Config (blocks all other phases)

**Step 1.** Add dependencies to `requirements.txt`:
- `openai>=1.40.0`
- `python-dotenv>=1.0.0`

**Step 2.** Create `.env` file at `nenc-dashboard/.env`:
- `OPENAI_API_KEY=sk-...`
- `VECTOR_STORE_ID=vs_...` (empty initially, populated after first vector store creation)

**Step 3.** Create `utils/ai_provider.py`:
- `get_openai_client() -> OpenAI` — loads API key from .env
- `get_vector_store_id() -> str | None` — loads from .env
- `save_vector_store_id(vs_id: str)` — writes to .env
- `create_analysis(system_prompt, user_prompt, vector_store_id=None, ...) -> str` — calls OpenAI Responses API with optional `file_search` tool
- Uses `client.responses.create()` with `tools=[{"type": "file_search", "vector_store_ids": [vs_id]}]`
- Returns response text with citations included

**Step 4.** Update `analise.py` sidebar:
- Replace Groq API key input with status indicator ("OpenAI configurado ✅" / "Configure no .env ❌")
- Remove provider/model selectors (Full OpenAI = single provider)
- Keep model selector as OpenAI-only: `gpt-5.4-nano` / `gpt-5.4-mini` / `gpt-5.4` dropdown
- Add toggle: "📚 Consultar base de conhecimento" (on by default if vector store exists)

### Phase 2: Knowledge Base Page (depends on Step 3, parallel with Phase 3)

**Step 5.** Create `modules/jornada_compra/base_conhecimento.py`:
- **Section 1: Configuração** — shows current vector store ID, button to create new vector store if none exists
- **Section 2: Upload de Documentos** — `st.file_uploader` accepting multiple PDF/PPTX/DOCX/TXT
  - Form fields for attributes: `tipo` (selectbox: relatório/artigo científico), `projeto` (text), `ano` (number), `marca` (text), `categoria` (text)
  - Upload button calls `client.vector_stores.files.upload_and_poll()` with attributes
  - Show progress for each file
- **Section 3: Documentos na Base** — list all files via `client.vector_stores.files.list()`
  - Table with: filename, tipo, projeto, ano, status, size
  - Delete button per file
  - Summary metrics: total docs, total by tipo, storage estimate
- **Section 4: Testar Busca** — text input for test query, shows top results with scores and source file names

**Step 6.** Register new page in `app.py` — add to `jornada_compra` section:
```python
st.Page("modules/jornada_compra/base_conhecimento.py", title="Base de Conhecimento", icon="📚")
```
Place between "Preparação de Dados" and "Análise" in the navigation order.

### Phase 3: Enhanced System Prompt (parallel with Phase 2)

**Step 7.** Create `utils/neuro_prompts.py`:
- `NEURO_SYSTEM_PROMPT` (~2500 tokens): comprehensive neuromarketing expert prompt containing:
  - Role definition: specialist in eye-tracking data analysis, visual attention, and shopper behavior at point-of-sale
  - Metrics glossary with interpretive framework:
    - TotalGazeDuration: total visual engagement (higher = more attention, ref: Wedel & Pieters 2008)
    - FixationCount: number of distinct fixations (more = deeper processing or difficulty finding, ref: Chandon et al. 2009)
    - TimeToFirstFixation: visual salience indicator (lower = more salient, ref: Clement et al. 2013)
    - AverageFixationDuration: depth of processing per fixation (longer = deeper cognitive engagement)
    - NormalizedGazeDuration: proportion of attention relative to total viewing time
    - ScanPathLength: visual search complexity (longer = more exploratory search pattern)
    - GazeCount: revisitation metric (higher = re-evaluation or comparison behavior)
    - AOITransitionRate: switching between areas (higher = comparison shopping, ref: Russo & Leclerc 1994)
  - 3-stage visual decision model (Russo & Leclerc 1994): browsing → evaluation → verification
  - Shelf position effects framework (Chandon et al. 2009): center bias, eye-level advantage, number of facings
  - ANOVA interpretation guide: p-value thresholds, effect sizes, practical significance
  - Key principles: "attention is necessary but not sufficient for purchase" (Huddleston et al. 2018), "99.7% of assortment disregarded before attentive processing" (Nordfält & Ahlbom 2024)
  - Output structure template: Resumo Executivo → Achados Estatísticos → Comparação de Marcas → Fundamentação Teórica → Recomendações Práticas
  - Instruction: cite sources from knowledge base when available, cite academic references when interpreting metrics
- `build_user_prompt(data_tables, project_context, pptx_text, problems) -> str` — structured prompt builder replacing inline concatenation in analise.py

### Phase 4: Updated Analysis Pipeline (depends on Phases 1+3)

**Step 8.** Rewrite AI section of `analise.py` (~lines 170-270):
- Replace `Groq()` client with `create_analysis()` from `utils/ai_provider.py`
- Use system prompt from `utils/neuro_prompts.py`
- Use user prompt from `build_user_prompt()`
- When "Consultar base de conhecimento" is on:
  - Call `client.responses.create()` with `file_search` tool + vector_store_id
  - Model automatically searches knowledge base for relevant passages
  - Response includes `file_citation` annotations → render as clickable references
- Add analysis mode selector: "Rápida (1 chamada)" vs "Aprofundada (2 etapas)"
- "Aprofundada" mode:
  - Call 1: Statistical analysis (temperature 0.3, focused on data patterns)
  - Call 2: Strategic interpretation using Call 1 output + knowledge base context (temperature 0.5)
- Display with `st.tabs(["📊 Análise Estatística", "💡 Interpretação Estratégica", "📚 Referências"])`
- Parse `file_citation` annotations → render as numbered footnotes in "Referências" tab

**Step 9.** Remove `groq` dependency from `requirements.txt`; remove old Groq-specific code from analise.py

### Phase 5: Home Page Update (depends on Phase 2)

**Step 10.** Update `home.py` Jornada de Compra section:
- Add "📚 Base de Conhecimento" to the page list shown
- Add knowledge base status indicator: "📚 Base de Conhecimento — X documentos" (or "sem base")

---

## Relevant Files

| Arquivo | Ação |
|---|---|
| `app.py` | Adicionar página Base de Conhecimento na navegação |
| `home.py` | Atualizar card Jornada + status KB |
| `modules/jornada_compra/analise.py` | Reescrever seção de IA (~linhas 170-270) |
| `utils/ai_provider.py` | **NOVO** — OpenAI client + Responses API |
| `utils/neuro_prompts.py` | **NOVO** — system prompt curado + prompt builder |
| `modules/jornada_compra/base_conhecimento.py` | **NOVO** — página de gestão da KB |
| `.env` | **NOVO** — OPENAI_API_KEY + VECTOR_STORE_ID |
| `requirements.txt` | +openai, +python-dotenv, -groq |
| `modules/jornada_compra/preparacao.py` | Sem mudanças |

---

## Verification

1. `pip install -r requirements.txt` instala sem erros
2. `.env` com API key válida → `streamlit run app.py` → sem crash
3. Base de Conhecimento → criar vector store → ID salvo no .env
4. Upload 2-3 PDFs/PPTX com atributos → aparecem na lista
5. Busca teste → retorna trechos relevantes com scores
6. Análise com KB ON → resposta cita documentos uploadados com filename
7. Análise com KB OFF → funciona normalmente (só system prompt)
8. Modo "Aprofundada" → 2 tabs com conteúdo distinto
9. API key inválida → mensagem de erro clara
10. Upload de artigo científico → análise posterior cita achados do artigo

---

## Scope

**Incluído:**
- OpenAI Vector Store como base de conhecimento persistente (relatórios + artigos)
- Full OpenAI pipeline (file_search + generation) com citações automáticas
- System prompt curado com ~25 referências acadêmicas de neuromarketing
- Página dedicada Base de Conhecimento com upload, lista, delete, busca teste
- Pipeline de análise em 2 etapas (estatística → estratégica)
- Configuração via .env para API key + vector store ID
- Seletor de modelo (nano/mini/full GPT-5.4)

**Excluído (futuro):**
- Multi-provider support (Groq, Claude, Gemini, DeepSeek)
- RAG com vector store local (ChromaDB/FAISS)
- Streaming responses
- Structured JSON output
- Geração de relatório PDF
- Análise de imagem/heatmap
