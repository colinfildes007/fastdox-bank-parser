import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException

from app.models import ExtractRequest
from app.services.bank_detector import select_parser
from app.services.pdf_loader import download_pdf
from app.services.reconciliation import build_reconciliation
from app.services.text_extractor import extract_text_from_pdf


PARSER_VERSION = os.getenv("PARSER_VERSION", "santander_v1.0.0")
PARSER_API_KEY = os.getenv("PARSER_API_KEY", "")


app = FastAPI(title="FastDox Bank Parser", version=PARSER_VERSION)


def require_auth(authorization: Optional[str]) -> None:
    if not PARSER_API_KEY:
        return

    expected = f"Bearer {PARSER_API_KEY}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorised")


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "fastdox-bank-parser",
        "parser_version": PARSER_VERSION,
    }


@app.post("/extract")
def extract_statement(
    payload: ExtractRequest,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    require_auth(authorization)

    try:
        pdf_path = download_pdf(payload.file_url)
    except Exception as exc:
        return {
            "status": "failed",
            "parser_version": PARSER_VERSION,
            "document_id": payload.document_id,
            "error": f"Could not download PDF: {str(exc)}",
        }

    try:
        page_texts, pages_debug = extract_text_from_pdf(pdf_path)
        all_text = "\n".join(page_texts)
        parser = select_parser(all_text, payload.bank_hint)
        parser_result = parser.parse(all_text)

        totals = parser_result["totals"]
        reconciliation = build_reconciliation(totals)
        bank_name = parser_result.get("bank_name") or payload.bank_hint

        return {
            "status": "success",
            "parser_version": PARSER_VERSION,
            "document_id": payload.document_id,
            "bank_name": bank_name,
            "page_count": len(page_texts),
            "statement": {
                "currency": "GBP",
                **totals,
            },
            "accounts": [],
            "transactions": [],
            "reconciliation": reconciliation,
            "issues": [],
            "parser_debug": {
                "text_layer_detected": any(len(t) > 100 for t in page_texts),
                "ocr_used": False,
                "parser_used": parser_result.get("parser_used", "unknown"),
                "pages": pages_debug,
            },
        }

    except Exception as exc:
        return {
            "status": "failed",
            "parser_version": PARSER_VERSION,
            "document_id": payload.document_id,
            "error": f"Parser failed: {str(exc)}",
        }
