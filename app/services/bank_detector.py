from typing import Any, Dict, Optional, Sequence

from app.parsers.base import BaseStatementParser
from app.parsers.generic_table import GenericTableParser
from app.parsers.lloyds import LloydsStatementParser
from app.parsers.santander import SantanderStatementParser

VALID_BANK_HINTS = {
    "auto",
    "santander",
    "lloyds",
    "lloyds bank",
    "classic",
    "club lloyds",
    "money in",
    "money out",
    "balance on",
    "halifax",
    "bank of scotland",
    "bos",
    "barclays",
    "hsbc",
    "natwest",
    "rbs",
    "nationwide",
    "monzo",
    "starling",
    "revolut",
}

HINT_TO_CANONICAL = {
    "auto": "auto",
    "santander": "Santander",
    "lloyds": "Lloyds Bank",
    "lloyds bank": "Lloyds Bank",
    "classic": "Lloyds Bank",
    "club lloyds": "Lloyds Bank",
    "money in": "Lloyds Bank",
    "money out": "Lloyds Bank",
    "balance on": "Lloyds Bank",
    "halifax": "Halifax",
    "bank of scotland": "Bank of Scotland",
    "bos": "Bank of Scotland",
}

BANK_ADAPTERS = {
    "Santander": "santander_v1",
    "Lloyds Bank": "lloyds_family_v1",
    "Halifax": "lloyds_family_v1",
    "Bank of Scotland": "lloyds_family_v1",
}

BANK_SIGNATURES = {
    "Santander": ["santander uk plc", "santander"],
    "Lloyds Bank": ["lloyds bank", "lloyds", "classic", "club lloyds", "money in", "money out", "balance on"],
    "Halifax": ["halifax"],
    "Bank of Scotland": ["bank of scotland"],
}

BANK_HEADER_TAGS = [
    "date",
    "transaction details",
    "description",
    "debit",
    "credit",
    "balance",
    "sort code",
    "account number",
]

ACCOUNT_LABELS = ["sort code", "account number", "account holder", "account name"]


def _normalize_bank_hint(bank_hint: Optional[str]) -> str:
    if not bank_hint:
        return "auto"
    normalized = bank_hint.strip().lower()
    return normalized if normalized in VALID_BANK_HINTS else "auto"


def detect_bank(pages: Sequence[Dict[str, Any]], bank_hint: Optional[str] = None) -> Dict[str, Any]:
    bank_hint_value = _normalize_bank_hint(bank_hint)
    bank_hint_canonical = HINT_TO_CANONICAL.get(bank_hint_value, "auto")

    first_pages_text = "\n".join(page.get("text", "") for page in pages[:2])
    search_text = first_pages_text.lower()

    evidence: list[str] = []
    confidence_by_bank: Dict[str, float] = {}

    has_account_labels = any(label in search_text for label in ACCOUNT_LABELS)
    has_table_headers = any(tag in search_text for tag in BANK_HEADER_TAGS)
    has_statement_period = "statement period" in search_text or "statement date" in search_text

    for bank_name, signatures in BANK_SIGNATURES.items():
        bank_name_found = any(signature in search_text for signature in signatures)
        if bank_name == "Bank of Scotland":
            bos_weak = "bos" in search_text
            bank_name_found = bank_name_found or (
                bos_weak and (has_table_headers or has_account_labels or has_statement_period)
            )

        if not bank_name_found:
            continue

        confidence = 0.5
        if has_table_headers:
            confidence += 0.35
        if has_account_labels:
            confidence += 0.15
        if has_statement_period:
            confidence += 0.1

        confidence = min(confidence, 1.0)
        if bank_name_found:
            evidence.append(f"{bank_name} text found on page 1")
        if has_table_headers:
            evidence.append(f"Statement headers matched for {bank_name}")
        if has_account_labels:
            evidence.append(f"Account labels detected for {bank_name}")
        if has_statement_period:
            evidence.append("Statement period label detected")

        if bank_name in {"Lloyds Bank", "Halifax", "Bank of Scotland"} and bank_name_found and (has_account_labels or has_statement_period):
            confidence = max(confidence, 0.9)

        if bank_name == "Santander" and has_table_headers:
            confidence = max(confidence, 0.95)
        if bank_name in {"Lloyds Bank", "Halifax", "Bank of Scotland"} and bank_name_found and has_table_headers:
            confidence = max(confidence, 0.9)

        confidence_by_bank[bank_name] = confidence

    detected_bank = "unknown"
    confidence = 0.0
    if confidence_by_bank:
        detected_bank = max(confidence_by_bank, key=confidence_by_bank.get)
        confidence = confidence_by_bank[detected_bank]

    parser_adapter = BANK_ADAPTERS.get(detected_bank)

    resolved_bank = None
    bank_detection_conflict = False
    status = "success"
    error = None

    if bank_hint_value == "auto":
        if detected_bank != "unknown" and confidence >= 0.90:
            resolved_bank = detected_bank
        else:
            status = "unsupported_or_uncertain_bank"
            resolved_bank = detected_bank if detected_bank != "unknown" else None
            error = "Unable to detect a supported bank with sufficient confidence."
    else:
        if detected_bank == "unknown" or detected_bank == bank_hint_canonical:
            resolved_bank = bank_hint_canonical
        else:
            if confidence >= 0.95:
                status = "success"
                bank_detection_conflict = True
                resolved_bank = detected_bank
            else:
                status = "bank_detection_conflict"
                bank_detection_conflict = True
                resolved_bank = bank_hint_canonical
                error = (
                    f"Bank hint '{bank_hint_value}' conflicts with detected bank '{detected_bank}'."
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
    return [SantanderStatementParser(), LloydsStatementParser(), GenericTableParser()]


def select_parser(context: Dict) -> BaseStatementParser:
    for parser in available_parsers():
        if parser.can_parse(context):
            return parser
    return GenericTableParser()
