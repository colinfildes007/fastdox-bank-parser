import os
from typing import Any, Dict, Optional
import tempfile
import shutil

from fastapi import FastAPI, Header, HTTPException, UploadFile, File, Form

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


def process_pdf_file(pdf_path: str, document_id: str, original_filename: Optional[str], bank_hint: Optional[str]) -> Dict[str, Any]:
    try:
        extracted = extract_pdf_text(pdf_path)
        context = {
            "document_id": document_id,
            "bank_hint": bank_hint,
            "original_filename": original_filename,
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
            "document_id": document_id,
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
            "document_id": document_id,
            "error": "Parser failed.",
        }


@app.post("/extract-upload")
def extract_upload(
    document_id: str = Form(...),
    file: UploadFile = File(...),
    original_filename: Optional[str] = Form(None),
    bank_hint: Optional[str] = Form(None),
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    require_auth(authorization)

    if not file:
        return {
            "status": "failed",
            "parser_version": PARSER_VERSION,
            "document_id": document_id,
            "error": "Missing PDF file.",
        }

    filename = original_filename or getattr(file, "filename", None) or "uploaded_statement.pdf"
    content_type = getattr(file, "content_type", "")
    has_pdf_filename = filename.lower().endswith(".pdf")
    has_pdf_content_type = content_type.lower() == "application/pdf"

    file.file.seek(0, os.SEEK_END)
    file_size = file.file.tell()
    if file_size == 0:
        return {
            "status": "failed",
            "parser_version": PARSER_VERSION,
            "document_id": document_id,
            "error": "Uploaded file is empty.",
        }

    file.file.seek(0)
    header = file.file.read(5)
    file.file.seek(0)

    if not has_pdf_filename and not has_pdf_content_type:
        if not header.startswith(b"%PDF"):
            return {
                "status": "failed",
                "parser_version": PARSER_VERSION,
                "document_id": document_id,
                "error": "Uploaded file is not a PDF.",
            }
    elif not header.startswith(b"%PDF"):
        return {
            "status": "failed",
            "parser_version": PARSER_VERSION,
            "document_id": document_id,
            "error": "Uploaded file does not appear to be a valid PDF.",
        }

    tmp_file = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_file = tmp.name
            shutil.copyfileobj(file.file, tmp)

        result = process_pdf_file(
            pdf_path=tmp_file,
            document_id=document_id,
            original_filename=filename,
            bank_hint=bank_hint,
        )

        return result
    finally:
        try:
            if tmp_file and os.path.exists(tmp_file):
                os.remove(tmp_file)
        except Exception:
            pass


@app.post("/extract")
def extract_statement(
    payload: ExtractRequest,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    require_auth(authorization)
    pdf_path = None
    try:
        pdf_path = download_pdf(payload.file_url)
        return process_pdf_file(
            pdf_path=pdf_path,
            document_id=payload.document_id,
            original_filename=payload.original_filename,
            bank_hint=payload.bank_hint,
        )
    except Exception:
        return {
            "status": "failed",
            "parser_version": PARSER_VERSION,
            "document_id": payload.document_id,
            "error": "Could not download PDF.",
        }
    finally:
        try:
            if pdf_path and os.path.exists(pdf_path):
                os.remove(pdf_path)
        except Exception:
            pass
