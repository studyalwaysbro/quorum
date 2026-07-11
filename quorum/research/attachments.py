"""Bounded, local attachment extraction for Quorum research.

This module deliberately converts supported files to plain text.  It never
passes an original path to a model, follows a symlink, expands an archive, or
claims that advisory pattern matching can make hostile content safe.

PDF and image extraction are optional.  Their parsers have a larger attack
surface than the standard-library text/data readers, so callers handling
untrusted uploads should still run extraction in an OS sandbox and must not
send the result to a tool-capable model.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import select
import shutil
import stat
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from quorum.research.ingest import chunk_text
from quorum.research.schema import SourceChunk

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 15 * 1024 * 1024
MAX_FILES = 8
MAX_EXTRACTED_CHARS = 400_000
MAX_PDF_PAGES = 50
MAX_IMAGE_PIXELS = 25_000_000
MAX_DATA_ROWS = 20_000
MAX_DATA_COLUMNS = 256
MAX_REMOTE_SOURCE_BYTES = 750_000
PARSER_TIMEOUT_SECONDS = 30
PARSER_OUTPUT_BYTES = 2 * 1024 * 1024
PARSER_IMAGE = os.environ.get("QUORUM_PARSER_IMAGE", "quorum-parser:0.1.0")

_TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".text"}
_DATA_EXTENSIONS = {".csv", ".tsv", ".json"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
SUPPORTED_EXTENSIONS = _TEXT_EXTENSIONS | _DATA_EXTENSIONS | _IMAGE_EXTENSIONS | {".pdf"}

_ARCHIVE_OR_EXECUTABLE_MAGIC = (
    b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08", b"\x1f\x8b", b"BZh",
    b"\xfd7zXZ\x00", b"7z\xbc\xaf\x27\x1c", b"Rar!", b"\x7fELF", b"MZ",
    b"\xd0\xcf\x11\xe0", b"{\\rtf",
)
_IMAGE_MAGIC = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".webp": (b"RIFF",),
}
_UNSAFE_PDF_MARKERS = (
    b"/JavaScript", b"/JS", b"/EmbeddedFile", b"/Filespec", b"/Launch",
    b"/OpenAction", b"/AA", b"/RichMedia", b"/SubmitForm", b"/ImportData",
)


@dataclass(frozen=True)
class Advisory:
    kind: str
    message: str
    start: int
    end: int
    severity: str = "warning"


@dataclass(frozen=True)
class AttachmentResult:
    name: str
    media_kind: str
    text: str
    chunks: tuple[SourceChunk, ...]
    advisories: tuple[Advisory, ...]
    original_bytes: int


_SECRET_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b")),
    ("github_token", re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{16,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("credential_url", re.compile(r"\bhttps?://[^\s/:@]+:[^\s/@]{4,}@[^\s]+", re.I)),
    ("credential_json", re.compile(
        r'''(?i)["'](?:api[_-]?key|access[_-]?token|secret|password)["']\s*:\s*["'][^"']{6,}["']'''
    )),
    ("us_ssn", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")),
    ("credential_assignment", re.compile(
        r"(?im)^\s*(?:export\s+)?[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)\s*=\s*[^\s#]{6,}"
    )),
)
_INJECTION_PATTERNS = (
    re.compile(r"(?i)\bignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?\b"),
    re.compile(r"(?i)\b(?:system|developer)\s+(?:message|prompt)\b"),
    re.compile(r"(?i)\b(?:reveal|print|exfiltrate)\b.{0,40}\b(?:secret|token|password|system prompt)\b"),
    re.compile(r"(?i)\b(?:run|execute)\b.{0,30}\b(?:command|shell|tool)\b"),
)
_HTML_XML_MARKERS = (
    "<!doctype", "<html", "<head", "<body", "<script", "<iframe", "<svg",
    "<?xml", "<!entity",
)


def sanitize_name(filename: str | None) -> str:
    """Return a short display-only basename; never use it as a filesystem path."""
    name = unicodedata.normalize("NFKC", filename or "attachment")
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '<>:"|?*')
    return name[:120] or "attachment"


def scan_advisories(text: str) -> tuple[Advisory, ...]:
    """Find likely secrets and injection-like language.

    These are review hints, not a complete prompt-injection or DLP detector.
    Missing findings never imply that content is safe or non-sensitive.
    """
    findings: list[Advisory] = []
    for kind, pattern in _SECRET_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(Advisory(kind, f"possible {kind.replace('_', ' ')}", match.start(), match.end(), "high"))
    for pattern in _INJECTION_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(Advisory(
                "prompt_injection_language",
                "instruction-like document text; treat as untrusted data, not authority",
                match.start(), match.end(), "warning",
            ))
    return tuple(sorted(findings, key=lambda f: (f.start, f.end, f.kind)))


def redact_text(text: str, advisories: Iterable[Advisory] | None = None) -> str:
    """Deterministically redact secret findings; injection advisories stay visible."""
    findings = tuple(advisories) if advisories is not None else scan_advisories(text)
    spans = sorted(
        ((a.start, a.end, a.kind) for a in findings if a.kind != "prompt_injection_language"),
        key=lambda item: (item[0], -item[1]),
    )
    merged: list[tuple[int, int, str]] = []
    for start, end, kind in spans:
        if start < 0 or end > len(text) or start >= end:
            continue
        if merged and start < merged[-1][1]:
            old_start, old_end, old_kind = merged[-1]
            merged[-1] = (old_start, max(old_end, end), old_kind)
        else:
            merged.append((start, end, kind))
    out, cursor = [], 0
    for start, end, kind in merged:
        out.extend((text[cursor:start], f"[REDACTED:{kind.upper()}]"))
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


def read_attachment_path(path: str | os.PathLike[str], *, max_bytes: int = MAX_FILE_BYTES) -> tuple[str, bytes]:
    """Read one regular local file without following symlinks."""
    raw = os.fspath(path)
    info = os.lstat(raw)
    if stat.S_ISLNK(info.st_mode):
        raise ValueError(f"refusing symlink attachment: {raw}")
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"attachment is not a regular file: {raw}")
    if info.st_size > max_bytes:
        raise ValueError(f"attachment too large ({info.st_size} bytes > {max_bytes})")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(raw, flags)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError(f"attachment changed while opening: {raw}")
        data = bytearray()
        while True:
            block = os.read(fd, min(64 * 1024, max_bytes + 1 - len(data)))
            if not block:
                break
            data.extend(block)
            if len(data) > max_bytes:
                raise ValueError(f"attachment too large (>{max_bytes} bytes)")
    finally:
        os.close(fd)
    return Path(raw).name, bytes(data)


def ingest_attachment_bytes(filename: str | None, data: bytes) -> AttachmentResult:
    name = sanitize_name(filename)
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"unsupported attachment type {ext or '(none)'}; supported: txt, md, csv, tsv, json, pdf, png, jpg, webp"
        )
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"{name} too large ({len(data)} bytes > {MAX_FILE_BYTES})")
    if any(data.startswith(magic) for magic in _ARCHIVE_OR_EXECUTABLE_MAGIC):
        raise ValueError(f"{name}: archives, office documents, executables, and RTF are not accepted")

    if ext in _TEXT_EXTENSIONS:
        text, kind = _decode_text(data, name), "text"
    elif ext in (".csv", ".tsv"):
        text, kind = _extract_delimited(data, name, "\t" if ext == ".tsv" else ","), "data"
    elif ext == ".json":
        text, kind = _extract_json(data, name), "data"
    elif ext == ".pdf":
        text, kind = _extract_pdf(data, name), "pdf"
    else:
        text, kind = _extract_image_ocr(data, name, ext), "image_ocr"

    if len(text) > MAX_EXTRACTED_CHARS:
        raise ValueError(f"{name}: extracted text exceeds {MAX_EXTRACTED_CHARS} characters")
    advisories = scan_advisories(text)
    chunks = tuple(chunk_text(text, source=name, max_total=MAX_EXTRACTED_CHARS))
    return AttachmentResult(name, kind, text, chunks, advisories, len(data))


def ingest_attachment_paths(paths: Iterable[str | os.PathLike[str]]) -> list[AttachmentResult]:
    paths = list(paths)
    if not paths:
        raise ValueError("no attachments provided")
    if len(paths) > MAX_FILES:
        raise ValueError(f"too many attachments ({len(paths)} > {MAX_FILES})")
    results, total = [], 0
    for path in paths:
        name, data = read_attachment_path(path)
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise ValueError(f"attachments exceed total byte cap ({total} > {MAX_TOTAL_BYTES})")
        results.append(ingest_attachment_bytes(name, data))
    return results


def combine_chunks(results: Iterable[AttachmentResult]) -> list[SourceChunk]:
    """Combine results while assigning globally unique citation IDs."""
    combined = []
    for result in results:
        for chunk in result.chunks:
            combined.append(SourceChunk(f"C{len(combined) + 1}", chunk.text, chunk.source))
    return combined


def enforce_remote_source_cap(chunks: Iterable[SourceChunk], question: str = "") -> None:
    total = len(question.encode("utf-8")) + sum(len(c.text.encode("utf-8")) for c in chunks)
    if total > MAX_REMOTE_SOURCE_BYTES:
        raise ValueError(
            f"prepared source is too large for remote analysis ({total} UTF-8 bytes > "
            f"{MAX_REMOTE_SOURCE_BYTES}); split the files into separate runs"
        )


def _decode_text(data: bytes, name: str) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    if b"\x00" in data:
        raise ValueError(f"{name}: NUL bytes are not valid text")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{name}: expected UTF-8 text") from exc
    head = text[:2048].lstrip().lower()
    if any(marker in head for marker in _HTML_XML_MARKERS):
        raise ValueError(f"{name}: HTML/XML documents are not accepted as plain text")
    sample = text[:8192]
    controls = sum(ch not in "\t\n\r" and unicodedata.category(ch) == "Cc" for ch in sample)
    if sample and controls / len(sample) > 0.02:
        raise ValueError(f"{name}: too many control characters")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _extract_delimited(data: bytes, name: str, delimiter: str) -> str:
    text = _decode_text(data, name)
    rows = []
    try:
        for index, row in enumerate(csv.reader(io.StringIO(text), delimiter=delimiter), start=1):
            if index > MAX_DATA_ROWS:
                raise ValueError(f"{name}: more than {MAX_DATA_ROWS} rows")
            if len(row) > MAX_DATA_COLUMNS:
                raise ValueError(f"{name}: row has more than {MAX_DATA_COLUMNS} columns")
            rows.append("\t".join(cell.replace("\t", "\\t").replace("\n", "\\n") for cell in row))
    except csv.Error as exc:
        raise ValueError(f"{name}: invalid delimited data: {exc}") from exc
    return "\n".join(rows)


def _extract_json(data: bytes, name: str) -> str:
    text = _decode_text(data, name)
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"{name}: invalid or excessively nested JSON") from exc
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    if len(rendered) > MAX_EXTRACTED_CHARS:
        raise ValueError(f"{name}: normalized JSON exceeds extracted-text cap")
    return rendered


def _extract_pdf(data: bytes, name: str) -> str:
    if not data.startswith(b"%PDF-"):
        raise ValueError(f"{name}: extension does not match PDF signature")
    if any(marker in data for marker in _UNSAFE_PDF_MARKERS):
        raise ValueError(
            f"{name}: active actions, scripts, forms, or embedded files are not accepted"
        )
    return _isolated_extract("pdf", data, name)


def _extract_pdf_worker(data: bytes, name: str) -> str:
    if not data.startswith(b"%PDF-"):
        raise ValueError(f"{name}: extension does not match PDF signature")
    if any(marker in data for marker in _UNSAFE_PDF_MARKERS):
        raise ValueError(
            f"{name}: active actions, scripts, forms, or embedded files are not accepted"
        )
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("PDF support requires the optional 'pypdf' package; install quorum-council[research]") from exc
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ValueError(f"{name}: encrypted PDFs are not supported")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError(f"{name}: too many PDF pages ({len(reader.pages)} > {MAX_PDF_PAGES})")
        pages, size = [], 0
        for number, page in enumerate(reader.pages, start=1):
            extracted = page.extract_text() or ""
            size += len(extracted)
            if size > MAX_EXTRACTED_CHARS:
                raise ValueError(f"{name}: PDF text exceeds extracted-text cap")
            pages.append(f"[Page {number}]\n{extracted.strip()}")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"{name}: malformed or unsupported PDF") from exc
    text = "\n\n".join(pages).strip()
    if not text:
        raise ValueError(f"{name}: no extractable text; scanned PDFs need a sandboxed OCR workflow")
    return text


def _extract_image_ocr(data: bytes, name: str, ext: str) -> str:
    return _isolated_extract(ext.lstrip("."), data, name)


def _extract_image_ocr_worker(data: bytes, name: str, ext: str) -> str:
    signatures = _IMAGE_MAGIC[ext]
    valid_magic = any(data.startswith(sig) for sig in signatures)
    if ext == ".webp":
        valid_magic = data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    if not valid_magic:
        raise ValueError(f"{name}: extension does not match image signature")
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:
        raise ValueError("image OCR requires optional packages Pillow and pytesseract plus the tesseract executable") from exc
    try:
        image = Image.open(io.BytesIO(data))
        image.verify()
        image = Image.open(io.BytesIO(data))
        width, height = image.size
        if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
            raise ValueError(f"{name}: image exceeds {MAX_IMAGE_PIXELS} pixels")
        if getattr(image, "n_frames", 1) != 1:
            raise ValueError(f"{name}: animated or multi-frame images are not supported")
        image.load()
    except ValueError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError(f"{name}: malformed or unsafe image") from exc
    try:
        import pytesseract
    except ImportError as exc:
        raise ValueError("image OCR requires optional 'pytesseract' and the tesseract executable") from exc
    try:
        text = pytesseract.image_to_string(image, timeout=20).strip()
    except RuntimeError as exc:
        raise ValueError(f"{name}: OCR timed out or failed") from exc
    except pytesseract.TesseractNotFoundError as exc:
        raise ValueError("image OCR requires the 'tesseract' executable on PATH") from exc
    if not text:
        raise ValueError(f"{name}: OCR found no text; visual reasoning is not supported by the text-only model interface")
    return text


def _isolated_extract(kind: str, data: bytes, name: str) -> str:
    """Parse risky formats in a locked-down, volume-free container."""
    docker = shutil.which("docker")
    if not docker:
        raise ValueError(
            f"{name}: secure {kind} extraction requires Docker and the {PARSER_IMAGE} image"
        )
    container = f"quorum-parser-{os.getpid()}-{os.urandom(6).hex()}"
    command = [
        docker, "run", "--rm", "--interactive", "--network", "none",
        "--name", container,
        "--log-driver", "none",
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--pids-limit", "32", "--memory", "1536m", "--memory-swap", "1536m",
        "--cpus", "1", "--ulimit", "nofile=64:64",
        "--ulimit", f"fsize={PARSER_OUTPUT_BYTES}:{PARSER_OUTPUT_BYTES}",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m", "--user", "65534:65534",
        PARSER_IMAGE, kind, name,
    ]

    try:
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
            start_new_session=True,
        )
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(data)
        process.stdin.close()
        fd = process.stdout.fileno()
        raw = bytearray()
        deadline = time.monotonic() + PARSER_TIMEOUT_SECONDS + 2
        while True:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(command, PARSER_TIMEOUT_SECONDS + 2)
            readable, _, _ = select.select([fd], [], [], 0.05)
            if readable:
                # Read at most one byte beyond the cap. No attacker-controlled
                # stdout is ever buffered to host memory/disk past that bound.
                chunk = os.read(fd, min(64 * 1024, PARSER_OUTPUT_BYTES + 1 - len(raw)))
                if chunk:
                    raw.extend(chunk)
                    if len(raw) > PARSER_OUTPUT_BYTES:
                        raise OverflowError
                elif process.poll() is not None:
                    break
            elif process.poll() is not None:
                # Poll once more for EOF/data already waiting in the pipe.
                final, _, _ = select.select([fd], [], [], 0)
                if not final:
                    break
        process.wait(timeout=2)
    except (subprocess.TimeoutExpired, OverflowError) as exc:
        process.kill()
        process.wait()
        subprocess.run(
            [docker, "rm", "-f", container],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=5, check=False,
        )
        reason = "output exceeded its cap" if isinstance(exc, OverflowError) else "timed out"
        raise ValueError(f"{name}: isolated parser {reason}") from None
    except OSError:
        if "process" in locals() and process.poll() is None:
            process.kill()
            process.wait()
        subprocess.run(
            [docker, "rm", "-f", container],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=5, check=False,
        )
        raise ValueError(f"{name}: isolated parser could not start") from None
    if process.returncode != 0:
        raise ValueError(
            f"{name}: isolated {kind.upper()} parser unavailable or rejected malformed/unsafe content; "
            f"build {PARSER_IMAGE} with the documented parser Dockerfile"
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
        text = payload["text"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
        raise ValueError(f"{name}: isolated parser returned invalid output") from None
    if not isinstance(text, str) or len(text) > MAX_EXTRACTED_CHARS:
        raise ValueError(f"{name}: isolated parser output exceeded text limits")
    return text


def _worker_extract(kind: str, data: bytes, name: str) -> str:
    """Entrypoint used only inside the isolated parser process."""
    if kind == "pdf":
        return _extract_pdf_worker(data, name)
    ext = "." + kind
    if ext in _IMAGE_EXTENSIONS:
        return _extract_image_ocr_worker(data, name, ext)
    raise ValueError("unsupported isolated parser kind")
