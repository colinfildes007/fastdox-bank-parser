import os
from typing import Any, Dict, Optional
import tempfile
import shutil

from fastapi import FastAPI, Header, HTTPException, UploadFile, File, Form

from app.models import ExtractRequest
from app.services.bank_detector import available_parsers, detect_bank, select_parser
from app.services.pdf_loader import download_pdf
from app.services.reconciliation import reconcile
from app.services.text_extractor import extract_pdf_text


PARSER_VERSION = os.getenv("PARSER_VERSION", "fastdox_parser_v1.1.0")
PARSER_API_KEY = os.getenv("PARSER_API_KEY", "")
SERVICE_NAME = "fastdox-bank-parser"
# Render injects RENDER_GIT_COMMIT / RENDER_GIT_BRANCH at runtime; fall back to
# a plain GIT_COMMIT env var or "unknown" so /health always answers.
GIT_COMMIT = os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_COMMIT") or "unknown"
GIT_BRANCH = os.getenv("RENDER_GIT_BRANCH") or os.getenv("GIT_BRANCH") or "unknown"


app = FastAPI(title="FastDox Bank Parser", version=PARSER_VERSION)


def _adapter_info() -> Dict[str, Any]:
    """Adapters and their versions, derived from the registered parsers."""
    adapters = []
    versions: Dict[str, str] = {}
    for parser in available_parsers():
        adapter = getattr(parser, "parser_adapter", None)
        if adapter and adapter not in adapters:
            adapters.append(adapter)
            versions[adapter] = getattr(parser, "adapter_version", "unknown")
    return {"available_adapters": adapters, "adapter_versions": versions}


def _version_payload() -> Dict[str, Any]:
    info = _adapter_info()
    return {
        "service_name": SERVICE_NAME,
        "parser_version": PARSER_VERSION,
        "available_adapters": info["available_adapters"],
        "adapter_versions": info["adapter_versions"],
        "git_commit": GIT_COMMIT,
        "git_branch": GIT_BRANCH,
    }


def require_auth(authorization: Optional[str]) -> None:
    if not PARSER_API_KEY:
        return

    expected = f"Bearer {PARSER_API_KEY}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorised")


@app.get("/health")
def health() -> Dict[str, Any]:
    payload = _version_payload()
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        **payload,
    }


@app.get("/version")
def version() -> Dict[str, Any]:
    return _version_payload()


@app.get("/")
def root() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "endpoints": ["/health", "/version", "/extract", "/extract-upload"],
        **_version_payload(),
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

        detection = detect_bank(extracted["pages"], context["bank_hint"])
        context["detected_bank"] = detection["detected_bank"]
        context["resolved_bank"] = detection["resolved_bank"]
        parser_metadata = {
            "detected_bank": detection["detected_bank"],
            "bank_detection_confidence": detection["bank_detection_confidence"],
            "bank_hint": detection["bank_hint"],
            "parser_adapter": detection["parser_adapter"],
            "detection_evidence": detection["detection_evidence"],
            "parser_version": PARSER_VERSION,
            "page_count": extracted["page_count"],
        }

        if detection["status"] != "success":
            return {
                "status": detection["status"],
                "detected_bank": detection["detected_bank"],
                "bank_detection_confidence": detection["bank_detection_confidence"],
                "bank_hint": detection["bank_hint"],
                "parser_adapter": detection["parser_adapter"],
                "parser_version": PARSER_VERSION,
                "detection_evidence": detection["detection_evidence"],
                "page_count": extracted["page_count"],
                "transaction_count": 0,
                "reconciliation": {
                    "status": detection["status"],
                    "calculated_total_debits": None,
                    "calculated_total_credits": None,
                    "statement_total_debits": None,
                    "statement_total_credits": None,
                    "closing_balance": None,
                    "derived_opening_balance": None,
                    "difference": None,
                },
                "transactions": [],
                "parser_metadata": parser_metadata,
                "error": detection["error"],
            }

        parser = select_parser(context)
        parser_result = parser.parse(context)
        reconciliation_result = reconcile(parser_result.get("statement", {}), parser_result.get("transactions", []))
        parser_adapter = getattr(parser, "parser_adapter", parser.parser_name)
        parser_metadata["parser_adapter"] = parser_adapter

        status = "success"
        if reconciliation_result["status"] != "matched":
            status = reconciliation_result["status"]

        return {
            "status": status,
            "detected_bank": detection["detected_bank"],
            "bank_detection_confidence": detection["bank_detection_confidence"],
            "bank_hint": detection["bank_hint"],
            "parser_adapter": parser_adapter,
            "parser_version": PARSER_VERSION,
            "detection_evidence": detection["detection_evidence"],
            "page_count": extracted["page_count"],
            "transaction_count": len(parser_result.get("transactions", [])),
            "reconciliation": reconciliation_result,
            "transactions": parser_result.get("transactions", []),
            "statement": parser_result.get("statement", {}),
            "accounts": parser_result.get("accounts", []),
            "issues": parser_result.get("issues", []),
            "parser_debug": parser_result.get("parser_debug", {}),
            "parser_metadata": parser_metadata,
            "bank_detection_conflict": detection.get("bank_detection_conflict", False),
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
