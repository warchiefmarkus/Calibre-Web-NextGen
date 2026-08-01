# Foliate page translation

The unified Foliate reader can translate only the text visible on the current
page through an OpenAI-compatible `chat/completions` or `responses` endpoint.

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
style, alignment, indentation, and block margins. Longer translations are laid
out as horizontal subpages with the same available height as the original page;
there is no vertical reader scroll. Reaching the final translated subpage turns
exactly one original Foliate page, keeps the previous overlay visible while the
next translation loads, then opens the first translated subpage. Backward
navigation restores the previous original page and its final translated subpage.
The translated page has no cache/status strip. While a request is pending, only
a larger centered spinner is shown without text, border, or background. Text
selection uses the same readable yellow highlight as the original Foliate document.

Selecting text in the original page exposes a temporary **Translation** action
beside Highlight and Add note. The selected range is translated as one cached
LLM block and replaced only in the current in-memory Foliate document. The
original fragment is retained and restored before page, chapter, bookmark, or
progress navigation. Leading and trailing whitespace from the selected range is
kept around the translated text so adjacent words do not become joined. Relocate
events caused by the temporary DOM patch are not saved, so CFI and reading
progress remain based on the untouched ebook.

Translations are cached per user using a hash of the book, visible source text,
languages, model settings, and prompt. Changing any of those inputs creates a
new cache entry.

Visible pages are translated in bounded batches (up to 8 blocks and roughly
3,500 source characters per provider request). If a model returns an incomplete
block list, the backend automatically retries smaller halves. Oversized single
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
are shown automatically when a compatible endpoint includes them.

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
