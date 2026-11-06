"""Turn uploaded files / web pages into plain text (with markdown-style headings)."""
from __future__ import annotations

import csv
import io
import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt", ".md", ".markdown", ".html", ".htm", ".csv"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class ExtractionError(ValueError):
    pass


def extract_text(filename: str, data: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ExtractionError(f"Unsupported file type '{ext}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ExtractionError("File too large (max 25 MB)")
    try:
        if ext == ".pdf":
            return _pdf(data)
        if ext == ".docx":
            return _docx(data)
        if ext == ".pptx":
            return _pptx(data)
        if ext in (".html", ".htm"):
            return html_to_text(data.decode("utf-8", errors="replace"))
        if ext == ".csv":
            return _csv(data.decode("utf-8-sig", errors="replace"))
        return data.decode("utf-8", errors="replace")
    except ExtractionError:
        raise
    except Exception as e:
        raise ExtractionError(f"Could not read {ext} file: {e}") from e


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = [(p.extract_text() or "").strip() for p in reader.pages]
    text = "\n\n".join(p for p in pages if p)
    if not text.strip():
        raise ExtractionError("PDF contains no extractable text (scanned images need OCR first)")
    return text


def _docx(data: bytes) -> str:
    import docx

    d = docx.Document(io.BytesIO(data))
    lines = []
    for p in d.paragraphs:
        t = p.text.strip()
        if not t:
            continue
        style = (p.style.name or "").lower() if p.style is not None else ""
        if style.startswith("heading"):
            level = "".join(c for c in style if c.isdigit()) or "2"
            lines.append("#" * min(int(level), 4) + " " + t)
        else:
            lines.append(t)
    for table in d.tables:
        for row in table.rows:
            lines.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n\n".join(lines)


def _pptx(data: bytes) -> str:
    from pptx import Presentation

    prs = Presentation(io.BytesIO(data))
    out = []
    for i, slide in enumerate(prs.slides, 1):
        texts = []
        title = None
        for shape in slide.shapes:
            if shape.has_text_frame:
                t = shape.text_frame.text.strip()
                if not t:
                    continue
                if title is None and shape == getattr(slide.shapes, "title", None):
                    title = t
                else:
                    texts.append(t)
        out.append(f"## Slide {i}: {title or ''}".strip() + "\n" + "\n".join(texts))
    return "\n\n".join(out)


def _csv(text: str) -> str:
    """CSV with question/answer columns becomes FAQ text; otherwise rows are joined."""
    reader = csv.DictReader(io.StringIO(text))
    fields = [f.lower().strip() for f in (reader.fieldnames or [])]
    q_col = next((f for f in reader.fieldnames or [] if f.lower().strip() in ("question", "q")), None)
    a_col = next((f for f in reader.fieldnames or [] if f.lower().strip() in ("answer", "a")), None)
    parts = []
    for row in reader:
        if q_col and a_col:
            parts.append(f"## {row[q_col].strip()}\n{row[a_col].strip()}")
        else:
            parts.append("; ".join(f"{k}: {v}" for k, v in row.items() if v))
    if not parts:
        raise ExtractionError(f"CSV is empty (columns: {fields})")
    return "\n\n".join(parts)


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "form", "svg", "iframe"]):
        tag.decompose()
    lines = []
    for el in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "dt", "dd"]):
        t = " ".join(el.get_text(" ", strip=True).split())
        if not t:
            continue
        if el.name in ("h1", "h2", "h3", "h4"):
            lines.append("#" * int(el.name[1]) + " " + t)
        elif el.name == "li":
            lines.append("- " + t)
        else:
            lines.append(t)
    # de-duplicate consecutive repeats (common in page builders)
    out = []
    for ln in lines:
        if not out or out[-1] != ln:
            out.append(ln)
    return "\n\n".join(out)


def _is_public_host(host: str) -> bool:
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True
    except socket.gaierror:
        return False


async def fetch_url(url: str, allowed_domains: list[str]) -> tuple[str, str]:
    """Fetch an approved web page. Returns (title, text). Only domains on the
    allowlist are permitted, and private network addresses are refused (SSRF)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ExtractionError("URL must start with http:// or https://")
    host = parsed.hostname.lower()
    if not any(host == d or host.endswith("." + d) for d in allowed_domains):
        raise ExtractionError(f"Domain '{host}' is not on the approved list ({', '.join(allowed_domains)}). "
                              "Add it to KNOWLEDGE_ALLOWED_DOMAINS to permit it.")
    if not _is_public_host(host):
        raise ExtractionError("URL resolves to a private address")
    async with httpx.AsyncClient(timeout=20, follow_redirects=False, headers={"User-Agent": "BongoHiveVoiceAgent/1.0"}) as c:
        r = await c.get(url)
        hops = 0
        while r.is_redirect and hops < 3:
            nxt = urlparse(str(r.next_request.url)) if r.next_request else None
            if not nxt or not any(nxt.hostname == d or (nxt.hostname or "").endswith("." + d) for d in allowed_domains):
                raise ExtractionError("Redirected to a non-approved domain")
            r = await c.get(str(r.next_request.url))
            hops += 1
        r.raise_for_status()
        if len(r.content) > MAX_UPLOAD_BYTES:
            raise ExtractionError("Page too large")
    soup = BeautifulSoup(r.text, "html.parser")
    title = (soup.title.string or "").strip() if soup.title else url
    return title[:500], html_to_text(r.text)
