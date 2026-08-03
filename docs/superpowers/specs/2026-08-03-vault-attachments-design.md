# Vault image attachments — design

**Date:** 2026-08-03
**Status:** approved, ready for implementation plan

## Problem

The MCP server exposes note tools only. When ChatGPT (or Claude) generates an image
for a note, it has no way to place the file in the vault and tells the user to save it
by hand:

> The Obsidian connector cannot upload the PNG itself. Save the generated image in your
> Obsidian attachments folder as `5xx-http-errors-handwritten.png`, and it will appear
> automatically in the note.

The note already contains the `![[…]]` embed, so the note side works. Only the binary
write is missing.

## Goal

Let the model put an image file into the vault's attachment folder and hand back the
wikilink to embed. Nothing else.

## Constraints

- MCP tool arguments are JSON, so binary data must arrive as base64 or as a URL the
  server fetches itself.
- Base64 alone is not enough: a 500 KB PNG is roughly 680 K base64 characters, past any
  model's single-response output limit. That is exactly the failure above, so a
  server-side fetch path is required.
- The server is currently filesystem-only and has no third-party HTTP dependency; the
  fetch path must use the standard library.
- Existing safety properties of `Vault` must hold: no absolute paths, no traversal out
  of the vault, no hidden files or folders, no clobbering without an explicit flag.

## Non-goals

- Mutating notes. The tool places a file and returns the embed string; the caller uses
  the existing `append_note` / `write_note` to place it.
- Non-image attachments (PDF, audio, video). Add later if a real need appears.
- Reading, listing, moving, or deleting attachments.

## Design

### `vault.py`

New module-level constants on `Vault`:

```python
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
```

**`_resolve_attachment(rel: str) -> Path`**
Calls the existing `_resolve(rel, require_md=False)` so it inherits the absolute-path,
traversal, and hidden-path guards unchanged, then rejects any suffix outside
`IMAGE_SUFFIXES` (case-insensitive). `_resolve` itself is not modified.

**`attachment_folder() -> str`**
Reads `attachmentFolderPath` from `<vault>/.obsidian/app.json` and returns it as a
vault-relative folder. Falls back to `"attachments"` when:

- `.obsidian/app.json` is absent, unreadable, or not valid JSON;
- the key is missing or empty (Obsidian's "vault root" setting — we still prefer a
  dedicated folder over scattering images at the root);
- the value is note-relative (starts with `./`), which cannot be resolved without
  knowing the target note.

A leading `/` is stripped. The result is not required to exist; `write_attachment`
creates it.

**`write_attachment(path: str, data: bytes, overwrite: bool = False) -> str`**

1. Reject `len(data) > MAX_ATTACHMENT_BYTES`.
2. Reject empty `data`.
3. Resolve via `_resolve_attachment`.
4. Refuse an existing file unless `overwrite` is true — same message shape as
   `write_note`.
5. `parent.mkdir(parents=True, exist_ok=True)` then `write_bytes`, wrapping `OSError`
   in `VaultError` exactly as `write_note` does.

Returns the vault-relative POSIX path.

### `tools.py`

Two tools, named to match the existing `write_note` / `read_note` convention.

**`write_attachment(filename, base64_data, folder="", overwrite=False) -> str`**
For small or locally-available images. Decodes with `base64.b64decode(…, validate=True)`
and turns `binascii.Error` into a `VaultError` that says the data was not valid base64.
Accepts and strips a `data:image/png;base64,` prefix, since models commonly emit one.

**`fetch_attachment(filename, url, folder="", overwrite=False) -> str`**
The server downloads the image. This is the path that works for large generated images.

- Reject any scheme other than `http` / `https`, both on the supplied URL and on
  `response.url` after redirects (blocks a redirect into `file://`).
- 30 second timeout.
- Read in chunks, aborting with a `VaultError` once `MAX_ATTACHMENT_BYTES` is exceeded,
  so an oversized or endless response cannot fill the disk.
- Sniff the leading magic bytes and require them to match the requested suffix for
  `png` / `jpg` / `gif` / `webp` / `bmp`, so an HTML error page never lands as a
  `.png`. `svg` is skipped (it is text); a mismatch raises `VaultError`.
- Uses `urllib.request` from the standard library — no new dependency.

Both tools:

- Treat `filename` as a bare filename; a `/` in it is an error, keeping folder choice in
  the `folder` argument.
- Default `folder` to `vault.attachment_folder()` when empty.
- Return JSON: `{"path": "attachments/x.png", "embed": "![[x.png]]", "bytes": 12345}`.
  `embed` uses the bare filename because Obsidian resolves wikilinks vault-wide.

### Error handling

Every failure surfaces as `VaultError` with a message naming the offending input and
the fix, matching the existing tools. No bare `except`, no silent fallbacks other than
the documented `attachment_folder()` default.

## Security trade-off

`fetch_attachment` gives this server outbound network access for the first time. A
prompt-injected note could ask it to `GET` an internal URL and write the body into the
vault. Accepted rather than filtered because:

- impact is bounded by the image-suffix allowlist, the magic-byte check, and the size
  cap — a non-image response is rejected, not saved;
- the server is local and single-user;
- blocking RFC1918 / loopback targets would break legitimately serving an image from a
  local HTTP server.

Documented in the README so the behaviour is not a surprise. Revisit if the server is
ever exposed beyond a personal tunnel.

## Testing

`tests/test_vault.py`

- attachment written to a nested folder that does not yet exist;
- traversal (`../outside.png`) rejected;
- hidden path (`.obsidian/x.png`) rejected;
- non-image suffix (`notes.txt`, `evil.md`) rejected;
- existing file refused without `overwrite`, replaced with it;
- oversized and empty payloads rejected;
- `attachment_folder()` for a configured value, a missing file, a malformed file, an
  empty value, a `./`-relative value, and a leading `/`.

`tests/test_tools.py`

- `write_attachment` round-trips bytes and returns the expected JSON shape;
- `data:` prefix accepted;
- malformed base64 rejected;
- `filename` containing `/` rejected;
- `fetch_attachment` with `urlopen` mocked: success path, non-http scheme, redirect to a
  non-http scheme, over-cap stream, magic-byte mismatch.

## Docs

Add both tools to the README tool table, note the 25 MB cap, and record the outbound
network behaviour of `fetch_attachment`.
