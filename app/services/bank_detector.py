import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from app.parsers.barclays import BarclaysStatementParser
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
    "barclays": "Barclays",
}

BANK_ADAPTERS = {
    "Santander": "santander_v1",
    "Barclays": "barclays_family_v1",
    "Lloyds Bank": "lloyds_family_v1",
    "Halifax": "lloyds_family_v1",
    "Bank of Scotland": "lloyds_family_v1",
}

# Weighted text signals for bank detection. Strong header/title phrases score
# high; bare bank-name mentions score low so that an isolated occurrence in a
# merchant description or footer cannot overpower a clear statement header.
BANK_SCORING_RULES: Dict[str, List[Tuple[str, "re.Pattern[str]", int]]] = {
    "Barclays": [
        ("Your Barclays Bank Account statement", re.compile(r"your\s+barclays\s+bank\s+account\s+statement", re.IGNORECASE), 100),
        ("Barclays Bank UK PLC", re.compile(r"barclays\s+bank\s+uk\s+plc", re.IGNORECASE), 80),
        ("Barclays Bank", re.compile(r"barclays\s+bank", re.IGNORECASE), 80),
        ("Current account statement", re.compile(r"current\s+account\s+statement", re.IGNORECASE), 30),
        ("Barclays", re.compile(r"\bbarclays\b", re.IGNORECASE), 15),
    ],
    "Santander": [
        ("Santander UK plc", re.compile(r"santander\s+uk\s+plc", re.IGNORECASE), 100),
        ("Santander Bank", re.compile(r"santander\s+bank", re.IGNORECASE), 80),
        ("Account Summary", re.compile(r"account\s+summary", re.IGNORECASE), 30),
        # bare "santander" mention only — too easy to hit on noisy text
        ("Santander", re.compile(r"\bsantander\b", re.IGNORECASE), 10),
    ],
    "Lloyds Bank": [
        ("Lloyds Bank plc", re.compile(r"lloyds\s+bank\s+plc", re.IGNORECASE), 100),
        ("Lloyds Bank", re.compile(r"lloyds\s+bank", re.IGNORECASE), 80),
        ("Lloyds", re.compile(r"\blloyds\b", re.IGNORECASE), 30),
        ("Classic statement", re.compile(r"classic\s+statement", re.IGNORECASE), 30),
        ("Club Lloyds", re.compile(r"club\s+lloyds", re.IGNORECASE), 30),
    ],
    "Halifax": [
        ("Halifax Bank plc", re.compile(r"halifax\s+bank\s+plc", re.IGNORECASE), 100),
        ("Halifax plc", re.compile(r"halifax\s+plc", re.IGNORECASE), 100),
        ("Halifax Bank", re.compile(r"halifax\s+bank", re.IGNORECASE), 80),
        ("Halifax", re.compile(r"\bhalifax\b", re.IGNORECASE), 30),
    ],
    "Bank of Scotland": [
        ("Bank of Scotland plc", re.compile(r"bank\s+of\s+scotland\s+plc", re.IGNORECASE), 100),
        ("Bank of Scotland", re.compile(r"bank\s+of\s+scotland", re.IGNORECASE), 80),
        ("BOS", re.compile(r"\bbos\b", re.IGNORECASE), 10),
    ],
}


def _has_barclays_at_a_glance(text: str) -> bool:
    lower = text.lower()
    return all(
        marker in lower
        for marker in ("at a glance", "start balance", "money in", "money out", "end balance")
    )


def _has_sort_code_and_account(text: str) -> bool:
    return bool(
        re.search(r"sort\s*code", text, re.IGNORECASE)
        and re.search(r"account\s*(?:no\b|number)", text, re.IGNORECASE)
    )


def _has_lloyds_money_layout(text: str) -> bool:
    lower = text.lower()
    return all(
        marker in lower for marker in ("your transactions", "money in", "money out")
    )


def _has_statement_context(text: str) -> bool:
    """Statement-shaped layout markers — used to lift confidence for a
    weak bare-name match (e.g. "Halifax" alone) once a statement-header
    context is present."""
    lower = text.lower()
    return any(
        marker in lower
        for marker in (
            "account statement",
            "statement period",
            "sort code",
            "account number",
        )
    )


def _bank_present_with_context(pattern: "re.Pattern[str]") -> Callable[[str], bool]:
    def predicate(text: str) -> bool:
        return bool(pattern.search(text)) and _has_statement_context(text)
    return predicate


# Layout-style signals — fire when a bank's table/page structure is present.
BANK_LAYOUT_SIGNALS: Dict[str, List[Tuple[str, Callable[[str], bool], int]]] = {
    "Barclays": [
        ("At a glance block (Start / Money in / Money out / End)", _has_barclays_at_a_glance, 60),
        ("Sort code + account number layout", _has_sort_code_and_account, 20),
    ],
    "Lloyds Bank": [
        ("Your Transactions + Money in/out columns", _has_lloyds_money_layout, 30),
        ("Lloyds + statement context", _bank_present_with_context(re.compile(r"\blloyds\b", re.IGNORECASE)), 60),
    ],
    "Halifax": [
        ("Halifax + statement context", _bank_present_with_context(re.compile(r"\bhalifax\b", re.IGNORECASE)), 60),
    ],
    "Bank of Scotland": [
        ("Bank of Scotland + statement context", _bank_present_with_context(re.compile(r"bank\s+of\s+scotland", re.IGNORECASE)), 60),
    ],
}

# A bank's score must clear this floor *and* beat the next bank by this margin
# to win. Otherwise detection is "unknown".
MIN_DETECTION_SCORE = 30
MIN_DETECTION_MARGIN = 20


def _normalize_bank_hint(bank_hint: Optional[str]) -> str:
    if not bank_hint:
        return "auto"
    normalized = bank_hint.strip().lower()
    return normalized if normalized in VALID_BANK_HINTS else "auto"


def _score_bank(bank: str, text: str) -> Tuple[int, List[str]]:
    score = 0
    matched: List[str] = []
    for label, pattern, weight in BANK_SCORING_RULES.get(bank, []):
        if pattern.search(text):
            score += weight
            matched.append(label)
    for label, predicate, weight in BANK_LAYOUT_SIGNALS.get(bank, []):
        if predicate(text):
            score += weight
            matched.append(label)
    return score, matched


def _bank_match_pages(bank: str, pages: Sequence[Dict[str, Any]]) -> List[int]:
    primary_patterns = [pattern for _, pattern, _ in BANK_SCORING_RULES.get(bank, [])]
    page_numbers: List[int] = []
    for page in pages:
        text = page.get("text", "") or ""
        if any(pattern.search(text) for pattern in primary_patterns):
            number = page.get("page_number")
            if number is not None:
                page_numbers.append(number)
    return page_numbers


def detect_bank(
    pages: Sequence[Dict[str, Any]],
    bank_hint: Optional[str] = None,
) -> Dict[str, Any]:
    bank_hint_value = _normalize_bank_hint(bank_hint)
    bank_hint_canonical = HINT_TO_CANONICAL.get(bank_hint_value, "auto")

    # Detection scans the first two pages (cover + header / "At a glance")
    # because that is where the statement-defining text lives. Footer / legal
    # / merchant noise on later pages cannot drive selection.
    first_pages_text = "\n".join(page.get("text", "") for page in pages[:2])

    candidates: Dict[str, Dict[str, Any]] = {}
    for bank in BANK_SCORING_RULES:
        score, matched = _score_bank(bank, first_pages_text)
        candidates[bank] = {
            "score": score,
            "matched_terms": matched,
            "pages": _bank_match_pages(bank, pages),
        }

    sorted_banks = sorted(candidates.items(), key=lambda kv: kv[1]["score"], reverse=True)
    top_bank, top_data = sorted_banks[0] if sorted_banks else (None, {"score": 0})
    runner_up_score = sorted_banks[1][1]["score"] if len(sorted_banks) > 1 else 0
    margin = top_data["score"] - runner_up_score

    if top_data["score"] >= MIN_DETECTION_SCORE and margin >= MIN_DETECTION_MARGIN and top_bank:
        detected_bank = top_bank
        confidence = min(top_data["score"] / 100.0, 1.0)
        evidence = list(top_data["matched_terms"])
    else:
        detected_bank = "unknown"
        confidence = 0.0
        evidence = []

    parser_adapter = BANK_ADAPTERS.get(detected_bank)

    resolved_bank: Optional[str] = None
    bank_detection_conflict = False
    status = "success"
    error: Optional[str] = None

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
        "bank_detection_candidates": candidates,
        "selected_bank": detected_bank if detected_bank != "unknown" else None,
        "selected_adapter": parser_adapter,
        "detection_margin": margin,
    }


def available_parsers() -> Sequence[BaseStatementParser]:
    # Barclays is selected before Lloyds because Lloyds' can_parse matches the
    # generic "Money in" / "Money out" markers that also appear in Barclays.
    return [
        SantanderStatementParser(),
        BarclaysStatementParser(),
        LloydsStatementParser(),
        GenericTableParser(),
    ]


def select_parser(context: Dict) -> BaseStatementParser:
    """Pick a parser. When bank detection has already resolved a bank, that
    decision is authoritative — its parser is returned directly so that a
    noisy other-bank substring in a merchant description cannot pull a less
    strict ``can_parse`` (e.g. Santander matching on a "Santander ATM" payee)
    away from the correct adapter."""
    detected_bank = context.get("resolved_bank") or context.get("detected_bank")
    expected_adapter = BANK_ADAPTERS.get(detected_bank) if detected_bank else None
    if expected_adapter:
        for parser in available_parsers():
            if getattr(parser, "parser_adapter", None) == expected_adapter:
                return parser
    for parser in available_parsers():
        if parser.can_parse(context):
            return parser
    return GenericTableParser()
