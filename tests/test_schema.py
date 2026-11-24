import json
import subprocess
import sys
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).parents[1]
RENDERER = ROOT / "scripts" / "render_pdf.py"
TEST_REPORT = Path(__file__).parent / "_test_report.json"
TEST_PDF = Path(__file__).parent / "_test_report.pdf"


def run(report, *args):
    TEST_REPORT.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    try:
        return subprocess.run(
            [sys.executable, str(RENDERER), str(TEST_REPORT), *args],
            capture_output=True, text=True
        )
    finally:
        TEST_REPORT.unlink(missing_ok=True)


def valid_report(section_count=1, language="en"):
    return {
        "title": "Validation test",
        "language": language,
        "summary": ["A concise summary without mandatory evidence labels."],
        "sections": [{
            "title": f"Results {i + 1}",
            "blocks": [{
                "type": "paragraph",
                "text": "A factual statement [S1].",
                "sources": ["S1"]
            }]
        } for i in range(section_count)],
        "sources": [{
            "id": "S1",
            "title": "Test source",
            "publisher": "Test",
            "url": "https://example.org/source"
        }]
    }


def test_plain_summary_and_unclassified_paragraph_are_valid():
    result = run(valid_report(), "--check-only")
    assert result.returncode == 0, result.stderr


def test_basis_is_optional_but_fact_requires_source_when_used():
    report = valid_report()
    report["sections"][0]["blocks"][0]["basis"] = "fact"
    report["sections"][0]["blocks"][0]["sources"] = []
    result = run(report, "--check-only")
    assert result.returncode == 2
    assert "fact needs source" in result.stderr


def test_unknown_source_fails():
    report = valid_report()
    report["sections"][0]["blocks"][0]["sources"] = ["S99"]
    result = run(report, "--check-only")
    assert result.returncode == 2
    assert "unknown source" in result.stderr


def test_unused_source_fails():
    report = valid_report()
    report["sources"].append({
        "id": "S2", "title": "Unused source", "publisher": "Test"
    })
    result = run(report, "--check-only")
    assert result.returncode == 2
    assert "S2 is unused" in result.stderr


def test_malformed_top_level_structure_returns_validation_error():
    report = valid_report()
    report["sources"] = 4
    result = run(report, "--check-only")
    assert result.returncode == 2
    assert "sources: expected an array" in result.stderr


def test_long_report_has_contents_and_no_basis_tags():
    report = valid_report(section_count=7)
    report["contents"] = True
    TEST_REPORT.write_text(json.dumps(report), encoding="utf-8")
    try:
        result = subprocess.run(
            [sys.executable, str(RENDERER), str(TEST_REPORT), str(TEST_PDF)],
            capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        text = "\n".join(page.extract_text() or "" for page in PdfReader(str(TEST_PDF)).pages)
        assert "Contents" in text
        assert "Results 7" in text
        assert "[Inference]" not in text
        assert "https://example.org/source" not in text
    finally:
        TEST_REPORT.unlink(missing_ok=True)
        TEST_PDF.unlink(missing_ok=True)


def test_short_report_does_not_get_contents_by_default():
    report = valid_report(section_count=3)
    TEST_REPORT.write_text(json.dumps(report), encoding="utf-8")
    try:
        result = subprocess.run(
            [sys.executable, str(RENDERER), str(TEST_REPORT), str(TEST_PDF)],
            capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        text = "\n".join(page.extract_text() or "" for page in PdfReader(str(TEST_PDF)).pages)
        assert "Contents" not in text
    finally:
        TEST_REPORT.unlink(missing_ok=True)
        TEST_PDF.unlink(missing_ok=True)


def test_japanese_cid_font_creates_extractable_text():
    report = valid_report(language="ja")
    report["title"] = "研究レポート"
    report["summary"] = ["これは日本語のPDFテストです。"]
    report["sections"][0]["title"] = "調査結果"
    report["sections"][0]["blocks"][0]["text"] = "日本語の本文です。"
    TEST_REPORT.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    try:
        result = subprocess.run(
            [sys.executable, str(RENDERER), str(TEST_REPORT), str(TEST_PDF)],
            capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        text = "\n".join(page.extract_text() or "" for page in PdfReader(str(TEST_PDF)).pages)
        assert "研究レポート" in text
        assert "日本語の本文です。" in text
    finally:
        TEST_REPORT.unlink(missing_ok=True)
        TEST_PDF.unlink(missing_ok=True)


def test_summary_length_is_not_a_hard_limit():
    report = valid_report()
    report["summary"] = [" ".join(["summary"] * 140)]
    result = run(report, "--check-only")
    assert result.returncode == 0, result.stderr


def test_malformed_nested_source_reference_returns_error():
    report = valid_report()
    report["sections"][0]["blocks"][0]["sources"] = [["S1"]]
    result = run(report, "--check-only")
    assert result.returncode == 2
    assert "malformed report structure" in result.stderr


def test_devanagari_fallback_font_creates_extractable_text():
    report = valid_report(language="hi")
    report["title"] = "अनुसंधान रिपोर्ट"
    report["summary"] = ["यह देवनागरी लिपि के लिए पीडीएफ परीक्षण है।"]
    report["sections"][0]["title"] = "परिणाम"
    report["sections"][0]["blocks"][0]["text"] = "यह एक परीक्षण वाक्य है।"
    TEST_REPORT.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    try:
        result = subprocess.run(
            [sys.executable, str(RENDERER), str(TEST_REPORT), str(TEST_PDF)],
            capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        text = "\n".join(page.extract_text() or "" for page in PdfReader(str(TEST_PDF)).pages)
        assert "अनुसंधान रिपोर्ट" in text
        assert "यह एक परीक्षण वाक्य है।" in text
    finally:
        TEST_REPORT.unlink(missing_ok=True)
        TEST_PDF.unlink(missing_ok=True)


def test_rtl_report_fails_clearly_without_shaping_dependencies():
    import importlib.util
    if (importlib.util.find_spec("arabic_reshaper") is not None
            and importlib.util.find_spec("bidi") is not None):
        return
    report = valid_report(language="ar")
    report["title"] = "تقرير"
    TEST_REPORT.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    try:
        result = subprocess.run(
            [sys.executable, str(RENDERER), str(TEST_REPORT), str(TEST_PDF)],
            capture_output=True, text=True
        )
        assert result.returncode == 1
        assert "requires arabic-reshaper and python-bidi" in result.stderr
        assert not TEST_PDF.exists()
    finally:
        TEST_REPORT.unlink(missing_ok=True)
        TEST_PDF.unlink(missing_ok=True)


def test_invalid_json_returns_clear_error():
    TEST_REPORT.write_text("{not valid json", encoding="utf-8")
    try:
        result = subprocess.run(
            [sys.executable, str(RENDERER), str(TEST_REPORT), "--check-only"],
            capture_output=True, text=True
        )
        assert result.returncode == 2
        assert "Cannot read report" in result.stderr
    finally:
        TEST_REPORT.unlink(missing_ok=True)


def test_summary_has_no_fixed_word_limit():
    report = valid_report()
    report["summary"] = [" ".join(["summary"] * 140)]
    result = run(report, "--check-only")
    assert result.returncode == 0, result.stderr


def test_unsupported_glyph_fails_instead_of_silent_replacement():
    report = valid_report()
    report["title"] = "Unsupported glyph test \U0010ffff"
    TEST_REPORT.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    try:
        result = subprocess.run(
            [sys.executable, str(RENDERER), str(TEST_REPORT), str(TEST_PDF)],
            capture_output=True, text=True
        )
        assert result.returncode == 1
        assert "No installed PDF font supports U+10FFFF" in result.stderr
        assert not TEST_PDF.exists()
    finally:
        TEST_REPORT.unlink(missing_ok=True)
        TEST_PDF.unlink(missing_ok=True)
