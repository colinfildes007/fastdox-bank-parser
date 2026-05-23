import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from app.parsers.base import BaseStatementParser
from app.services.reconciliation import reconcile


MONTH_TO_NUM = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

# A Barclays row begins "DD Mmm" (e.g. "20 May").
DATE_PREFIX = re.compile(
    r"^\s*(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b",
    re.IGNORECASE,
)
MONEY_TOKEN = re.compile(r"-?£?\d{1,3}(?:,\d{3})*\.\d{2}")

# "At a glance" summary block.
START_BALANCE_RE = re.compile(r"Start\s+balance\s*[:\-]?\s*£?\s*([\d,]+\.\d{2})", re.IGNORECASE)
END_BALANCE_RE = re.compile(r"End\s+balance\s*[:\-]?\s*£?\s*([\d,]+\.\d{2})", re.IGNORECASE)
MONEY_IN_RE = re.compile(r"Money\s+in\s*[:\-]?\s*£?\s*([\d,]+\.\d{2})", re.IGNORECASE)
MONEY_OUT_RE = re.compile(r"Money\s+out\s*[:\-]?\s*£?\s*([\d,]+\.\d{2})", re.IGNORECASE)

# Statement period: "19 May - 18 Aug 2023"
PERIOD_RE = re.compile(
    r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*[-–]\s*"
    r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})",
    re.IGNORECASE,
)

# Description prefix -> derived transaction type. Order matters (specific first).
TRANSACTION_TYPE_PATTERNS = [
    (re.compile(r"start\s+balance", re.IGNORECASE), "start_balance"),
    (re.compile(r"end\s+balance", re.IGNORECASE), "end_balance"),
    (re.compile(r"cash\s+machine\s+withdrawal", re.IGNORECASE), "cash_machine_withdrawal"),
    (re.compile(r"card\s+payment", re.IGNORECASE), "card_payment"),
    (re.compile(r"card\s+purchase", re.IGNORECASE), "card_purchase"),
    (re.compile(r"bill\s+payment", re.IGNORECASE), "bill_payment"),
    (re.compile(r"direct\s+debit", re.IGNORECASE), "direct_debit"),
    (re.compile(r"received\s+from", re.IGNORECASE), "received_from"),
    (re.compile(r"transfer\s+from", re.IGNORECASE), "transfer_from"),
    (re.compile(r"transfer\s+to", re.IGNORECASE), "transfer_to"),
]

CREDIT_TYPES = {"received_from", "transfer_from"}
DEBIT_TYPES = {
    "card_payment", "card_purchase", "bill_payment", "transfer_to",
    "direct_debit", "cash_machine_withdrawal",
}
BALANCE_MARKER_TYPES = {"start_balance", "end_balance"}

# Once any of these phrases is seen on a page, transactions stop. Pages 21-22
# of the sample contain only informational content keyed by these markers.
STOP_MARKERS = [
    "important information",
    "your benefits at a glance",
    "how to contact",
    "if you change your mind",
    "how to make a complaint",
    "explanation of terms",
    "about your account",
]


class BarclaysStatementParser(BaseStatementParser):
    bank_name = "Barclays"
    parser_name = "barclays_family_text_v1"
    parser_adapter = "barclays_family_v1"
    adapter_version = "1.0.0"

    def can_parse(self, context: Dict) -> bool:
        hint = (context.get("bank_hint") or "").lower()
        detected = (context.get("detected_bank") or "").lower()
        if hint == "barclays" or "barclays" in detected:
            return True
        return "barclays" in (context.get("all_text") or "").lower()

    def parse(self, context: Dict) -> Dict:
        pages = context.get("pages", [])
        all_text = context.get("all_text", "")
        page_count = context.get("page_count", 0)

        summary = self._extract_summary(all_text)
        statement_start_date, statement_end_date = self._extract_period(all_text)

        transactions, page_stats, debug_stats = self._parse_transactions(
            pages, statement_start_date, statement_end_date
        )
        transactions, debug_stats = self._deduplicate(transactions, debug_stats)

        opening_balance = summary.get("start_balance")
        closing_balance = summary.get("end_balance")
        statement_total_credits = summary.get("money_in")
        statement_total_debits = summary.get("money_out")

        calculated_total_credits = round(
            sum(float(tx.get("paid_in", 0.0)) for tx in transactions), 2
        )
        calculated_total_debits = round(
            sum(float(tx.get("paid_out", 0.0)) for tx in transactions), 2
        )

        statement = {
            "bank_name": self.bank_name,
            "account_holder": None,
            "account_number": None,
            "sort_code": None,
            "statement_start_date": statement_start_date,
            "statement_end_date": statement_end_date,
            "opening_balance": opening_balance,
            "closing_balance": closing_balance,
            "total_credits": statement_total_credits,
            "total_debits": statement_total_debits,
            "currency": "GBP",
        }
        reconciliation_result = reconcile(statement, transactions)

        per_page_counts = {str(s["page_number"]): s["transaction_rows"] for s in page_stats}

        parser_debug = {
            "parser_name": self.parser_name,
            "adapter_selected": self.parser_adapter,
            "parser_adapter": self.parser_adapter,
            "page_count": page_count,
            "text_layer_detected": context.get("text_layer_detected", False),
            "summary_block_found": any(
                summary.get(k) is not None
                for k in ("start_balance", "end_balance", "money_in", "money_out")
            ),
            "statement_period_found": statement_start_date is not None and statement_end_date is not None,
            "opening_balance_found": opening_balance is not None,
            "closing_balance_found": closing_balance is not None,
            "statement_total_credits_found": statement_total_credits is not None,
            "statement_total_debits_found": statement_total_debits is not None,
            "transaction_pages_detected": debug_stats.get("transaction_pages_detected", []),
            "date_matches_found": debug_stats.get("date_matches_found", 0),
            "candidate_transaction_rows": debug_stats.get("candidate_transaction_rows", 0),
            "transactions_returned": len(transactions),
            "duplicate_transaction_count": debug_stats.get("duplicate_transaction_count", 0),
            "calculated_total_credits": calculated_total_credits,
            "calculated_total_debits": calculated_total_debits,
            "per_page_transaction_counts": per_page_counts,
            "first_transaction": transactions[0] if transactions else None,
            "last_transaction": transactions[-1] if transactions else None,
        }

        response = self.build_response(context)
        response.update({
            "bank_name": self.bank_name,
            "statement": statement,
            "accounts": [],
            "transactions": transactions,
            "issues": [],
            "reconciliation": reconciliation_result,
            "parser_debug": parser_debug,
        })
        return response

    # ---- summary / period --------------------------------------------------

    def _extract_summary(self, text: str) -> Dict[str, Optional[float]]:
        def grab(pattern):
            match = pattern.search(text)
            if not match:
                return None
            return round(float(match.group(1).replace(",", "")), 2)
        return {
            "start_balance": grab(START_BALANCE_RE),
            "end_balance": grab(END_BALANCE_RE),
            "money_in": grab(MONEY_IN_RE),
            "money_out": grab(MONEY_OUT_RE),
        }

    def _extract_period(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        match = PERIOD_RE.search(text)
        if not match:
            return None, None
        d1, m1, d2, m2, year = match.groups()
        end_year = int(year)
        m1_num = MONTH_TO_NUM[m1[:3].title()]
        m2_num = MONTH_TO_NUM[m2[:3].title()]
        # Statement spans Dec/Jan -> start year is the previous one.
        start_year = end_year - 1 if m1_num > m2_num else end_year
        start = f"{start_year:04d}-{m1_num:02d}-{int(d1):02d}"
        end = f"{end_year:04d}-{m2_num:02d}-{int(d2):02d}"
        return start, end

    # ---- transaction parsing ----------------------------------------------

    def _parse_transactions(
        self,
        pages: List[Dict],
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> Tuple[List[Dict], List[Dict], Dict]:
        transactions: List[Dict] = []
        page_stats: List[Dict] = []
        debug = {
            "transaction_pages_detected": [],
            "date_matches_found": 0,
            "candidate_transaction_rows": 0,
        }

        for page in pages:
            page_text = page.get("text", "") or ""
            page_number = page.get("page_number")

            # Pages 21-22 etc. are informational. Cut the page at the first
            # stop marker so any trailing date-like phrases below it cannot
            # be parsed as transactions.
            cut = self._first_stop_offset(page_text.lower())
            section = page_text[:cut] if cut is not None else page_text

            rows = self._extract_rows(section)
            if rows:
                debug["transaction_pages_detected"].append(page_number)
            debug["candidate_transaction_rows"] += len(rows)

            page_rows: List[Dict] = []
            for row_index, row in enumerate(rows, start=1):
                transaction = self._build_transaction(
                    row, page_number, row_index, start_date, end_date
                )
                if transaction is None:
                    continue
                debug["date_matches_found"] += 1
                page_rows.append(transaction)

            transactions.extend(page_rows)
            page_stats.append({"page_number": page_number, "transaction_rows": len(page_rows)})

        return transactions, page_stats, debug

    def _first_stop_offset(self, lower_text: str) -> Optional[int]:
        offsets = [lower_text.find(marker) for marker in STOP_MARKERS if marker in lower_text]
        return min(offsets) if offsets else None

    def _extract_rows(self, section_text: str) -> List[Dict]:
        rows: List[Dict] = []
        current: Optional[Dict] = None
        for line in section_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            date_match = DATE_PREFIX.match(stripped)
            if date_match:
                if current is not None:
                    rows.append(current)
                current = {
                    "date_text": f"{int(date_match.group(1))} {date_match.group(2).title()}",
                    "lines": [stripped],
                }
            elif current is not None:
                current["lines"].append(stripped)
        if current is not None:
            rows.append(current)
        return rows

    def _build_transaction(
        self,
        row: Dict,
        page_number: Optional[int],
        row_index: int,
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> Optional[Dict]:
        joined = " ".join(row["lines"])

        # Skip the leading date when extracting the description.
        prefix = DATE_PREFIX.match(joined)
        body_text = joined[prefix.end():] if prefix else joined

        money_matches = list(MONEY_TOKEN.finditer(body_text))
        if not money_matches:
            return None
        money_tokens = [match.group(0) for match in money_matches]

        # Find where the trailing run of money tokens begins (tokens separated
        # only by whitespace, anchored at end of body_text). Description is
        # everything before that.
        trailing_start = len(body_text)
        for match in reversed(money_matches):
            if body_text[match.end():trailing_start].strip() == "":
                trailing_start = match.start()
            else:
                break
        description = re.sub(r"\s{2,}", " ", body_text[:trailing_start]).strip(" ,;-")

        derived_type = self._derive_transaction_type(description)
        # Start/end balance markers are recorded by the summary, not as
        # transactions.
        if derived_type in BALANCE_MARKER_TYPES:
            return None

        money_values = [self._parse_money(t) for t in money_tokens]
        paid_in = 0.0
        paid_out = 0.0
        balance_after: Optional[float] = None
        if len(money_values) >= 3:
            paid_out = money_values[-3]
            paid_in = money_values[-2]
            balance_after = money_values[-1]
        elif len(money_values) == 2:
            balance_after = money_values[-1]
            amount = money_values[-2]
            if derived_type in CREDIT_TYPES:
                paid_in = amount
            elif derived_type in DEBIT_TYPES:
                paid_out = amount
            else:
                # Direction unknown — recorded as a debit by default; the
                # reconciliation will surface the discrepancy if wrong.
                paid_out = amount
        elif len(money_values) == 1:
            # Only a balance line (start/end balance handled above; otherwise
            # not a parsable transaction row).
            return None

        transaction_date = self._parse_date(row["date_text"], start_date, end_date)
        if transaction_date is None:
            return None

        amount = paid_in if paid_in > 0 else paid_out
        direction = "credit" if paid_in > 0 else "debit" if paid_out > 0 else "unknown"

        return {
            "transaction_id": None,
            "transaction_date": transaction_date,
            "description_raw": description,
            "description_clean": description,
            "transaction_type": derived_type or "unknown",
            "derived_transaction_type": derived_type or "unknown",
            "amount": round(amount, 2),
            "debit": round(paid_out, 2),
            "credit": round(paid_in, 2),
            "paid_out": round(paid_out, 2),
            "paid_in": round(paid_in, 2),
            "balance_after": round(balance_after if balance_after is not None else 0.0, 2),
            "type": direction,
            "page_number": page_number or 0,
            "row_index": row_index,
            "source_line_start": None,
            "source_line_end": None,
            "parser_adapter": self.parser_adapter,
            "confidence": 0.9,
        }

    def _derive_transaction_type(self, description: str) -> str:
        text = description or ""
        for pattern, name in TRANSACTION_TYPE_PATTERNS:
            if pattern.search(text):
                return name
        return "unknown"

    def _parse_money(self, token: str) -> float:
        return round(float(token.replace("£", "").replace(",", "")), 2)

    def _parse_date(
        self,
        date_text: str,
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> Optional[str]:
        match = re.match(r"(\d{1,2})\s+([A-Za-z]{3})", date_text)
        if not match:
            return None
        day = int(match.group(1))
        month = MONTH_TO_NUM.get(match.group(2).title())
        if month is None:
            return None
        year = self._infer_year(month, start_date, end_date)
        return f"{year:04d}-{month:02d}-{day:02d}"

    def _infer_year(
        self,
        month: int,
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> int:
        if start_date and end_date:
            start_year, start_month = int(start_date[:4]), int(start_date[5:7])
            end_year, end_month = int(end_date[:4]), int(end_date[5:7])
            if start_year == end_year:
                return start_year
            # spans Dec / Jan
            if month >= start_month:
                return start_year
            return end_year
        if start_date:
            return int(start_date[:4])
        if end_date:
            return int(end_date[:4])
        return datetime.now().year

    # ---- duplicate handling ------------------------------------------------

    def _deduplicate(
        self,
        transactions: List[Dict],
        debug: Dict,
    ) -> Tuple[List[Dict], Dict]:
        """Strong-key dedup that *includes* page_number and row_index, so two
        rows sharing date / description / amount / balance on different lines
        of the statement are NOT collapsed. Only an exact double-read at the
        same source position is removed."""
        seen = set()
        deduped: List[Dict] = []
        removed = 0
        for tx in transactions:
            key = (
                tx.get("transaction_date"),
                (tx.get("description_raw") or "").strip().upper(),
                round(float(tx.get("paid_in", 0.0) or 0.0), 2),
                round(float(tx.get("paid_out", 0.0) or 0.0), 2),
                round(float(tx.get("balance_after", 0.0) or 0.0), 2),
                tx.get("page_number"),
                tx.get("row_index"),
            )
            if key in seen:
                removed += 1
                continue
            seen.add(key)
            deduped.append(tx)
        debug["duplicate_transaction_count"] = removed
        return deduped, debug
