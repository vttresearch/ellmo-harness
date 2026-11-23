def write_file(path: str, text: str) -> str:
    """
    Safe write-file tool.

    Behavior:
      - Validates that the path is syntactically OK and has an existing parent directory.
      - Ensures the file does NOT already exist.
      - Writes the file directly if validation passes.
      - Returns a JSON-encoded string with:
          - status: "success" | "cancelled" | "error"
          - message: explanation
          - path: absolute path (when relevant)
    """

    import json
    from pathlib import Path

    # -------------------------
    # Validate the path
    # -------------------------
    try:
        p = Path(path).expanduser().resolve()
    except Exception:
        return json.dumps({
            "status": "error",
            "message": f"Invalid path syntax: {path!r}"
        })

    if not p.parent.exists():
        return json.dumps({
            "status": "error",
            "message": f"Invalid path: parent folder does not exist: {p.parent}"
        })

    if p.exists():
        return json.dumps({
            "status": "error",
            "message": f"File already exists and will not be overwritten: {p}"
        })

    if p.is_dir():
        return json.dumps({
            "status": "error",
            "message": f"Invalid path: points to a directory, not a file: {p}"
        })

    # -------------------------
    # Perform the write
    # -------------------------
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return json.dumps({
            "status": "success",
            "message": "File successfully created.",
            "path": str(p)
        })
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"Failed to write file: {e}",
            "path": str(p)
        })
