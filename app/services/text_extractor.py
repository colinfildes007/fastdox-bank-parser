from typing import Dict, List

import pdfplumber

try:  # PyMuPDF — a second, span-order text engine
    import fitz
except Exception:  # pragma: no cover - fitz is a hard dependency, but stay safe
    fitz = None


def _extract_flow_text(pdf_path: str) -> List[str]:
    """Per-page text in PyMuPDF's reading order.

    PyMuPDF emits each text span as a unit and orders spans by block/line, so
    overlapping multi-column runs stay intact. pdfplumber's ``extract_text()``
    sorts individual characters by coordinate, which on some statement PDFs
    interleaves overlapping runs ("CDolumn ate" instead of "Column"/"Date").
    """
    if fitz is None:
        return []
    pages: List[str] = []
    try:
        with fitz.open(pdf_path) as doc:
            for page in doc:
                pages.append(page.get_text("text") or "")
    except Exception:
        return []
    return pages


def _safe_extract_tables(page) -> List[List[List[str]]]:
    """Geometry-based table extraction.

    ``extract_text()`` reads a multi-column / ruled table in document order,
    which on some bank-statement PDFs interleaves characters across columns
    ("CDolumn ate" instead of "Column"/"Date"). ``extract_tables()`` instead
    segments the page by its ruling lines and reads each cell's bounding box,
    so each cell's text stays intact. Failures are swallowed — the text layer
    remains available as a fallback.
    """
    tables: List[List[List[str]]] = []
    try:
        raw_tables = page.extract_tables() or []
    except Exception:
        return tables

    for raw_table in raw_tables:
        rows: List[List[str]] = []
        for raw_row in raw_table or []:
            if raw_row is None:
                continue
            rows.append([(cell or "").replace("\n", " ").strip() for cell in raw_row])
        if rows:
            tables.append(rows)
    return tables


def _reconstruct_text_by_position(page) -> str:
    """Rebuild the page text from word bounding boxes: words grouped into rows
    by their vertical position, then ordered left-to-right by x within a row.

    This recovers a sane reading order when ``extract_text()`` interleaves
    overlapping/multi-column text runs.
    """
    try:
        words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    except Exception:
        return ""
    if not words:
        return ""

    rows: Dict[int, list] = {}
    for word in words:
        bucket = int(round(float(word.get("top", 0.0)) / 3.0))
        rows.setdefault(bucket, []).append(word)

    lines = []
    for bucket in sorted(rows):
        ordered = sorted(rows[bucket], key=lambda w: float(w.get("x0", 0.0)))
        line = " ".join(str(w.get("text", "")) for w in ordered).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def extract_pdf_text(pdf_path: str) -> Dict:
    page_details = []
    extracted_texts = []

    flow_pages = _extract_flow_text(pdf_path)

    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)

        for index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            extracted_texts.append(text)
            flow_text = flow_pages[index - 1] if index - 1 < len(flow_pages) else ""

            page_details.append(
                {
                    "page_number": index,
                    "text": text,
                    "text_length": len(text),
                    # Alternative extractions, used by parsers when the default
                    # text layer is garbled. The plain `text` field is left
                    # untouched so header/summary parsing is unaffected.
                    "flow_text": flow_text,
                    "tables": _safe_extract_tables(page),
                    "position_text": _reconstruct_text_by_position(page),
                }
            )

    all_text = "\n".join(extracted_texts)
    indicators = ["Date", "Description", "Debits", "Credits", "Balance", "£"]
    text_layer_detected = any(len(page["text"].strip()) > 0 for page in page_details) and any(
        indicator.lower() in all_text.lower() for indicator in indicators
    )

    return {
        "page_count": page_count,
        "pages": page_details,
        "all_text": all_text,
        "text_layer_detected": text_layer_detected,
    }
