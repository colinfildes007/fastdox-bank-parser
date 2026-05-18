from pathlib import Path
import re
from typing import Any, Dict, List, Tuple

import pdfplumber


def extract_text_from_pdf(pdf_path: Path) -> Tuple[List[str], List[Dict[str, Any]]]:
    pages_debug: List[Dict[str, Any]] = []
    page_texts: List[str] = []

    with pdfplumber.open(pdf_path) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            page_texts.append(text)

            dated_rows = len(
                re.findall(
                    r"\b\d{1,2}(?:st|nd|rd|th)\s+[A-Za-z]{3,9}\s+\d{2}\b",
                    text,
                )
            )

            pages_debug.append(
                {
                    "page_number": index,
                    "text_length": len(text),
                    "dated_rows_detected": dated_rows,
                }
            )

    return page_texts, pages_debug
