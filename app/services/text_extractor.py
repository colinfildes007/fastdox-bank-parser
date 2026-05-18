import re
from typing import Dict

import pdfplumber


def extract_pdf_text(pdf_path: str) -> Dict:
    page_details = []
    extracted_texts = []

    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)

        for index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            extracted_texts.append(text)

            page_details.append(
                {
                    "page_number": index,
                    "text": text,
                    "text_length": len(text),
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
