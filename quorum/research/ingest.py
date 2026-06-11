"""Turn uploaded text into stable, citable source chunks.

Chunks are paragraph-ish so a model can cite a specific, findable span. IDs are
stable within a run (``C1``, ``C2``, …) so citations resolve deterministically.
MVP handles plain text / markdown; PDF extraction lives behind the optional
``[research]`` extra (see ``ingest_pdf``).
"""

from __future__ import annotations

import re

from quorum.research.schema import SourceChunk

MAX_TOTAL_CHARS = 200_000      # MVP guard; fail honestly above this
_TARGET_CHARS = 900           # soft cap per chunk before splitting a long paragraph

import unicodedata

# Upload limits (research mode), per the upload-security design. txt/md only;
# PDF would sit behind the optional [research] extra with its own worker caps.
MAX_FILE_BYTES = 512 * 1024           # 512 KiB per file
MAX_TOTAL_BYTES = 1_536 * 1024        # 1.5 MiB across a run
MAX_FILES = 4
MAX_CHUNKS = 400
ALLOWED_EXTENSIONS = {".txt", ".md", ".markdown", ".text"}

# Binary / archive / document magic. We do NOT trust extension or Content-Type;
# we sniff the bytes and reject anything that isn't plain text.
_BINARY_MAGIC = (
    b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08",      # zip / docx / jar / apk
    b"\x1f\x8b", b"BZh", b"\xfd7zXZ\x00", b"7z\xbc\xaf\x27\x1c", b"Rar!",
    b"%PDF-", b"\x7fELF", b"MZ", b"\xd0\xcf\x11\xe0",  # pdf / elf / pe / ole
    b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"BM",        # images
    b"\xca\xfe\xba\xbe", b"\xfe\xed\xfa",               # java/mach-o
)
_HTML_MARKERS = ("<!doctype", "<html", "<head", "<body", "<script", "<iframe", "<svg", "<?xml")


def _sanitize_name(filename: str | None) -> str:
    name = (filename or "upload").replace("\\", "/").rsplit("/", 1)[-1]
    name = unicodedata.normalize("NFKC", name)
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '<>:"|?*')
    return name[:80] or "upload.txt"


def read_upload(filename: str | None, data: bytes, max_bytes: int = MAX_FILE_BYTES) -> tuple[str, str]:
    """Validate a single uploaded file and return (display_name, decoded_text).

    Trusts NOTHING from the client beyond the bytes: extension allowlist, size
    bound, binary-magic sniff, strict UTF-8 decode (NUL rejected), HTML/XML
    document rejection, and a control-character ratio gate. Raises ``ValueError``.
    """
    name = _sanitize_name(filename)
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"unsupported file type {ext or '(none)'}; allowed: .txt, .md")
    if len(data) > max_bytes:
        raise ValueError(f"{name} too large ({len(data)} bytes > {max_bytes})")
    for sig in _BINARY_MAGIC:
        if data.startswith(sig):
            raise ValueError(f"{name}: looks like a binary/archive/document file, not text")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{name} is not valid UTF-8 text")
    if "\x00" in text:
        raise ValueError(f"{name} contains NUL bytes")
    head = text[:2048].lstrip().lower()
    if any(head.startswith(m) for m in _HTML_MARKERS):
        raise ValueError(f"{name}: HTML/XML documents are not accepted (plain text/markdown only)")
    sample = text[:8192]
    if sample:
        ctrl = sum(1 for ch in sample if ord(ch) < 9 or 13 < ord(ch) < 32)
        if ctrl / len(sample) > 0.02:
            raise ValueError(f"{name}: too many control characters (not plain text)")
    return name, text


def ingest_uploads(files: list[tuple[str | None, bytes]]) -> list[SourceChunk]:
    """Validate + combine multiple uploads into source chunks (global ids)."""
    if not files:
        raise ValueError("no files uploaded")
    if len(files) > MAX_FILES:
        raise ValueError(f"too many files ({len(files)} > {MAX_FILES})")
    total = sum(len(data) for _, data in files)
    if total > MAX_TOTAL_BYTES:
        raise ValueError(f"upload too large ({total} bytes > {MAX_TOTAL_BYTES})")

    sections, names = [], []
    for filename, data in files:
        name, text = read_upload(filename, data)
        names.append(name)
        sections.append(text)
    chunks = chunk_text("\n\n".join(sections), source=", ".join(names))
    if len(chunks) > MAX_CHUNKS:
        raise ValueError(f"source produced too many chunks ({len(chunks)} > {MAX_CHUNKS})")
    return chunks


def chunk_text(text: str, source: str = "upload", max_total: int = MAX_TOTAL_CHARS) -> list[SourceChunk]:
    """Split text into paragraph chunks with stable IDs.

    Raises ``ValueError`` if the extracted text exceeds ``max_total`` — we fail
    honestly rather than silently truncate evidence.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > max_total:
        raise ValueError(
            f"source too large ({len(text)} chars > {max_total}); split it or "
            "use a retrieval-limited mode (not in the MVP)"
        )

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[SourceChunk] = []
    for para in paragraphs:
        for piece in _split_long(para, _TARGET_CHARS):
            chunks.append(SourceChunk(id=f"C{len(chunks) + 1}", text=piece, source=source))
    if not chunks and text.strip():
        chunks.append(SourceChunk(id="C1", text=text.strip(), source=source))
    return chunks


def _split_long(paragraph: str, target: int) -> list[str]:
    """Split an over-long paragraph: sentence-pack first, then hard-window any
    piece with no usable boundaries so a degenerate input (e.g. one 200k-char
    token) can't become a single giant chunk."""
    if len(paragraph) <= target:
        return [paragraph]
    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    pieces, current = [], ""
    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > target:
            pieces.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current.strip():
        pieces.append(current.strip())

    out: list[str] = []
    for piece in pieces or [paragraph]:
        if len(piece) <= target * 2:
            out.append(piece)
        else:                                   # no boundaries — deterministic windows
            out.extend(piece[i:i + target] for i in range(0, len(piece), target))
    return out
