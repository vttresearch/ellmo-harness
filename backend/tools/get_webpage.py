import time
import requests


def get_webpage(webpage: str, timeout_seconds: float = 30.0) -> str:
    """Fetch and clean a webpage."""

    webpage = str(webpage or "").strip()
    if not webpage:
        return "Invalid request: 'webpage' cannot be empty."

    try:
        timeout_seconds = float(timeout_seconds)
    except Exception:
        return "Invalid request: 'timeout_seconds' must be a number."
    if timeout_seconds <= 0:
        return "Invalid request: 'timeout_seconds' must be > 0."

    connect_timeout = min(timeout_seconds, 10.0)
    request_timeout = (connect_timeout, timeout_seconds)

    try:
        res = requests.get(webpage, timeout=request_timeout)
        res.raise_for_status()
    except requests.Timeout:
        return (
            f"Failed to fetch webpage within {timeout_seconds:.1f}s: {webpage}"
        )
    except requests.RequestException as e:
        return f"Failed to fetch webpage: {e}"

    ts = time.time()

    print("cleaning page...", webpage)
    cleaned_page = clean_html_for_llm(res.text)
    print(f"... page cleaned in {time.time() - ts:.2f}s")

    return f"Here is the requested webpage:\n---\n{cleaned_page}"


def clean_html_for_llm(html: str) -> str:
    from bs4 import BeautifulSoup
    from bs4.element import Comment
    from html import unescape
    import re

    soup = BeautifulSoup(html, "lxml")

    # Remove non-content tags
    for tag in soup(["script", "style", "noscript", "header", "footer",
                     "nav", "svg", "meta", "link"]):
        tag.decompose()

    # Remove comments
    for element in soup(text=lambda t: isinstance(t, Comment)):
        element.extract()

    # Preserve links as "label [url]"
    for a in soup.find_all("a"):
        href = a.get("href")
        label = a.get_text(" ", strip=True)
        replacement = f"{label} [{href}]" if href else label
        a.replace_with(replacement)

    # Preserve images as "[Image: alt] [src]"
    for img in soup.find_all("img"):
        alt = img.get("alt", "").strip()
        src = img.get("src")
        replacement = ""
        if src:
            replacement = f"[Image: {alt}] [{src}]" if alt else f"[Image] [{src}]"
        img.replace_with(replacement)

    # Remove inline styles and JS handlers
    for tag in soup.find_all(True):
        if tag.has_attr("style"):
            del tag["style"]
        for attr in list(tag.attrs):
            if attr.lower().startswith("on"):
                del tag[attr]

    # Extract text
    text = soup.get_text(separator="\n")
    text = unescape(text)

    # Normalize whitespace
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]

    return "\n".join(lines)
