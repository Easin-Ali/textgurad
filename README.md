# TextGuard

A single-page writing analysis workspace with a FastAPI backend and a dependency-free HTML/CSS/JavaScript frontend. The project runs locally without service keys or a frontend build step.

## Run locally

1. Extract the ZIP and open a terminal in the `TextGuard` folder. Use Python 3.12.
2. Create a virtual environment with `python -m venv .venv`. Activate it with `source .venv/bin/activate` on macOS/Linux or `.venv\Scripts\Activate.ps1` in Windows PowerShell.
3. Run `python -m pip install -r requirements.txt`.
4. Optional: copy `.env.example` to `.env` and fill in the providers you want to use. The defaults run without a .env file.
5. Run `python -m uvicorn app:app --host 127.0.0.1 --port 8000 --no-access-log`. Open http://127.0.0.1:8000.

Select **Try a sample** to exercise local detection, comparison to a built-in sample, and rewriting. In **Plagiarism**, add up to five private reference files to replace the default sample corpus. Private sources stay in workspace memory and are sent to this backend for matching. TXT supports UTF-8 and UTF-16 with a BOM. PDFs must have a text layer. Each document supports up to 25,000 words and 600,000 characters after extraction, and each upload is capped at 10 MB.

## Provider configuration

| Setting | Purpose |
| --- | --- |
| GPTZERO_API_KEY | GPTZero v2 detector. Above the provider's 150,000-character cap or when the provider fails, the UI labels the local output as a demo. |
| OPENAI_API_KEY / OPENAI_MODEL | OpenAI Chat Completions rewrite. Text is sent in chunks for long documents. The local fallback applies a small English phrase dictionary. |
| ENABLE_PUBLIC_SEARCH | Set to true for Crossref, OpenAlex, Europe PMC and Wikipedia. |
| OPENALEX_API_KEY | Optional OpenAlex key. |
| BRAVE_API_KEY / NEWS_API_KEY | Add Brave Search and NewsAPI to online search when enabled. |
| COMMERCIAL_SCAN_URL / COMMERCIAL_SCAN_TOKEN | Optional integration with a commercial service through your authorized HTTPS connector. |

Check **Allow external services** in the workspace to enable sending text to configured providers. Online similarity sends up to six sampled phrases. GPTZero, OpenAI and a commercial connector may receive the document text. Results show provider availability and coverage. The language selector controls the Wikipedia edition and supplies a rewrite/connector hint. GPTZero detects language independently. Other searches receive the original query language.

The commercial connector contract is a bearer-authenticated JSON POST with `{"text":"…","language":"en"}`. It must return `{"sources":[{"title":"Source title","url":"https://example.org/paper","text":"Authorized comparison excerpt"}]}`. Only use excerpts your provider authorizes you to retrieve. This app includes the adapter contract, with no bundled Turnitin/iThenticate, Copyleaks, Unicheck, or PlagiarismCheck.org credentials or proprietary corpus. Search coverage is limited to the named adapters and the snippets, abstracts or metadata they return. There is no automatic full-text crawling or paywall access.

## How analysis works

- **Detection.** The GPTZero adapter normalizes document probability, confidence and returned sentence scores. The gauge represents the provider's fully AI-generated class probability. It does not estimate the fraction of the document written by AI. Sentence highlights apply two display thresholds. The separate diagnostics compute the standard deviation of sentence lengths, vocabulary diversity, repeated trigram share and exponentiated unigram entropy. Model perplexity is displayed only if returned by the provider. The offline sentence-length score is an uncalibrated interface demo.
- **Matching.** Search samples ten-word phrases from an overlapping window. Local comparison uses 8–10-word windows and trigram candidate retrieval. Exact windows match directly. Fuzzy candidates must pass Jaccard overlap, cosine similarity and token-level Levenshtein distance thresholds. Each input word counts once in the overall score even if multiple sources match. Fuzzy windows measure approximate passage coverage. These thresholds are deliberately bounded, and heavy paraphrases and translations may escape detection. Quote, bibliography, and common citation-pattern filtering are heuristic.
- **Rewriting.** The language model receives a mode, synonym strength and instructions to preserve facts, quantities and citation text. Verify its edits against the source. The optional second scan runs both detectors on the rewrite. No detector outcome is guaranteed.
- **Reports.** Download highlighted PDFs and TXT/DOCX rewrites. PDF export includes Unicode fonts for supported scripts. Complex-script shaping and bidirectional layout depend on the PDF renderer.
  
## Privacy and deployment

Text stays in browser workspace memory and transient server processing. The optional history saves summary metadata in the current browser. Delete my data clears the workspace and saved summaries. External providers apply their own retention policies. The process keeps short-lived, bounded search-response caches keyed by a hash of the query, and in-memory rate-limit counters. It does not save a document history on the server.

For network deployment, terminate TLS at your reverse proxy, set `REQUIRE_HTTPS=true`, set `TRUSTED_ORIGINS` to your HTTPS origin, and trust forwarded headers only from your proxy. Keep Uvicorn bound privately, disable request-body logging, configure upload size limits and streaming timeouts at the proxy, and restrict access where paid keys are enabled. Per-IP rate limiting is process-local, so use a shared gateway limiter for multiple workers. Restrict outbound access to configured providers. Localhost HTTP is for development.

## Files

```text
TextGuard/
  .env.example
  .gitignore
  README.md
  requirements.txt
  app.py              FastAPI routes, uploads, streaming and exports
  matching.py         Tokenization, exclusions and similarity
  providers.py        Detection, rewrite and search adapters
  fonts/              PDF fonts and license
  static/
    index.html
    style.css
    app.js
```

Provider references: [GPTZero](https://docs.gptzero.me/api-reference/), [OpenAI](https://developers.openai.com/api/reference/chat-completions/overview), [Brave](https://api-dashboard.search.brave.com/documentation/guides/authentication), [Crossref](https://www.crossref.org/documentation/retrieve-metadata/rest-api), [OpenAlex](https://help.openalex.org/api/).
