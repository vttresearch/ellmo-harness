import os


def read_file(path: str) -> str:
    """Read the contents of a file and return as text.

    Args:
        path (str): Path to the file.

    Returns:
        The file contents or an error message.
    """
    if not os.path.exists(path):
        return f"File does not exist: {path}"

    if not os.path.isfile(path):
        return f"Path is not a file: {path}"

    # If it's a PDF, extract text instead of reading raw bytes.
    if path.lower().endswith(".pdf"):
        try:
            from pdfminer.high_level import extract_text as pdf_extract_text
        except Exception:
            return (
                "PDF extraction dependency is missing (pdfminer.six). "
                "Install it to read PDF files with this tool."
            )

        try:
            pdf_text = pdf_extract_text(path)
            if not pdf_text or not pdf_text.strip():
                return f"No extractable text found in PDF file: {path}"
            return f"Contents of {path} (PDF extracted text):\n---\n{pdf_text}"
        except Exception as e:
            return f"Error reading PDF file {path}: {e}"

    # Fallback for normal text files
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            contents = f.read()
    except Exception as e:
        return f"Error reading file {path}: {e}"

    return f"Contents of {path}:\n---\n{contents}"
