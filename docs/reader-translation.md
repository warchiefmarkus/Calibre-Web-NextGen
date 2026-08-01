# Foliate page translation

The unified Foliate reader translates the text visible on the current page
through an OpenAI-compatible `chat/completions` or `responses` endpoint while
preserving visible book images locally without sending image data to the model.

## Reader workflow

1. Open the dedicated **Page translation** panel from the language button in the reader toolbar.
2. Add an LLM profile or select an existing profile.
3. Choose source and target languages.
4. Enable automatic translation.
5. Use the icon-only Original / Translation switch in the reader toolbar, or press `T`, to switch views.

The original ebook DOM is never modified. A separate fixed-height overlay renders
the translated blocks, so CFI locations, progress, bookmarks, highlights, and
reader pagination remain based on the original book.

The overlay copies the computed typography and page geometry of the visible
source blocks, including font family, pixel font size, line height, weight,
style, alignment, indentation, and block margins. Inline source formatting is
normalized into ordered text runs with stable IDs. The supported semantic marks
are strong, emphasis, code, superscript, subscript, and link, plus explicit line
breaks. The model receives only run IDs, text, semantic mark names, and break
counts; it never receives source HTML, CSS, or link URLs. The backend requires
complete run-ID coverage, restores source boundary whitespace, and copies trusted
source marks into the response. The browser then rebuilds safe React elements
from the original run metadata instead of rendering model-produced markup.

Original `img` elements are inserted back into the translated content at their
source-order positions with their displayed size, alignment, margins, and aspect
ratio. Text embedded inside images is not translated. Longer translations are
laid out as horizontal subpages with the same available height as the original page;
there is no vertical reader scroll. Reaching the final translated subpage turns
exactly one original Foliate page, keeps the previous overlay visible while the
next translation loads, then opens the first translated subpage. Backward
navigation restores the previous original page and its final translated subpage.
The translated page has no cache/status strip or centered loading overlay. While
a current-page translation or one-page-ahead preload is active, a compact progress
ring is drawn around the translation icon in the top Original/Translation switch. Text
selection uses the same readable yellow highlight as the original Foliate document.

Selecting text in the original page exposes temporary **Translation** and
**ChatGPT** actions beside Highlight and Add note. ChatGPT opens a new web chat
with the exact selected text in the `q` parameter and never sends it through the
reader translation provider. The Translation action sends the selected range as
one LLM block and replaces it only in the current in-memory Foliate document. The
original fragment is retained and restored before page, chapter, bookmark, or
progress navigation. Leading and trailing whitespace from the selected range is
kept around the translated text so adjacent words do not become joined. Relocate
events caused by the temporary DOM patch are not saved, so CFI and reading
progress remain based on the untouched ebook.

Translations are cached per user using a hash of the book, visible source text,
languages, provider endpoint, model settings, and prompt. Changing any of those
inputs creates a new cache entry. The reader setting **Cache full translated
pages** controls both the browser session cache and persistent server cache.
Disabling it bypasses existing entries and prevents new full-page entries from
being written; inline selected-text translations are never persisted.

When **Preload one translated page ahead** is enabled together with automatic
translation and full-page caching, the reader extracts the next paginated
Foliate page from the already loaded section after the current page is ready. It
shifts only an offscreen extraction viewport; it does not call next/previous,
change the reader CFI or save progress. Its activity is shown by the same compact
ring around the top translation icon. The background result is stored in both
browser and server caches. If the reader opens that page before preloading finishes,
the foreground view joins the existing preload promise instead of sending a second
HTTP request. Only one preload request runs at a time, and the newest requested
page replaces an older queued
page. Provider errors remain silent while the page is only speculative; if the
reader opens that page and joins the request, the same error is surfaced as a
foreground translation error. Opening a different page or receiving a foreground
provider error cancels obsolete preload work. A failed foreground page is not
submitted again by repeated Foliate relocate events; it remains on the original
page with the error shown until the user explicitly presses **Retry**. The toolbar
activity state is also reconciled against active request refs once per second and
aborts work that exceeds the selected profile timeout plus a five-second client
grace period, preventing a stale spinner after an aborted or disconnected request.
Preloading is skipped in
scrolling/vertical-writing layouts and at a section boundary where the next section
is not already loaded.

Visible pages are translated in bounded batches (up to 8 blocks and roughly
3,500 source characters per provider request). The profile timeout is an overall
deadline for the complete page, including incomplete-response fallbacks; each
fallback receives only the remaining time. If a model returns an incomplete
block list, the backend automatically retries smaller halves. If one formatted
block still omits runs, the backend translates its runs separately and rebuilds
the block with the original marks and line-break metadata. Oversized plain
paragraphs are split into temporary fragments and reassembled under the original
block ID, so reasoning models with limited output budgets do not fail the whole
page.

## Supported endpoint contract

Profiles use the common OpenAI-compatible request shape:

```text
POST {base_url}/{endpoint_path}
Authorization: Bearer {api_key}
```

The default endpoint path is `chat/completions`. The backend also supports
OpenAI Responses (`responses`), Anthropic Messages (`messages`), and Google
Generate Content (`models/<model>:generateContent`) request/response envelopes.
Presets are included for OpenCode Zen, OpenCode Go, NVIDIA NIM, OpenRouter,
Groq, Mistral, and Ollama, while all fields remain editable. NVIDIA NIM uses
`https://integrate.api.nvidia.com/v1`, discovers the live hosted catalog through
`GET /v1/models`, and defaults to `nvidia/nemotron-3-nano-30b-a3b`. The public
catalog currently supplies model ID and publisher; context length and description
are shown automatically when a compatible endpoint includes them. GPT-OSS models
use low reasoning effort and streamed Chat Completions to reduce translation
latency. Their page batches run sequentially, and a stalled hosted completion is
retried once with a bounded per-attempt timeout while still respecting the overall
profile deadline. For automatic page translation, `openai/gpt-oss-20b` provides
materially lower hosted latency than the 120B route.

OpenCode Zen and OpenCode Go use their public `/models` feeds for discovery.
Saving either preset automatically loads the current server-side model list.
When a discovered model is selected, the reader chooses the protocol documented
for that OpenCode model family: GPT uses Responses, Claude/Qwen/MiniMax use
Messages where applicable, Gemini uses Generate Content, and the remaining
open models use Chat Completions.

API keys are encrypted with the installation Fernet key and are never returned
to the browser. Additional headers reject authorization, cookie, token, secret,
and API-key style names; use the dedicated API-key field for credentials.

## Private and local endpoints

Private, loopback, and LAN destinations are blocked by default. Public requests
use the application's Advocate SSRF validator. To permit a local Ollama or other
LAN inference server, set this explicitly in the service environment:

```ini
CWNG_READER_TRANSLATION_ALLOW_PRIVATE_ENDPOINTS=true
```

Restart Calibre-Web NextGen after changing the environment. Enabling this flag
allows users with reader access to configure arbitrary private HTTP endpoints,
so it should only be used on a trusted single-user installation.

## Limits

- at most 80 visible blocks per request;
- at most 8,000 source characters per visible page;
- at most 8,000 characters per block;
- provider timeout from 5 to 180 seconds;
- provider responses are limited to 2 MiB;
- translation output must contain every requested block ID.
