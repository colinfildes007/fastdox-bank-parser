import os
from typing import Any, Dict, Optional

from fastapi import FastAPI, Header, HTTPException

from app.models import ExtractRequest
from app.parsers.generic_table import GenericTableParser
from app.parsers.santander import SantanderStatementParser
from app.services.bank_detector import detect_bank
from app.services.pdf_loader import download_pdf
from app.services.reconciliation import reconcile
from app.services.text_extractor import extract_pdf_text


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
    except Exception:
        return {
            "status": "failed",
            "parser_version": PARSER_VERSION,
            "document_id": payload.document_id,
            "error": "Could not download PDF.",
        }

    try:
        extracted = extract_pdf_text(pdf_path)
        context = {
            "document_id": payload.document_id,
            "bank_hint": payload.bank_hint,
            "original_filename": payload.original_filename,
            "page_count": extracted["page_count"],
            "pages": extracted["pages"],
            "all_text": extracted["all_text"],
            "text_layer_detected": extracted["text_layer_detected"],
        }

        bank = detect_bank(context["all_text"], context["bank_hint"])
        if bank == "santander":
            parser = SantanderStatementParser()
            top_status = "success"
        else:
            parser = GenericTableParser()
            top_status = "unsupported_bank"

        parser_result = parser.parse(context)
        reconciliation_result = reconcile(parser_result.get("statement", {}), parser_result.get("transactions", []))

        return {
            "status": top_status,
            "parser_version": PARSER_VERSION,
            "document_id": payload.document_id,
            "bank_name": parser_result.get("bank_name"),
            "page_count": extracted["page_count"],
            "statement": parser_result.get("statement", {}),
            "accounts": parser_result.get("accounts", []),
            "transactions": parser_result.get("transactions", []),
            "reconciliation": reconciliation_result,
            "issues": parser_result.get("issues", []),
            "parser_debug": parser_result.get("parser_debug", {}),
        }

    except Exception:
        return {
            "status": "failed",
            "parser_version": PARSER_VERSION,
            "document_id": payload.document_id,
            "error": "Parser failed.",
        }
