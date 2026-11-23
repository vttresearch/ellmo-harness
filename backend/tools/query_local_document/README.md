# Local document search

Place exactly one manual in this directory. Supported formats:

- PDF (`.pdf`) — parsed with Docling
- Markdown (`.md` or `.markdown`) — read directly
- Plain text (`.txt`) — read directly

Alternatively, set `ELLMO_DOCUMENT_PATH` to one local document. The
`query_local_document` tool itself does not accept a file path; the operator
controls which document it searches. Its generated index is kept separately under `cache/`; source
documents and generated caches are ignored and must not be committed.

PDF citations expose a printed `page=` label and a physical `pdf_page=` position.
Labels come from PDF metadata; without metadata, they fall back to one-based file
positions. To navigate in Unity, load the same document and pass `page=` unchanged
to `change_page`. Text and Markdown citations use chunk numbers instead.

You may also set `local_document.path` in your local configuration. Relative paths
are resolved from the configuration file's directory. `ELLMO_DOCUMENT_PATH` takes
precedence; an empty setting uses the tool's document directory.
