import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from app.parsers.base import BaseStatementParser
from app.services.reconciliation import extract_statement_totals, reconcile

DATE_PATTERN = re.compile(
    r"\b(\d{1,2}(?:/\d{1,2}(?:/\d{2,4})?|\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{2,4}))\b",
    re.IGNORECASE,
)
MONEY_PATTERN = re.compile(r"£?[\d,]+\.\d{2}")
FOOTER_PATTERNS = [
    "total debits",
    "total debit",
    "total credits",
    "total credit",
    "closing balance",
    "statement balance",
    "account number",
    "sort code",
    "account name",
    "account holder",
    "statement period",
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


class LloydsStatementParser(BaseStatementParser):
    bank_name = "Lloyds Bank"
    parser_name = "lloyds_family_text_v1"
    parser_adapter = "lloyds_family_v1"

    def can_parse(self, context: Dict) -> bool:
        bank_hint = (context.get("bank_hint") or "").lower()
        detected_bank = (context.get("detected_bank") or "").lower()
        all_text = (context.get("all_text") or "").lower()

        if any(value in bank_hint for value in ["lloyds", "halifax", "bank of scotland", "bos"]):
            return True

        if any(value in detected_bank for value in ["lloyds", "halifax", "bank of scotland", "bos"]):
            return True

        return any(value in all_text for value in ["lloyds bank", "halifax", "bank of scotland", "bos"])

    def parse(self, context: Dict) -> Dict:
        pages = context.get("pages", [])
        all_text = context.get("all_text", "")
        page_count = context.get("page_count", 0)
        detected_bank = context.get("detected_bank") or self.bank_name

        statement_info = self._extract_statement_info(all_text)
        statement_start_date = statement_info["statement_start_date"]
        statement_end_date = statement_info["statement_end_date"]

        transactions, issues = self._parse_transactions(
            pages,
            statement_start_date=statement_start_date,
            statement_end_date=statement_end_date,
        )

        if not statement_start_date or not statement_end_date:
            dates = [tx.get("transaction_date") for tx in transactions if tx.get("transaction_date")]
            if dates:
                statement_start_date = statement_start_date or min(dates)
                statement_end_date = statement_end_date or max(dates)

        totals = extract_statement_totals(all_text)
        opening_balance = totals.get("derived_opening_balance")
        if opening_balance is None:
            opening_balance = self._estimate_opening_balance(transactions)

        statement = {
            "bank_name": detected_bank,
            "account_holder": statement_info["account_holder"],
            "account_number": statement_info["account_number"],
            "sort_code": statement_info["sort_code"],
            "statement_start_date": statement_start_date,
            "statement_end_date": statement_end_date,
            "opening_balance": opening_balance,
            "closing_balance": totals.get("closing_balance"),
            "total_credits": totals.get("total_credits"),
            "total_debits": totals.get("total_debits"),
            "derived_opening_balance": totals.get("derived_opening_balance"),
            "currency": "GBP",
        }
        reconciliation_result = reconcile(statement, transactions)

        response = self.build_response(context)
        response.update(
            {
                "bank_name": detected_bank,
                "statement": statement,
                "accounts": [],
                "transactions": transactions,
                "issues": issues,
                "reconciliation": reconciliation_result,
                "parser_debug": {
                    "parser_name": self.parser_name,
                    "page_count": page_count,
                    "text_layer_detected": context.get("text_layer_detected", False),
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

        return response

    def _extract_statement_info(self, text: str) -> Dict[str, Optional[str]]:
        account_holder = self._find_label_value(text, ["account holder", "account name"])
        if not account_holder:
            account_holder = self._find_label_value(text, ["name"])

        account_number = self._find_label_value(text, ["account number"], allow_masked=True)
        sort_code = self._find_label_value(text, ["sort code"], allow_masked=True)
        statement_start_date, statement_end_date = self._extract_statement_period(text)

        return {
            "account_holder": account_holder,
            "account_number": account_number,
            "sort_code": sort_code,
            "statement_start_date": statement_start_date,
            "statement_end_date": statement_end_date,
        }

    def _find_label_value(self, text: str, labels: List[str], allow_masked: bool = False) -> Optional[str]:
        for label in labels:
            if label == "name":
                pattern = re.compile(
                    rf"\b(?:account\s+)?name\b\s*[:\-]?\s*([\d\*\s\-]+|[A-Za-z0-9 \&\,\.'/-]+)",
                    re.IGNORECASE,
                )
            else:
                pattern = re.compile(
                    rf"\b{re.escape(label)}\b\s*[:\-]?\s*([\d\*\s\-]+|[A-Za-z0-9 \&\,\.'/-]+)",
                    re.IGNORECASE,
                )
            match = pattern.search(text)
            if match:
                value = match.group(1).strip()
                if allow_masked:
                    value = value.replace(" ", "")
                return value
        return None

    def _extract_statement_period(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        period_match = re.search(
            r"statement period\s*[:\-]?\s*(\d{1,2}(?:\s+\w+\s+\d{2,4}|/\d{1,2}(?:/\d{2,4})?))\s*(?:to|-)\s*(\d{1,2}(?:\s+\w+\s+\d{2,4}|/\d{1,2}(?:/\d{2,4})?))",
            text,
            re.IGNORECASE,
        )
        if period_match:
            start_date = self._parse_date(period_match.group(1))
            end_date = self._parse_date(period_match.group(2), statement_start_date=start_date)
            return start_date, end_date
        return None, None

    def _parse_transactions(
        self,
        pages: List[Dict],
        statement_start_date: Optional[str],
        statement_end_date: Optional[str],
    ) -> (List[Dict], List[str]):
        blocks = self._build_transaction_blocks(pages)
        parsed = []
        issues = []

        for page_number, block in blocks:
            transaction = self._parse_transaction_block(
                block,
                page_number,
                statement_start_date,
                statement_end_date,
            )
            if transaction:
                parsed.append(transaction)
            else:
                issues.append("transaction_parse_failed")

        parsed = self._assign_directions(parsed, issues)
        return parsed, issues

    def _build_transaction_blocks(self, pages: List[Dict]) -> List[tuple]:
        blocks = []
        current_block: List[str] = []
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

        return blocks

    def _is_footer_line(self, line: str) -> bool:
        lower_line = line.lower()
        return any(token in lower_line for token in FOOTER_PATTERNS)

    def _parse_transaction_block(
        self,
        block: List[str],
        page_number: Optional[int],
        statement_start_date: Optional[str],
        statement_end_date: Optional[str],
    ) -> Optional[Dict]:
        first_line = block[0]
        data_match = DATE_PATTERN.search(first_line)
        if not data_match:
            return None

        transaction_date = self._parse_date(
            data_match.group(1),
            statement_start_date=statement_start_date,
            statement_end_date=statement_end_date,
        )
        raw_text = " ".join(block)
        money_values = [self._parse_money(value) for value in MONEY_PATTERN.findall(raw_text)]

        balance_after = money_values[-1] if len(money_values) >= 1 else None
        debit_amount = 0.0
        credit_amount = 0.0
        amount = 0.0

        if len(money_values) >= 3:
            debit_amount = money_values[-3]
            credit_amount = money_values[-2]
            amount = debit_amount if debit_amount > 0 else credit_amount
        elif len(money_values) >= 2:
            amount = money_values[-2]

        description = self._extract_description(block, data_match.group(1))

        return {
            "transaction_id": None,
            "transaction_date": transaction_date,
            "description_raw": description,
            "description_clean": description,
            "amount": round(amount or 0.0, 2),
            "debit": round(debit_amount, 2),
            "credit": round(credit_amount, 2),
            "balance_after": round(balance_after or 0.0, 2),
            "type": "debit" if debit_amount > 0 else "credit" if credit_amount > 0 else "unknown",
            "page_number": page_number or 0,
            "row_index": 0,
            "confidence": 0.95,
        }

    def _extract_description(self, lines: List[str], date_text: str) -> str:
        description_parts = []
        for line in lines:
            cleaned = MONEY_PATTERN.sub("", line)
            cleaned = DATE_PATTERN.sub("", cleaned)
            cleaned = cleaned.replace("|", " ").strip()
            if cleaned and not self._is_footer_line(cleaned):
                description_parts.append(cleaned)

        description = " ".join(description_parts).strip()
        return re.sub(r"\s{2,}", " ", description)

    def _parse_money(self, value: str) -> float:
        return round(float(value.replace("£", "").replace(",", "")), 2)

    def _parse_date(
        self,
        raw_date: str,
        statement_start_date: Optional[str] = None,
        statement_end_date: Optional[str] = None,
    ) -> str:
        raw_date = raw_date.strip()
        if "/" in raw_date:
            parts = raw_date.split("/")
            if len(parts) == 2:
                day, month = parts
                day = day.zfill(2)
                month = month.zfill(2)
                year = self._infer_year_for_month(month, statement_start_date, statement_end_date)
            else:
                day, month, year = parts
                day = day.zfill(2)
                month = month.zfill(2)
                year = year.strip()
                year = year if len(year) == 4 else f"20{year}"
            return f"{year}-{month}-{day}"

        match = re.search(
            r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{2,4})",
            raw_date,
            re.IGNORECASE,
        )
        if not match:
            return raw_date

        day, month_token, year_token = match.groups()
        month = MONTH_MAP.get(month_token[:3].lower(), "01")
        year = year_token if len(year_token) == 4 else f"20{year_token}"
        return f"{year}-{month}-{int(day):02d}"

    def _infer_year_for_month(
        self,
        month: str,
        statement_start_date: Optional[str],
        statement_end_date: Optional[str],
    ) -> str:
        if statement_start_date and statement_end_date:
            try:
                start_year = int(statement_start_date[:4])
                start_month = int(statement_start_date[5:7])
                end_year = int(statement_end_date[:4])
                end_month = int(statement_end_date[5:7])
                target_month = int(month)

                if start_year == end_year:
                    return str(start_year)

                if target_month >= start_month:
                    return str(start_year)
                if target_month <= end_month:
                    return str(end_year)
            except ValueError:
                pass

        if statement_start_date:
            return statement_start_date[:4]
        if statement_end_date:
            return statement_end_date[:4]

        return str(datetime.now().year)

    def _assign_directions(self, transactions: List[Dict], issues: List[str]) -> List[Dict]:
        for index, transaction in enumerate(transactions):
            debit_amount = float(transaction.get("debit", 0.0))
            credit_amount = float(transaction.get("credit", 0.0))
            amount = abs(float(transaction.get("amount", 0.0)))
            balance = transaction.get("balance_after")
            older_balance = None
            if index + 1 < len(transactions):
                older_balance = transactions[index + 1].get("balance_after")

            if debit_amount > 0:
                direction = "debit"
            elif credit_amount > 0:
                direction = "credit"
            else:
                direction = self._infer_direction(amount, balance, older_balance, transaction.get("description_raw", ""))

            transaction["type"] = direction
            if direction == "debit":
                transaction["debit"] = amount or debit_amount
                transaction["credit"] = 0.0
                transaction["paid_out"] = amount or debit_amount
                transaction["paid_in"] = 0.0
                transaction["amount"] = -abs(amount or debit_amount)
            elif direction == "credit":
                transaction["credit"] = amount or credit_amount
                transaction["debit"] = 0.0
                transaction["paid_in"] = amount or credit_amount
                transaction["paid_out"] = 0.0
                transaction["amount"] = abs(amount or credit_amount)
            else:
                transaction["debit"] = 0.0
                transaction["credit"] = 0.0
                transaction["paid_in"] = 0.0
                transaction["paid_out"] = 0.0
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
        if "credit" in normalized or "received" in normalized or "salary" in normalized:
            return "credit"
        if "debit" in normalized or "withdrawal" in normalized or "payment" in normalized:
            return "debit"

        return "unknown"

    def _estimate_opening_balance(self, transactions: List[Dict]) -> Optional[float]:
        if not transactions:
            return None

        first_transaction = transactions[0]
        amount = abs(first_transaction.get("amount", 0.0))
        balance = first_transaction.get("balance_after")
        if balance is None:
            return None

        if first_transaction.get("type") == "debit":
            return round(balance + amount, 2)
        if first_transaction.get("type") == "credit":
            return round(balance - amount, 2)

        return None
