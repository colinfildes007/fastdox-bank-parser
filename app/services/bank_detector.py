from typing import Any, Dict, Optional, Sequence

from app.parsers.base import BaseStatementParser
from app.parsers.generic_table import GenericTableParser
from app.parsers.santander import SantanderStatementParser

VALID_BANK_HINTS = {
    "auto",
    "santander",
    "lloyds",
    "halifax",
    "barclays",
    "hsbc",
    "natwest",
    "rbs",
    "nationwide",
    "monzo",
    "starling",
    "revolut",
}

BANK_ADAPTERS = {
    "santander": "santander_v1",
}

BANK_DISPLAY_NAMES = {
    "santander": "Santander",
    "unknown": "unknown",
}


def detect_bank(pages: Sequence[Dict[str, Any]], bank_hint: Optional[str] = None) -> Dict[str, Any]:
    bank_hint_value = (bank_hint or "auto").strip().lower()
    if bank_hint_value not in VALID_BANK_HINTS:
        bank_hint_value = "auto"

    first_pages_text = "\n".join(page.get("text", "") for page in pages[:2])
    search_text = first_pages_text.lower()

    evidence = []
    has_santander_uk = "santander uk plc" in search_text
    has_santander = "santander" in search_text
    has_account_summary = "account summary" in search_text
    has_table_headers = all(
        header in search_text
        for header in ["date", "description", "debits", "credits", "balance"]
    )

    if has_santander_uk:
        evidence.append("Santander UK plc found on page 1")
    elif has_santander:
        evidence.append("Santander text found on page 1")

    if has_account_summary:
        evidence.append("Account Summary found")

    if has_table_headers:
        evidence.append("Santander statement table headers matched")

    confidence = 0.0
    if has_santander_uk:
        confidence += 0.45
    elif has_santander:
        confidence += 0.3

    if has_account_summary:
        confidence += 0.2
    if has_table_headers:
        confidence += 0.35

    if has_santander and has_table_headers:
        confidence = max(confidence, 0.98)

    confidence = min(confidence, 1.0)

    detected_bank = "Santander" if has_santander else "unknown"
    parser_adapter = BANK_ADAPTERS.get(detected_bank.lower())

    resolved_bank = None
    bank_detection_conflict = False
    status = "success"
    error = None

    if bank_hint_value == "auto":
        if detected_bank == "Santander" and confidence >= 0.95:
            resolved_bank = detected_bank
        else:
            status = "unsupported_or_uncertain_bank"
            resolved_bank = detected_bank if detected_bank != "unknown" else None
            error = "Unable to detect a supported bank with sufficient confidence."
    elif bank_hint_value == "santander":
        resolved_bank = "Santander"
    else:
        if detected_bank == "Santander" and confidence >= 0.95:
            status = "success"
            bank_detection_conflict = True
            resolved_bank = "Santander"
        else:
            status = "bank_detection_conflict"
            bank_detection_conflict = True
            resolved_bank = None
            error = (
                f"Bank hint '{bank_hint_value}' conflicts with detected bank 'Santander'."
            )

    return {
        "detected_bank": detected_bank,
        "bank_hint": bank_hint_value,
        "bank_detection_confidence": round(confidence, 2),
        "parser_adapter": parser_adapter,
        "detection_evidence": evidence,
        "resolved_bank": resolved_bank,
        "status": status,
        "bank_detection_conflict": bank_detection_conflict,
        "error": error,
    }


def available_parsers() -> Sequence[BaseStatementParser]:
    return [SantanderStatementParser(), GenericTableParser()]


def select_parser(context: Dict) -> BaseStatementParser:
    for parser in available_parsers():
        if parser.can_parse(context):
            return parser
    return GenericTableParser()
