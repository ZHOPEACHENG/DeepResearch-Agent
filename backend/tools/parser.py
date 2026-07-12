"""
Multi-format document parser.

Extracts text content from PDF, DOCX, TXT, and Markdown files.
Used by the knowledge base pipeline (Phase 7 / US5).

Supports:
- PDF: pdfplumber (text layer extraction)
- DOCX: python-docx
- TXT/MD: plain text with encoding detection
"""

from __future__ import annotations

import io
import re
from pathlib import Path


def extract_text(file_path: str | Path, file_type: str) -> str:
    """Extract full text from a document file.

    Args:
        file_path: Path to the document file on disk.
        file_type: One of ``"pdf"``, ``"docx"``, ``"txt"``, ``"md"``.

    Returns:
        Extracted plain text.  Empty string on total failure.

    Raises:
        ValueError: If ``file_type`` is unsupported.
        FileNotFoundError: If ``file_path`` does not exist.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    ft = file_type.lower()
    if ft == "pdf":
        return _extract_pdf(file_path)
    elif ft == "docx":
        return _extract_docx(file_path)
    elif ft in ("txt", "md"):
        return _extract_text_file(file_path)
    else:
        raise ValueError(f"不支持的文件类型: {ft}")


def is_password_protected_pdf(file_data: bytes) -> bool:
    """Check whether a PDF byte stream is password-protected.

    Quick heuristic — pdfplumber will raise on password-protected files;
    this pre-check avoids an expensive full-extraction attempt.
    """
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(file_data)) as pdf:
            # If we can read the first page without error, it's not encrypted
            if pdf.pages:
                pdf.pages[0].extract_text()
            return False
    except Exception:
        # pdfplumber raises "File is encrypted" type errors
        return True


# ── Internal extractors ───────────────────────────────────────────────


def _extract_pdf(file_path: Path) -> str:
    """Extract text from a PDF using pdfplumber."""
    try:
        import pdfplumber
    except ImportError:
        raise ImportError("pdfplumber 未安装。请运行: pip install pdfplumber")

    texts: list[str] = []
    try:
        with pdfplumber.open(str(file_path)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    texts.append(page_text)
    except Exception as exc:
        msg = str(exc)
        if "encrypted" in msg.lower() or "password" in msg.lower():
            raise ValueError("PDF 文件受密码保护，无法提取文本")
        raise RuntimeError(f"PDF 文本提取失败: {msg}") from exc

    return "\n\n".join(texts)


def _extract_docx(file_path: Path) -> str:
    """Extract text from a DOCX file using python-docx."""
    try:
        from docx import Document as DocxDocument
    except ImportError:
        raise ImportError("python-docx 未安装。请运行: pip install python-docx")

    try:
        doc = DocxDocument(str(file_path))
    except Exception as exc:
        raise RuntimeError(f"DOCX 文件解析失败: {exc}") from exc

    paragraphs: list[str] = []
    for para in doc.paragraphs:
        if para.text.strip():
            paragraphs.append(para.text)

    # Also extract text from tables
    for table in doc.tables:
        for row in table.rows:
            row_texts = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if row_texts:
                paragraphs.append(" | ".join(row_texts))

    return "\n\n".join(paragraphs)


def _extract_text_file(file_path: Path) -> str:
    """Extract text from a plain-text file (TXT or Markdown).

    Tries UTF-8 first, then common CJK encodings.
    """
    # Ordered list of encodings to try
    encodings = ["utf-8", "utf-8-sig", "gbk", "gb2312", "gb18030", "latin-1"]
    last_error: Exception | None = None

    for enc in encodings:
        try:
            return file_path.read_text(encoding=enc)
        except (UnicodeDecodeError, UnicodeError) as e:
            last_error = e
            continue
        except Exception as e:
            last_error = e
            continue

    # Last resort: read as bytes and decode with errors='replace'
    try:
        raw = file_path.read_bytes()
        return raw.decode("utf-8", errors="replace")
    except Exception:
        if last_error:
            raise RuntimeError(f"文本文件编码检测失败: {last_error}") from last_error
        raise RuntimeError("无法读取文本文件")


# ── Markdown stripping (for TXT/MD → plain text extraction) ──────────


_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_MD_ITALIC_RE = re.compile(r"[*_]([^*_]+)[*_]")
_MD_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
_MD_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_MD_BLOCKQUOTE_RE = re.compile(r"^>\s?", re.MULTILINE)
_MD_LIST_RE = re.compile(r"^[\s]*[-*+]\s+", re.MULTILINE)
_MD_NUM_LIST_RE = re.compile(r"^[\s]*\d+\.\s+", re.MULTILINE)
_MD_HR_RE = re.compile(r"^[-*_]{3,}\s*$", re.MULTILINE)


def strip_markdown(text: str) -> str:
    """Strip common Markdown syntax to produce cleaner plain text.

    Used as a pre-processing step before chunking markdown documents so
    that the indexed text is more readable for keyword search.
    """
    # Remove code blocks first (they may contain special chars)
    text = _MD_CODE_BLOCK_RE.sub(" ", text)
    # Remove images
    text = _MD_IMAGE_RE.sub(r"\1", text)
    # Convert links to just the link text
    text = _MD_LINK_RE.sub(r"\1", text)
    # Remove heading markers (keep heading text)
    text = _MD_HEADING_RE.sub("", text)
    # Bold / italic → keep text only
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = _MD_ITALIC_RE.sub(r"\1", text)
    # Inline code → keep code text
    text = _MD_INLINE_CODE_RE.sub(r"\1", text)
    # Blockquote marker
    text = _MD_BLOCKQUOTE_RE.sub("", text)
    # List markers
    text = _MD_LIST_RE.sub("", text)
    text = _MD_NUM_LIST_RE.sub("", text)
    # Horizontal rules
    text = _MD_HR_RE.sub("", text)
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
