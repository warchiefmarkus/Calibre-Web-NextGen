# Managed fork and upstream workflow

## Repository topology

This bare-metal deployment has two source repositories:

1. `warchiefmarkus/Calibre-Web-NextGen` — this UI repository.
2. `warchiefmarkus/calibremcp` — MCP/REST backend and RAG engine.

Calibre itself is installed as the system application `/usr/bin/calibre-server`; it is not maintained as a third Git fork. `/root/calibre/Library` and `/root/calibre/CalibreConfig` are runtime data directories.

## Git remotes and branch

- Working branch: `legion-managed`
- `origin`: `warchiefmarkus/Calibre-Web-NextGen` — fetch and push
- `upstream`: `new-usemame/Calibre-Web-NextGen` — fetch only; push is disabled

Use merge commits when updating from upstream:

```bash
git fetch upstream
git switch legion-managed
git merge --no-ff upstream/main
cd frontend
npm ci
npm run build
cd ..
python -m pytest tests/unit -q
git push origin legion-managed
```

This preserves upstream history while keeping the managed deployment changes reviewable on one consistently named branch in both forks.

## Runtime responsibility

Calibre-Web NextGen owns:

- the SPA and classic browser surfaces;
- authentication and library visibility policy;
- EPUB/FB2 readers and reader state;
- the authenticated `/api/v1/rag/status` and `/api/v1/rag/search` proxy;
- presentation of embedding/reranker metadata and query-centred excerpts.

CalibreMCP owns extraction, indexing, FTS/BM25, E5 embeddings, LanceDB, query normalization, Jina reranking, and the authenticated backend REST API. The system `calibre-server` serves the native Calibre content/API surface.

## Reader behavior

The SPA uses one vendored `foliate-js` runtime for EPUB/KEPUB, FB2/FBZ, MOBI/AZW/AZW3, and CBZ. The snapshot is pinned to commit `78914aefbb1351545fe60e4cdbabcce514d1f201`; its LGPL-3.0-or-later license and update record are stored in `frontend/src/vendor/foliate-js/`. PDF, audio, TXT, CBR, CBT, and CB7 remain on the native fallback reader.

The unified reader provides paginated and continuous modes, one/two-column layout, recursive TOC, full-text search, stable whole-book progress, section/page/location labels, account-synced settings and named bookmarks, highlights/notes, TTS, fullscreen, and keyboard navigation. Current position remains in the shared Calibre bookmark/reader-data row; named bookmarks live in CWNG `app.db`; highlights and notes continue to use the existing annotations API.

FB2 is fetched as bytes. The vendored parser inspects the XML declaration and re-decodes non-UTF-8 files, preserving Windows-1251 books such as `Хельсрич`. Legacy `fb2-scroll:` bookmarks and `position_fraction` values are accepted on first open and then rewritten as foliate locators. Do not replace byte loading with `response.text()`.

Book content is rendered in sandboxed publication iframes. The SPA CSP permits only the required same-origin/blob frames and resources; `/show` responses retain `script-src 'none'` so ebook scripts cannot execute.

## RAG UI behavior

The AI Search page must show both:

- `intfloat/multilingual-e5-small` as the embedding model;
- `jinaai/jina-reranker-v2-base-multilingual` as the reranker.

Search excerpts are supplied by CalibreMCP as query-centred windows. The UI must not substitute the beginning of the full chunk, because that can hide the relevant event inside a long passage.

## Required verification after an upstream merge

- Run the upstream unit suite and the managed RAG/reader tests.
- Run TypeScript checking and a production Vite build.
- Start the bare-metal services and verify `/app` and `/api/v1/auth/config` return HTTP 200.
- In a real browser, open FB2 book 62 and confirm `Хельсрич` has no mojibake.
- Search for the ASIC design-time passage and the Carmack school-computer passage through `/api/v1/rag/search`.
- Confirm the AI Search page renders both model names.
- Review service logs for new traceback, database-session, reader, or RAG errors.

Do not commit deployment secrets, `.env` files, user databases, generated frontend dependencies, or runtime library data.
