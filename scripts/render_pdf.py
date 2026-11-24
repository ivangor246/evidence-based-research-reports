#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Render a source-linked research report from UTF-8 JSON to PDF.

Dependencies: reportlab, pypdf, and a locally available TrueType font covering the report's writing system.
No network requests, remote images, HTML input or shell commands are used.
"""

from __future__ import annotations

import argparse
import html
import json
import glob
import unicodedata
import os
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents


NAVY = colors.HexColor("#142740")
BLUE = colors.HexColor("#2463A0")
MUTED = colors.HexColor("#5C6A7A")
PALE = colors.HexColor("#EFF5FA")
SOURCE_PATTERN = re.compile(r"\[(S\d+)\]")
CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

LABELS = {
    "en": {
        "kicker": "RESEARCH REPORT", "date": "Date", "scope": "Scope",
        "summary": "Executive summary", "findings": "Key findings",
        "methodology": "Methods and verification",
        "limitations": "Limitations and uncertainty", "sources": "Sources",
        "published": "Published", "accessed": "Accessed",
        "contents": "Contents", "page": "Page",
    }
}


def clean(value: object) -> str:
    return CONTROL_PATTERN.sub(" ", str(value if value is not None else "")).strip()


def is_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("https", "http") and bool(parsed.netloc)


def validate(report: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(report, dict):
        return ["Root must be a JSON object"]
    if not clean(report.get("title")):
        errors.append("title: expected a nonempty string")
    if not isinstance(report.get("language", "en"), str) or not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", report.get("language", "en")):
        errors.append("language: use a valid BCP 47 language tag")
    if "labels" in report and not isinstance(report["labels"], dict):
        errors.append("labels: expected an object")
    if "contents" in report and not isinstance(report["contents"], bool):
        errors.append("contents: expected true or false")
    for name in ("summary", "findings", "sections", "methodology", "limitations", "sources"):
        if name in report and not isinstance(report[name], list):
            errors.append(f"{name}: expected an array")
    if errors:
        return errors
    if not report.get("summary"):
        errors.append("summary: include a concise summary suited to the task")
    if not report.get("sections"):
        errors.append("sections: include at least one research section")

    source_ids: set[str] = set()
    used_source_ids: set[str] = set()
    for i, src in enumerate(report.get("sources", [])):
        path = f"sources[{i}]"
        if not isinstance(src, dict):
            errors.append(f"{path}: expected an object")
            continue
        sid = clean(src.get("id"))
        if not re.fullmatch(r"S\d+", sid):
            errors.append(f"{path}.id: expected S1, S2, ...")
        if sid in source_ids:
            errors.append(f"{path}.id: duplicate {sid}")
        source_ids.add(sid)
        if not clean(src.get("title")):
            errors.append(f"{path}.title: required")
        if src.get("url") and not is_http_url(clean(src["url"])):
            errors.append(f"{path}.url: expected an HTTP(S) source URL")

    def check_inline_refs(text: object, path: str) -> None:
        for sid in SOURCE_PATTERN.findall(clean(text)):
            if sid not in source_ids:
                errors.append(f"{path}: unknown source {sid}")
            else:
                used_source_ids.add(sid)

    def check_refs(item: dict, path: str) -> None:
        basis = item.get("basis")
        if basis is not None and basis not in ("fact", "calculation", "inference", "hypothesis"):
            errors.append(f"{path}.basis: use fact, calculation, inference or hypothesis")
        refs = item.get("sources", [])
        if not isinstance(refs, list):
            errors.append(f"{path}.sources: expected an array")
            return
        if basis in ("fact", "calculation", "inference") and not refs:
            errors.append(f"{path}: {basis} needs source or data-origin references")
        for sid in refs:
            if sid not in source_ids:
                errors.append(f"{path}: unknown source {sid}")
            else:
                used_source_ids.add(sid)
        check_inline_refs(item.get("text", ""), path)

    for i, item in enumerate(report.get("summary", [])):
        if isinstance(item, dict):
            check_refs(item, f"summary[{i}]")
        elif isinstance(item, str):
            check_inline_refs(item, f"summary[{i}]")
        else:
            errors.append(f"summary[{i}]: expected text or a claim object")

    for i, item in enumerate(report.get("findings", [])):
        if not isinstance(item, dict) or not clean(item.get("title")) or not clean(item.get("text")):
            errors.append(f"findings[{i}]: expected title and text")
            continue
        check_refs(item, f"findings[{i}]")

    for i, section in enumerate(report.get("sections", [])):
        if not isinstance(section, dict) or not clean(section.get("title")):
            errors.append(f"sections[{i}]: expected title")
            continue
        blocks = section.get("blocks", [])
        if not isinstance(blocks, list):
            errors.append(f"sections[{i}].blocks: expected an array")
            continue
        for j, block in enumerate(blocks):
            path = f"sections[{i}].blocks[{j}]"
            if not isinstance(block, dict):
                errors.append(f"{path}: expected an object")
                continue
            kind = block.get("type")
            if kind not in ("paragraph", "bullets", "table", "subheading", "callout"):
                errors.append(f"{path}.type: unknown block type {kind}")
                continue
            if kind == "bullets":
                if not isinstance(block.get("items"), list):
                    errors.append(f"{path}.items: expected an array")
                else:
                    for k, bullet in enumerate(block["items"]):
                        if isinstance(bullet, dict):
                            check_refs(bullet, f"{path}.items[{k}]")
                        elif isinstance(bullet, str):
                            check_inline_refs(bullet, f"{path}.items[{k}]")
                        else:
                            errors.append(f"{path}.items[{k}]: expected text or a claim object")
            elif kind == "table":
                headers, rows = block.get("headers"), block.get("rows")
                if not isinstance(headers, list) or not 1 <= len(headers) <= 5:
                    errors.append(f"{path}.headers: expected 1–5 columns")
                if not isinstance(rows, list) or not rows:
                    errors.append(f"{path}.rows: expected nonempty array")
                elif isinstance(headers, list):
                    for k, row in enumerate(rows):
                        if not isinstance(row, list) or len(row) != len(headers):
                            errors.append(f"{path}.rows[{k}]: expected {len(headers)} cells")
                            continue
                        for cell in row:
                            for sid in SOURCE_PATTERN.findall(clean(cell)):
                                if sid not in source_ids:
                                    errors.append(f"{path}: unknown inline source {sid}")
                table_refs = block.get("sources", [])
                if not isinstance(table_refs, list):
                    errors.append(f"{path}.sources: expected an array")
                    table_refs = []
                for sid in table_refs:
                    if sid not in source_ids:
                        errors.append(f"{path}: unknown source {sid}")
                    else:
                        used_source_ids.add(sid)
                cell_refs = {
                    sid for row in (rows if isinstance(rows, list) else [])
                    if isinstance(row, list) for cell in row
                    for sid in SOURCE_PATTERN.findall(clean(cell))
                }
                if not cell_refs.issubset(set(table_refs)):
                    errors.append(f"{path}.sources: include every source cited in table cells")
                widths = block.get("col_widths")
                header_count = len(headers) if isinstance(headers, list) else 0
                if widths is not None and (
                    not isinstance(widths, list) or len(widths) != header_count
                    or any(not isinstance(w, (int, float)) or isinstance(w, bool) or w <= 0 for w in widths)
                ):
                    errors.append(f"{path}.col_widths: use positive widths matching the columns")
            else:
                if not clean(block.get("text")):
                    errors.append(f"{path}.text: required")
                check_refs(block, path)
    for field in ("methodology", "limitations"):
        for index, value in enumerate(report.get(field, [])):
            check_inline_refs(value, f"{field}[{index}]")
    for sid in sorted(source_ids - used_source_ids):
        errors.append(f"sources: {sid} is unused; remove it or cite it")
    return errors



FONT_COVERAGE: dict[str, set[int]] = {}
FONT_FALLBACK_ORDER: list[str] = []


def find_font(language: str = "en") -> tuple[str, str]:
    global FONT_COVERAGE, FONT_FALLBACK_ORDER
    FONT_COVERAGE = {}
    FONT_FALLBACK_ORDER = []
    lang = language.lower().replace("_", "-")

    # ReportLab's built-in CID fonts cover CJK without relying on a host font.
    cid_faces = None
    if lang.startswith("ja"):
        cid_faces = ("HeiseiMin-W3", "HeiseiKakuGo-W5")
    elif lang.startswith(("zh-hant", "zh-tw", "zh-hk")):
        cid_faces = ("MSung-Light", "MSung-Light")
    elif lang.startswith("zh"):
        cid_faces = ("STSong-Light", "STSong-Light")
    elif lang.startswith("ko"):
        cid_faces = ("HYSMyeongJo-Medium", "HYSMyeongJo-Medium")
    if cid_faces:
        regular_cid = UnicodeCIDFont(cid_faces[0])
        regular_cid.fontName = "Research"
        regular_cid.name = "Research"
        bold_cid = UnicodeCIDFont(cid_faces[1])
        bold_cid.fontName = "Research-Bold"
        bold_cid.name = "Research-Bold"
        pdfmetrics.registerFont(regular_cid)
        pdfmetrics.registerFont(bold_cid)
        pdfmetrics.registerFontFamily("Research", normal="Research", bold="Research-Bold")
        return "Research", "Research-Bold"

    regular = os.environ.get("RESEARCH_REPORT_FONT", "")
    bold = os.environ.get("RESEARCH_REPORT_FONT_BOLD", "")
    if not regular:
        candidates = [
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ]
        regular = next((p for p in candidates if Path(p).is_file()), "")
    if not bold:
        candidates = [
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ]
        bold = next((p for p in candidates if Path(p).is_file()), regular)
    if not regular or not Path(regular).is_file():
        raise RuntimeError("No base TrueType font found; set RESEARCH_REPORT_FONT")

    pdfmetrics.registerFont(TTFont("Research", regular))
    pdfmetrics.registerFont(TTFont("Research-Bold", bold if Path(bold).is_file() else regular))
    pdfmetrics.registerFontFamily("Research", normal="Research", bold="Research-Bold")
    FONT_COVERAGE = {"Research": set(TTFont("ResearchCoverage", regular).face.charWidths)}

    fallback_paths = []
    env_paths = os.environ.get("RESEARCH_REPORT_FONTS", "")
    if env_paths:
        fallback_paths.extend(p for p in env_paths.split(os.pathsep) if p)
    fallback_paths.extend(glob.glob("/usr/share/fonts/truetype/noto/NotoSans*-Regular.ttf"))
    fallback_paths.extend([
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ])
    seen = {os.path.realpath(regular)}
    index = 0
    for path in fallback_paths:
        if not Path(path).is_file() or os.path.realpath(path) in seen:
            continue
        seen.add(os.path.realpath(path))
        try:
            font_name = f"Research-Fallback-{index}"
            font = TTFont(font_name, path)
            coverage = set(font.face.charWidths)
            if not coverage:
                continue
            pdfmetrics.registerFont(font)
            FONT_COVERAGE[font_name] = coverage
            FONT_FALLBACK_ORDER.append(font_name)
            index += 1
        except Exception:
            continue
    FONT_FALLBACK_ORDER.insert(0, "Research")
    return "Research", "Research-Bold"


def styles() -> dict[str, ParagraphStyle]:
    def make(name: str, **kwargs: object) -> ParagraphStyle:
        kwargs.setdefault("fontName", "Research")
        kwargs.setdefault("textColor", NAVY)
        return ParagraphStyle(name, **kwargs)

    return {
        "kicker": make("research-kicker", fontSize=8, leading=11, textColor=BLUE, spaceAfter=8),
        "title": make("research-title", fontName="Research-Bold", fontSize=21, leading=27, spaceAfter=12),
        "subtitle": make("research-subtitle", fontSize=11, leading=16, textColor=MUTED, spaceAfter=13),
        "h1": make("research-h1", fontName="Research-Bold", fontSize=14, leading=19, spaceBefore=17, spaceAfter=9, keepWithNext=True),
        "h2": make("research-h2", fontName="Research-Bold", fontSize=11, leading=15, spaceBefore=9, spaceAfter=6, keepWithNext=True),
         "body": make("research-body", fontSize=9.8, leading=14.6, spaceAfter=7, allowWidows=0, allowOrphans=0),
        "body-keep": make("research-body-keep", fontSize=9.8, leading=14.6, spaceAfter=7, keepWithNext=True, allowWidows=0, allowOrphans=0),
        "meta": make("research-meta", fontSize=8.5, leading=13, textColor=MUTED, spaceAfter=6),
        "bullet": make("research-bullet", fontSize=9.6, leading=14, leftIndent=17, firstLineIndent=-12, spaceAfter=4),
        "table-head": make("research-table-head", fontName="Research-Bold", fontSize=8.2, leading=11, textColor=colors.white),
        "table-cell": make("research-table-cell", fontSize=8.1, leading=11.4),
         "small": make("research-small", fontSize=8, leading=11.5, textColor=MUTED, spaceAfter=4),
        "source": make("research-source", fontSize=8.2, leading=11.5, spaceAfter=5),
        "toc": make("research-toc", fontSize=9.5, leading=14, leftIndent=0, firstLineIndent=0, spaceBefore=3),
        "section": make("research-section", fontName="Research-Bold", fontSize=14, leading=19, spaceBefore=17, spaceAfter=9, keepWithNext=True),
        "url": make("research-url", fontSize=7.2, leading=10.5, textColor=BLUE, wordWrap="CJK", spaceAfter=7),
    }



class TOCHeading(Paragraph):
    def __init__(self, text: str, style: ParagraphStyle, key: str):
        super().__init__(text, style)
        self.toc_key = key


class ResearchDocTemplate(SimpleDocTemplate):
    def afterFlowable(self, flowable: object) -> None:
        if isinstance(flowable, TOCHeading):
            title = flowable.getPlainText()
            key = flowable.toc_key
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(title, key, 0, False)
            self.notify("TOCEntry", (0, title, self.page, key))


def render(report: dict, output: Path) -> None:
    lang = report.get("language", "en")
    find_font(lang)
    rtl_languages = ("ar", "fa", "ur", "he", "ps", "sd", "ug", "yi")
    is_rtl = str(lang).lower().split("-")[0] in rtl_languages
    rtl_tools = None
    if is_rtl:
        try:
            import arabic_reshaper
            from bidi.algorithm import get_display
            rtl_tools = (arabic_reshaper, get_display)
        except ImportError as exc:
            raise RuntimeError(
                "Right-to-left PDF text requires arabic-reshaper and python-bidi; "
                "install the skill requirements first."
            ) from exc
    sty = styles()
    label = dict(LABELS["en"])
    label.update(report.get("labels", {}))
    sources = {str(x["id"]): x for x in report.get("sources", [])}

    def inline_refs(ids: object) -> str:
        out = []
        for sid in ids if isinstance(ids, list) else []:
            src = sources.get(str(sid), {})
            url = clean(src.get("url"))
            token = html.escape(f"[{sid}]")
            if is_http_url(url):
                out.append(f'<link href="{html.escape(url, quote=True)}" color="#2463A0">{token}</link>')
            else:
                out.append(token)
        return " ".join(out)

    def font_text(value: str) -> str:
        if rtl_tools:
            reshaper, get_display = rtl_tools
            if is_rtl and any(unicodedata.bidirectional(ch) in ("R", "AL", "AN") for ch in value):
                base_lang = str(lang).lower().split("-")[0]
                if base_lang in ("ar", "fa", "ur", "ps", "sd", "ug"):
                    value = reshaper.reshape(value)
                value = get_display(value)
        if not FONT_COVERAGE:
            return html.escape(value).replace("\n", "<br/>")
        result = []
        current_font = None
        current = []

        def flush() -> None:
            nonlocal current_font, current
            if not current:
                return
            text = html.escape("".join(current)).replace("\n", "<br/>")
            if current_font and current_font != "Research":
                result.append(f'<font name="{current_font}">{text}</font>')
            else:
                result.append(text)
            current_font, current = None, []

        for char in value:
            code = ord(char)
            chosen = next(
                (name for name in FONT_FALLBACK_ORDER
                 if code in FONT_COVERAGE.get(name, set())),
                None
            )
            if chosen is None:
                if code in (9, 10, 13):
                    chosen = "Research"
                else:
                    raise RuntimeError(
                        f"No installed PDF font supports U+{code:04X} ({char!r}). "
                        "Install a suitable font or set RESEARCH_REPORT_FONTS."
                    )
            if current_font is not None and chosen != current_font:
                flush()
            current_font = chosen
            current.append(char)
        flush()
        return "".join(result)

    def markup(value: object) -> str:
        raw = clean(value)
        parts = []
        cursor = 0
        for match in SOURCE_PATTERN.finditer(raw):
            parts.append(font_text(raw[cursor:match.start()]))
            parts.append(inline_refs([match.group(1)]))
            cursor = match.end()
        parts.append(font_text(raw[cursor:]))
        return "".join(parts)

    def claim(item: object) -> str:
        if isinstance(item, str):
            return markup(item)
        cited_in_text = set(SOURCE_PATTERN.findall(clean(item.get("text", ""))))
        suffix = inline_refs([sid for sid in item.get("sources", []) if sid not in cited_in_text])
        return markup(item.get("text", "")) + ("  " + suffix if suffix else "")

    output.parent.mkdir(parents=True, exist_ok=True)
    doc = ResearchDocTemplate(
        str(output), pagesize=A4, leftMargin=48, rightMargin=48,
        topMargin=48, bottomMargin=48, title=clean(report["title"]),
        author="Evidence-Based Research & Reports",
    )
    story: list[object] = []
    story.append(Paragraph(markup(label["kicker"]), sty["kicker"]))
    story.append(Paragraph(markup(report["title"]), sty["title"]))
    if report.get("subtitle"):
        story.append(Paragraph(markup(report["subtitle"]), sty["subtitle"]))
    meta_parts = [f'{label["date"]}: {clean(report.get("date", date.today().isoformat()))}']
    if report.get("scope"):
        meta_parts.append(f'{label["scope"]}: {clean(report["scope"])}')
    story.append(Paragraph(markup("  |  ".join(meta_parts)), sty["meta"]))
    story.append(HRFlowable(width="100%", thickness=1.2, color=BLUE, spaceBefore=6, spaceAfter=12))

    story.append(Paragraph(markup(label["summary"]), sty["h1"]))
    for part in report.get("summary", []):
        story.append(Paragraph(claim(part), sty["body"]))

    if report.get("findings"):
        story.append(Paragraph(markup(label["findings"]), sty["h1"]))
        for i, item in enumerate(report["findings"], 1):
            title = Paragraph(f"<b>{i}. {markup(item['title'])}</b>", sty["h2"])
            body = Paragraph(claim(item), sty["body"])
            story.append(KeepTogether([title, body]))

    def count_words(value: object) -> int:
        if isinstance(value, str):
            return len(re.findall(r"\b[\w’'-]+\b", value, flags=re.UNICODE))
        if isinstance(value, dict):
            return sum(count_words(v) for k, v in value.items() if k not in ("sources", "url"))
        if isinstance(value, list):
            return sum(count_words(v) for v in value)
        return 0

    body_fields = [report.get(key, []) for key in
                   ("title", "subtitle", "scope", "summary", "findings",
                    "sections", "methodology", "limitations")]
    body_words = sum(count_words(value) for value in body_fields)
    body_chars = sum(len(re.sub(r"\s+", "", str(value))) for value in body_fields)
    section_count = len(report.get("sections", []))
    toc_default = (body_words >= 1600 or body_chars >= 9000
                   or (section_count >= 9 and (body_words >= 900 or body_chars >= 5000)))
    toc_enabled = report.get("contents", toc_default)
    toc = None
    if toc_enabled:
        story.append(PageBreak())
        story.append(Paragraph(markup(label["contents"]), sty["h1"]))
        toc = TableOfContents()
        toc.levelStyles = [sty["toc"]]
        story.append(toc)
        story.append(PageBreak())

    def build_table_items(block: dict) -> list[object]:
        headers = block["headers"]
        cells = [[Paragraph(markup(v), sty["table-head"]) for v in headers]]
        cells.extend([[Paragraph(markup(v), sty["table-cell"]) for v in row]
                      for row in block["rows"]])
        weights = block.get("col_widths")
        if weights:
            total = sum(weights)
            col_widths = [doc.width * weight / total for weight in weights]
        else:
            col_widths = [doc.width / len(headers)] * len(headers)
        table = LongTable(cells, colWidths=col_widths,
                          repeatRows=1, hAlign="LEFT", splitByRow=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#D7E2ED")),
        ]))
        items: list[object] = [table, Spacer(1, 6)]
        caption = markup(block.get("caption", ""))
        table_text = " ".join(clean(cell) for row in block["rows"] for cell in row)
        cited_in_cells = set(SOURCE_PATTERN.findall(table_text))
        refs = inline_refs([sid for sid in block.get("sources", []) if sid not in cited_in_cells])
        if caption or refs:
            items.append(Paragraph(" ".join(x for x in (caption, refs) if x), sty["small"]))
        return items

    for section_index, section in enumerate(report.get("sections", []), 1):
        section_heading = TOCHeading(markup(section["title"]), sty["section"], f"section-{section_index}")
        blocks = section.get("blocks", [])
        block_index = 0
        if (len(blocks) > 1 and blocks[0].get("type") == "paragraph"
                and blocks[1].get("type") == "table"):
            first_paragraph = Paragraph(claim(blocks[0]), sty["body"])
            story.append(KeepTogether([section_heading, first_paragraph] + build_table_items(blocks[1])))
            block_index = 2
        else:
            story.append(section_heading)
        while block_index < len(blocks):
            block = blocks[block_index]
            kind = block["type"]
            if kind == "paragraph":
                paragraph = Paragraph(claim(block), sty["body"])
                if block_index + 1 < len(blocks) and blocks[block_index + 1].get("type") == "table":
                    story.append(KeepTogether([paragraph] + build_table_items(blocks[block_index + 1])))
                    block_index += 2
                    continue
                story.append(paragraph)
            elif kind == "subheading":
                story.append(Paragraph(markup(block["text"]), sty["h2"]))
            elif kind == "bullets":
                for item in block.get("items", []):
                    story.append(Paragraph("•  " + claim(item), sty["bullet"]))
                story.append(Spacer(1, 5))
            elif kind == "callout":
                title = markup(block.get("title", ""))
                content = f"<b>{title}</b><br/>{claim(block)}" if title else claim(block)
                box = Table([[Paragraph(content, sty["body"])]], colWidths=[doc.width])
                box.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), PALE),
                    ("LINEBEFORE", (0, 0), (0, -1), 3, BLUE),
                    ("LEFTPADDING", (0, 0), (-1, -1), 13),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                    ("TOPPADDING", (0, 0), (-1, -1), 9),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]))
                story.extend([box, Spacer(1, 10)])
            elif kind == "table":
                story.append(KeepTogether(build_table_items(block)))
            block_index += 1

    for field, heading in (("methodology", label["methodology"]),
                           ("limitations", label["limitations"])):
        if report.get(field):
            story.append(Paragraph(heading, sty["h1"]))
            for line in report[field]:
                story.append(Paragraph("•  " + markup(line), sty["bullet"]))

    if report.get("sources"):
        story.append(Paragraph(markup(label["sources"]), sty["h1"]))
        for src in report["sources"]:
            sid = clean(src["id"])
            url = clean(src.get("url"))
            title = markup(src["title"])
            title_markup = f'<link href="{html.escape(url, quote=True)}" color="#2463A0">{title}</link>' if is_http_url(url) else title
            details = [clean(src.get("publisher"))]
            if src.get("published"):
                details.append(f'{label["published"]}: {clean(src["published"])}')
            if src.get("accessed"):
                details.append(f'{label["accessed"]}: {clean(src["accessed"])}')
            metadata = html.escape(" · ".join(v for v in details if v))
            line = f"<b>[{sid}]</b> {title_markup}"
            if metadata:
                line += f' <font color="#5C6A7A">— {metadata}</font>'
            story.append(Paragraph(line, sty["source"]))

    def footer(canvas: object, current_doc: object) -> None:
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#D6E0EA"))
        canvas.line(doc.leftMargin, 35, A4[0] - doc.rightMargin, 35)
        canvas.setFillColor(MUTED)
        canvas.setFont("Research", 7.8)
        short_title = clean(report["title"])
        if len(short_title) > 65:
            short_title = short_title[:62] + "..."
        canvas.drawString(doc.leftMargin, 22, short_title)
        canvas.drawRightString(A4[0] - doc.rightMargin, 22,
                               f'{label["page"]} {current_doc.page}')
        canvas.restoreState()

    if toc_enabled:
        doc.multiBuild(story, onFirstPage=footer, onLaterPages=footer)
    else:
        doc.build(story, onFirstPage=footer, onLaterPages=footer)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="UTF-8 report JSON")
    parser.add_argument("output", type=Path, nargs="?", help="Output PDF")
    parser.add_argument("--check-only", action="store_true", help="Validate without writing PDF")
    args = parser.parse_args()
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"Cannot read report: {exc}", file=sys.stderr)
        return 2
    try:
        problems = validate(report)
    except (TypeError, ValueError, KeyError, AttributeError, IndexError) as exc:
        print(f"INVALID: malformed report structure ({type(exc).__name__})", file=sys.stderr)
        return 2
    if problems:
        for problem in problems:
            print(f"INVALID: {problem}", file=sys.stderr)
        return 2
    if args.check_only:
        print("VALID: report data and source references")
        return 0
    if not args.output:
        parser.error("output PDF path is required unless --check-only is used")
    try:
        render(report, args.output)
        from pypdf import PdfReader

        pdf = PdfReader(str(args.output))
        if not pdf.pages or not any((p.extract_text() or "").strip() for p in pdf.pages):
            raise RuntimeError("Generated PDF contains no extractable text")
    except Exception as exc:
        print(f"PDF generation failed: {exc}", file=sys.stderr)
        return 1
    print(f"CREATED: {args.output.resolve()} ({len(pdf.pages)} pages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
