"""
Report export utilities — Markdown → HTML → PDF pipeline.

Phase 8' / US6.  PDF generation uses ``fpdf2`` (pure Python, no system
dependencies) so it works on Windows / macOS / Linux without any extra
runtime libraries.
"""

from __future__ import annotations

import re
from pathlib import Path


def markdown_to_html(md: str, title: str = "研究报告") -> str:
    """Convert Markdown to a self-contained, print-friendly HTML document."""
    import markdown as md_lib

    body = md_lib.markdown(
        md, extensions=["fenced_code", "tables", "codehilite", "toc"],
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: "Noto Sans CJK SC", "SimSun", serif; font-size: 11pt;
         line-height: 1.85; max-width: 780px; margin: 40px auto; padding: 0 20px;
         color: #222; }}
  h1 {{ font-size: 20pt; border-bottom: 2px solid #333; padding-bottom: 8px; }}
  h2 {{ font-size: 14pt; margin-top: 28px; }}
  h3 {{ font-size: 12pt; margin-top: 20px; }}
  blockquote {{ border-left: 3px solid #ccc; margin-left: 0; padding-left: 16px;
                color: #555; }}
  pre {{ background: #f5f5f5; padding: 12px; border-radius: 4px;
         overflow-x: auto; font-size: 10pt; }}
  code {{ background: #f0f0f0; padding: 1px 4px; border-radius: 3px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
  th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
  th {{ background: #f5f5f5; }}
  @page {{ margin: 2cm; size: A4; @bottom-center {{ content: counter(page); }} }}
</style>
</head>
<body>
{body}
</body>
</html>"""


def html_to_pdf(html: str) -> bytes:
    """Render HTML to PDF via WeasyPrint."""
    from weasyprint import HTML
    return HTML(string=html).write_pdf()


# ═══════════════════════════════════════════════════════════════════
# fpdf2-based PDF (zero system deps, cross-platform)
# ═══════════════════════════════════════════════════════════════════

_PAGE_W = 210
_PAGE_H = 297
_MARGIN = 20
_BODY_W = _PAGE_W - 2 * _MARGIN

# Common system font paths for CJK (searched in order)
_CJK_FONT_CANDIDATES = [
    # Windows
    "C:/Windows/Fonts/msyh.ttc",         # 微软雅黑
    "C:/Windows/Fonts/simsun.ttc",       # 宋体
    "C:/Windows/Fonts/simhei.ttf",       # 黑体
    "C:/Windows/Fonts/kaiu.ttf",         # 楷体
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]


def _find_cjk_font() -> str | None:
    """Return the first available CJK-capable font path, or None."""
    for path in _CJK_FONT_CANDIDATES:
        if Path(path).exists():
            return path
    return None


# Simple regex to strip common Markdown syntax for plain-text PDF rendering
_MD_HEAD = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_CODE = re.compile(r"`([^`]+)`")


def markdown_to_pdf_bytes(md: str, title: str = "研究报告") -> bytes:
    """Convert Markdown to PDF bytes using fpdf2 (pure Python).

    No system libraries required — works on Windows, macOS, and Linux.
    Raises RuntimeError if no CJK-capable system font is found.
    """
    font_path = _find_cjk_font()
    if font_path is None:
        raise RuntimeError(
            "PDF 生成失败：未找到中文字体。请安装文泉驿微米黑 (wqy-microhei) "
            "或 Noto Sans CJK 字体后重试。"
        )

    from fpdf import FPDF

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_font("cjk", "", font_path, uni=True)
    pdf.add_font("cjk", "B", font_path, uni=True)
    pdf.add_page()

    for block in _parse_md_blocks(md):
        _render_block(pdf, block)

    return bytes(pdf.output())


def _parse_md_blocks(md: str) -> list[dict]:
    """Parse Markdown into a list of {kind, text, level} blocks."""
    blocks: list[dict] = []
    for line in md.split("\n"):
        stripped = line.strip()
        if not stripped:
            blocks.append({"kind": "blank", "text": "", "level": 0})
        elif stripped.startswith("# "):
            blocks.append({"kind": "h1", "text": stripped[2:], "level": 1})
        elif stripped.startswith("## "):
            blocks.append({"kind": "h2", "text": stripped[3:], "level": 2})
        elif stripped.startswith("### "):
            blocks.append({"kind": "h3", "text": stripped[4:], "level": 3})
        elif stripped.startswith("> "):
            blocks.append({"kind": "quote", "text": stripped[2:], "level": 0})
        elif stripped.startswith("- ") or stripped.startswith("* "):
            blocks.append({"kind": "bullet", "text": stripped[2:], "level": 0})
        elif re.match(r"^\d+\.\s", stripped):
            blocks.append({"kind": "num", "text": re.sub(r"^\d+\.\s", "", stripped), "level": 0})
        elif stripped.startswith("---"):
            blocks.append({"kind": "hr", "text": "", "level": 0})
        else:
            blocks.append({"kind": "para", "text": stripped, "level": 0})
    return blocks


def _render_block(pdf, block: dict) -> None:
    """Render a single parsed block onto the PDF."""
    kind = block["kind"]
    text = _clean_text(block["text"])

    if kind == "blank":
        pdf.ln(4)
    elif kind == "h1":
        pdf.ln(6)
        pdf.set_font("cjk", "B", 18)
        pdf.multi_cell(_BODY_W, 10, text)
        pdf.set_draw_color(50, 50, 50)
        pdf.line(_MARGIN, pdf.get_y() + 2, _PAGE_W - _MARGIN, pdf.get_y() + 2)
        pdf.ln(6)
    elif kind == "h2":
        pdf.ln(4)
        pdf.set_font("cjk", "B", 14)
        pdf.multi_cell(_BODY_W, 8, text)
        pdf.ln(2)
    elif kind == "h3":
        pdf.ln(3)
        pdf.set_font("cjk", "B", 12)
        pdf.multi_cell(_BODY_W, 7, text)
        pdf.ln(2)
    elif kind == "quote":
        pdf.set_font("cjk", "", 10)
        pdf.set_text_color(80, 80, 80)
        pdf.set_x(_MARGIN + 6)
        pdf.multi_cell(_BODY_W - 6, 6, text)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(2)
    elif kind in ("bullet", "num"):
        pdf.set_font("cjk", "", 10)
        prefix = "• " if kind == "bullet" else f"{text[:2] if text[:1].isdigit() else '• '} "
        pdf.set_x(_MARGIN + 4)
        pdf.multi_cell(_BODY_W - 4, 6, prefix + text)
        pdf.ln(1)
    elif kind == "hr":
        pdf.ln(4)
        pdf.set_draw_color(180, 180, 180)
        pdf.line(_MARGIN, pdf.get_y(), _PAGE_W - _MARGIN, pdf.get_y())
        pdf.ln(4)
    elif kind == "para":
        pdf.set_font("cjk", "", 10)
        pdf.multi_cell(_BODY_W, 6, text)
        pdf.ln(2)


def _clean_text(text: str) -> str:
    """Strip residual Markdown syntax from a text line."""
    text = _MD_HEAD.sub("", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_BOLD.sub(r"\1", text)
    text = _MD_CODE.sub(r"\1", text)
    return text
