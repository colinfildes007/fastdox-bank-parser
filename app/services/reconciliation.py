import re
from typing import Dict, Optional


def money_to_float(value: str) -> float:
    return round(float(value.replace("£", "").replace(",", "").strip()), 2)


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

    derived_opening_balance = None
    if total_debits is not None and total_credits is not None and closing_balance is not None:
        derived_opening_balance = round(closing_balance - total_credits + total_debits, 2)

    return {
        "total_debits": total_debits,
        "total_credits": total_credits,
        "closing_balance": closing_balance,
        "derived_opening_balance": derived_opening_balance,
    }


def build_reconciliation(totals: Dict[str, Optional[float]]) -> Dict[str, Optional[float]]:
    calculated_ok = (
        totals["total_debits"] is not None
        and totals["total_credits"] is not None
        and totals["closing_balance"] is not None
    )

    return {
        "status": "totals_detected" if calculated_ok else "missing_totals",
        "statement_total_debits": totals["total_debits"],
        "statement_total_credits": totals["total_credits"],
        "closing_balance": totals["closing_balance"],
        "derived_opening_balance": totals["derived_opening_balance"],
        "difference": None,
    }
