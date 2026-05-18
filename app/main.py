import os
import re
import tempfile
from typing import Any, Dict, List, Optional

import pdfplumber
import requests
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel


PARSER_VERSION = os.getenv("PARSER_VERSION", "santander_v1.0.0")
PARSER_API_KEY = os.getenv("PARSER_API_KEY", "")


app = FastAPI(title="FastDox Bank Parser", version=PARSER_VERSION)


class ExtractRequest(BaseModel):
    document_id: str
    file_url: str
    original_filename: Optional[str] = None
    bank_hint: Optional[str] = None


def require_auth(authorization: Optional[str]) -> None:
    if not PARSER_API_KEY:
        return

    expected = f"Bearer {PARSER_API_KEY}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorised")


def money_to_float(value: str) -> float:
    value = value.replace("£", "").replace(",", "").strip()
    return round(float(value), 2)


def extract_statement_totals(all_text: str) -> Dict[str, Optional[float]]:
    total_debits = None
    total_credits = None
    closing_balance = None

    debit_match = re.search(r"Total debits\s+£([\d,]+\.\d{2})", all_text, re.IGNORECASE)
    credit_match = re.search(r"Total credit\s+£([\d,]+\.\d{2})", all_text, re.IGNORECASE)
    closing_match = re.search(r"Closing Balance\s+£([\d,]+\.\d{2})", all_text, re.IGNORECASE)

    if debit_match:
        total_debits = money_to_float(debit_match.group(1))
    if credit_match:
        total_credits = money_to_float(credit_match.group(1))
    if closing_match:
        closing_balance = money_to_float(closing_match.group(1))

    derived_opening = None
    if total_debits is not None and total_credits is not None and closing_balance is not None:
        derived_opening = round(closing_balance - total_credits + total_debits, 2)

    return {
        "total_debits": total_debits,
        "total_credits": total_credits,
        "closing_balance": closing_balance,
        "derived_opening_balance": derived_opening,
    }


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
        response = requests.get(payload.file_url, timeout=30)
        response.raise_for_status()
    except Exception as exc:
        return {
            "status": "failed",
            "parser_version": PARSER_VERSION,
            "document_id": payload.document_id,
            "error": f"Could not download PDF: {str(exc)}",
        }

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temp_file:
        temp_file.write(response.content)
        temp_path = temp_file.name

    pages_debug: List[Dict[str, Any]] = []
    page_texts: List[str] = []

    try:
        with pdfplumber.open(temp_path) as pdf:
            page_count = len(pdf.pages)

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

        all_text = "\n".join(page_texts)
        totals = extract_statement_totals(all_text)

        calculated_ok = (
            totals["total_debits"] is not None
            and totals["total_credits"] is not None
            and totals["closing_balance"] is not None
        )

        reconciliation_status = "totals_detected" if calculated_ok else "missing_totals"

        return {
            "status": "success",
            "parser_version": PARSER_VERSION,
            "document_id": payload.document_id,
            "bank_name": "Santander" if "santander" in all_text.lower() else payload.bank_hint,
            "page_count": page_count,
            "statement": {
                "currency": "GBP",
                **totals,
            },
            "accounts": [],
            "transactions": [],
            "reconciliation": {
                "status": reconciliation_status,
                "statement_total_debits": totals["total_debits"],
                "statement_total_credits": totals["total_credits"],
                "closing_balance": totals["closing_balance"],
                "derived_opening_balance": totals["derived_opening_balance"],
                "difference": None,
            },
            "issues": [],
            "parser_debug": {
                "text_layer_detected": any(len(t) > 100 for t in page_texts),
                "ocr_used": False,
                "parser_used": "pdfplumber_text_probe_v1",
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
