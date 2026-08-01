# Foliate page translation

The unified Foliate reader can translate only the text visible on the current
page through an OpenAI-compatible `chat/completions` or `responses` endpoint.

## Reader workflow

1. Open **Reader settings → Page translation**.
2. Add an LLM profile or select an existing profile.
3. Choose source and target languages.
4. Enable automatic translation.
5. Use **Original / Translation** in the reader toolbar to switch views.

The original ebook DOM is never modified. A separate scrollable overlay renders
the translated blocks, so CFI locations, progress, bookmarks, highlights, and
reader pagination remain based on the original book.

Translations are cached per user using a hash of the book, visible source text,
languages, model settings, and prompt. Changing any of those inputs creates a
new cache entry.

## Supported endpoint contract

Profiles use the common OpenAI-compatible request shape:

```text
POST {base_url}/{endpoint_path}
Authorization: Bearer {api_key}
```

The default endpoint path is `chat/completions`. When the configured path ends
in `responses`, the backend automatically switches to the Responses API request
and response envelope. Presets are included for OpenRouter, Groq, Mistral, and
Ollama, while all fields remain editable.

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
- at most 24,000 source characters per page;
- at most 8,000 characters per block;
- provider timeout from 5 to 180 seconds;
- provider responses are limited to 2 MiB;
- translation output must contain every requested block ID.
