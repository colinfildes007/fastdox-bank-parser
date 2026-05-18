import re
from typing import Dict, List, Optional

from app.parsers.base import BaseStatementParser
from app.services.reconciliation import extract_statement_totals


DATE_PATTERN = re.compile(
    r"\b(\d{1,2}(?:st|nd|rd|th))\s+"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+"
    r"(\d{2})\b",
    re.IGNORECASE,
)
MONEY_PATTERN = re.compile(r"£[\d,]+\.\d{2}")
FOOTER_PATTERNS = [
    "total debits",
    "total credit",
    "closing balance",
    "date description debits credits balance",
    "statement balance",
    "account number",
    "sort code",
    "sort code masked",
    "account name",
]
CREDIT_CLUES = [
    "faster payments receipt",
    "bank giro credit",
    "mrs r drew reference",
    "from",
    "receipt",
]
DEBIT_CLUES = [
    "transfer to",
    "cash withdrawal",
    "direct debit payment",
    "standing order",
    "debit card payments",
    "monthly fee",
    "tesco",
    "stores",
]
MONTH_MAP = {
    "jan": "01",
    "feb": "02",
    "mar": "03",
    "apr": "04",
    "may": "05",
    "jun": "06",
    "jul": "07",
    "aug": "08",
    "sep": "09",
    "oct": "10",
    "nov": "11",
    "dec": "12",
}
TOLERANCE = 0.01


class SantanderStatementParser(BaseStatementParser):
    bank_name = "Santander"
    parser_name = "santander_text_v1"

    def can_parse(self, context: Dict) -> bool:
        bank_hint = context.get("bank_hint") or ""
        all_text = context.get("all_text", "")

        if bank_hint and "santander" in bank_hint.lower():
            return True

        return "santander" in all_text.lower()

    def parse(self, context: Dict) -> Dict:
        pages = context.get("pages", [])
        all_text = context.get("all_text", "")
        page_count = context.get("page_count", 0)
        text_layer_detected = context.get("text_layer_detected", False)

        transactions, issues = self._parse_transactions(pages)
        totals = extract_statement_totals(all_text)

        response = self.build_response(context)
        response.update(
            {
                "bank_name": self.bank_name,
                "statement": {
                    "currency": "GBP",
                    **totals,
                },
                "accounts": [],
                "transactions": transactions,
                "issues": issues,
                "reconciliation": {
                    "status": "totals_detected"
                    if totals["total_debits"] is not None
                    and totals["total_credits"] is not None
                    and totals["closing_balance"] is not None
                    else "missing_totals",
                    "calculated_total_debits": None,
                    "calculated_total_credits": None,
                    "statement_total_debits": totals["total_debits"],
                    "statement_total_credits": totals["total_credits"],
                    "closing_balance": totals["closing_balance"],
                    "derived_opening_balance": totals["derived_opening_balance"],
                    "difference": None,
                },
                "parser_debug": {
                    "parser_name": self.parser_name,
                    "page_count": page_count,
                    "text_layer_detected": text_layer_detected,
                    "ocr_used": False,
                    "pages": [
                        {
                            "page_number": page.get("page_number"),
                            "text_length": len(page.get("text", "")),
                        }
                        for page in pages
                    ],
                },
            }
        )

        last_transaction = transactions[-1] if transactions else None
        response["parser_debug"]["final_page_check"] = {
            "last_transaction_detected": bool(last_transaction),
            "last_transaction_date": last_transaction.get("transaction_date") if last_transaction else None,
            "last_transaction_description": last_transaction.get("description_raw") if last_transaction else None,
            "last_transaction_amount": abs(last_transaction.get("paid_out", 0.0)) if last_transaction else None,
            "last_transaction_balance": last_transaction.get("balance_after") if last_transaction else None,
        }

        return response

    def _parse_transactions(self, pages: List[Dict]) -> (List[Dict], List[str]):
        blocks = self._build_transaction_blocks(pages)
        parsed = []
        issues = []

        for page_number, block in blocks:
            transaction = self._parse_transaction_block(block, page_number)
            if transaction:
                parsed.append(transaction)
            else:
                issues.append("transaction_parse_failed")

        parsed = self._assign_directions(parsed, issues)
        return parsed, issues

    def _build_transaction_blocks(self, pages: List[Dict]) -> List[tuple]:
        blocks = []
        current_block = []
        current_page = None

        for page in pages:
            page_number = page.get("page_number")
            for line in page.get("text", "").splitlines():
                normalized = line.strip()
                if not normalized:
                    continue

                if self._is_footer_line(normalized):
                    if current_block:
                        blocks.append((current_page, current_block))
                        current_block = []
                        current_page = None
                    continue

                if DATE_PATTERN.search(normalized):
                    if current_block:
                        blocks.append((current_page, current_block))
                    current_block = [normalized]
                    current_page = page_number
                    continue

                if current_block:
                    current_block.append(normalized)

            if current_block:
                blocks.append((current_page, current_block))
                current_block = []
                current_page = None

        if current_block:
            blocks.append((current_page, current_block))

        return blocks

    def _is_footer_line(self, line: str) -> bool:
        lower_line = line.lower()
        return any(token in lower_line for token in FOOTER_PATTERNS)

    def _parse_transaction_block(self, block: List[str], page_number: Optional[int]) -> Optional[Dict]:
        first_line = block[0]
        date_match = DATE_PATTERN.search(first_line)
        if not date_match:
            return None

        transaction_date = self._parse_date(date_match.group(0))
        content_lines = block

        money_values = [self._parse_money(value) for value in MONEY_PATTERN.findall(" ".join(content_lines))]
        balance_after = money_values[-1] if money_values else None
        amount = money_values[-2] if len(money_values) >= 2 else None

        description = self._extract_description(content_lines, date_match.group(0))

        return {
            "transaction_date": transaction_date,
            "description_raw": description,
            "paid_out": 0.0,
            "paid_in": 0.0,
            "amount": amount or 0.0,
            "direction": "unknown",
            "balance_after": balance_after or 0.0,
            "page_number": page_number or 0,
            "row_index": 0,
        }

    def _extract_description(self, lines: List[str], date_text: str) -> str:
        description_parts = []
        for line in lines:
            cleaned = MONEY_PATTERN.sub("", line)
            cleaned = DATE_PATTERN.sub("", cleaned)
            cleaned = cleaned.replace("|", " ").strip()
            if cleaned and not self._is_footer_line(cleaned):
                description_parts.append(cleaned)

        return " ".join(description_parts).strip()

    def _parse_money(self, value: str) -> float:
        return float(value.replace("£", "").replace(",", ""))

    def _parse_date(self, raw_date: str) -> str:
        match = DATE_PATTERN.search(raw_date)
        if not match:
            return raw_date

        day_token, month_token, year_token = match.groups()
        day = re.sub(r"(st|nd|rd|th)$", "", day_token, flags=re.IGNORECASE)
        month = MONTH_MAP.get(month_token[:3].lower(), "01")
        year = int(year_token)
        year += 2000 if year < 100 else 0
        return f"{year:04d}-{month}-{int(day):02d}"

    def _assign_directions(self, transactions: List[Dict], issues: List[str]) -> List[Dict]:
        for index, transaction in enumerate(transactions):
            amount = float(transaction.get("amount", 0.0))
            balance = transaction.get("balance_after")
            older_balance = None

            if index + 1 < len(transactions):
                older_balance = transactions[index + 1].get("balance_after")

            direction = self._infer_direction(amount, balance, older_balance, transaction.get("description_raw", ""))
            transaction["direction"] = direction
            if direction == "debit":
                transaction["paid_out"] = amount
                transaction["paid_in"] = 0.0
                transaction["amount"] = -abs(amount)
            elif direction == "credit":
                transaction["paid_in"] = amount
                transaction["paid_out"] = 0.0
                transaction["amount"] = abs(amount)
            else:
                transaction["paid_out"] = 0.0
                transaction["paid_in"] = 0.0
                if "unknown_direction" not in issues:
                    issues.append("unknown_direction")

            transaction["row_index"] = index + 1

        return transactions

    def _infer_direction(
        self,
        amount: float,
        balance: Optional[float],
        older_balance: Optional[float],
        description: str,
    ) -> str:
        if amount and balance is not None and older_balance is not None:
            if abs((older_balance - balance) - amount) <= TOLERANCE:
                return "debit"
            if abs((older_balance - balance) + amount) <= TOLERANCE:
                return "credit"

        normalized = description.lower()
        if any(clue in normalized for clue in CREDIT_CLUES):
            return "credit"
        if any(clue in normalized for clue in DEBIT_CLUES):
            return "debit"

        return "unknown"
