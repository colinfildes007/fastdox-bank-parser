import hashlib
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from app.parsers.base import BaseStatementParser
from app.services.reconciliation import reconcile


MONTH_TO_NUM = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

# A leading "DD Mmm" prefix (e.g. "20 May"). Used to detect the current date
# line; the date may carry forward across many transaction rows.
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

PERIOD_RE = re.compile(
    r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*[-–]\s*"
    r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})",
    re.IGNORECASE,
)

# Account-metadata patterns.
SORT_CODE_RE = re.compile(r"(\d{2}-\d{2}-\d{2})")
ACCOUNT_NUMBER_RE = re.compile(r"Account\s*(?:No\.?|Number)\s*[:\-]?\s*(\d{6,12})", re.IGNORECASE)
IBAN_RE = re.compile(r"\b(GB\d{2}\s*[A-Z]{4}(?:\s*\d{2,4}){3,5})\b")
SWIFT_RE = re.compile(r"\b(?:BIC|SWIFT)\b[^A-Z0-9]*([A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b", re.IGNORECASE)
HOLDER_RE = re.compile(r"\b(M(?:r|rs|iss|s)\.?\s+[A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+){1,4})\b")

# Description prefix -> derived transaction type. Longest / most-specific first.
TRANSACTION_TYPE_PATTERNS = [
    (re.compile(r"start\s+balance", re.IGNORECASE), "start_balance"),
    (re.compile(r"end\s+balance", re.IGNORECASE), "end_balance"),
    (re.compile(r"cash\s+machine\s+withdrawal", re.IGNORECASE), "cash_machine_withdrawal"),
    (re.compile(r"card\s+payment", re.IGNORECASE), "card_payment"),
    (re.compile(r"card\s+purchase", re.IGNORECASE), "card_purchase"),
    (re.compile(r"bill\s+payment\s+from", re.IGNORECASE), "bill_payment_from"),
    (re.compile(r"bill\s+payment\s+to", re.IGNORECASE), "bill_payment_to"),
    (re.compile(r"bill\s+payment", re.IGNORECASE), "bill_payment"),
    (re.compile(r"direct\s+debit", re.IGNORECASE), "direct_debit"),
    (re.compile(r"received\s+from", re.IGNORECASE), "received_from"),
    (re.compile(r"transfer\s+from", re.IGNORECASE), "transfer_from"),
    (re.compile(r"transfer\s+to", re.IGNORECASE), "transfer_to"),
]

# Phrases that mark the start of a transaction. Each new occurrence finalises
# the previous row and starts a new one (with the carried-forward date).
TRANSACTION_PHRASES = [
    "card payment to",
    "card payment",
    "card purchase",
    "bill payment from",
    "bill payment to",
    "bill payment",
    "transfer from",
    "transfer to",
    "direct debit to",
    "direct debit",
    "cash machine withdrawal",
    "received from",
    "start balance",
    "end balance",
]

CREDIT_TYPES = {"received_from", "transfer_from", "bill_payment_from"}
DEBIT_TYPES = {
    "card_payment", "card_purchase", "bill_payment", "bill_payment_to",
    "transfer_to", "direct_debit", "cash_machine_withdrawal",
}
BALANCE_MARKER_TYPES = {"start_balance", "end_balance"}

TRANSACTION_SECTION_ANCHOR = "your transactions"

# Stop markers — once seen, the transaction table is over.
STOP_MARKERS = [
    "anything wrong",
    "credit interest rates",
    "how it works",
    "dispute resolution",
    "important information about compensation",
    "important information about your account",
    "your benefits at a glance",
    "how to contact",
    "if you change your mind",
    "how to make a complaint",
    "explanation of terms",
]


class BarclaysStatementParser(BaseStatementParser):
    bank_name = "Barclays"
    parser_name = "barclays_family_text_v1"
    parser_adapter = "barclays_family_v1"
    adapter_version = "1.0.2"

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

        deterministic_run_id = hashlib.sha1((all_text or "").encode("utf-8")).hexdigest()[:12]
        page_processing_order = [page.get("page_number") for page in pages]

        summary = self._extract_summary(all_text)
        statement_start_date, statement_end_date = self._extract_period(all_text)
        account_metadata = self._extract_account_metadata(all_text)

        transactions, page_stats, parse_debug = self._parse_transactions(
            pages, statement_start_date, statement_end_date
        )
        transactions, parse_debug = self._deduplicate(transactions, parse_debug)

        opening_balance = summary.get("start_balance")
        closing_balance = summary.get("end_balance")
        statement_total_credits = summary.get("money_in")
        statement_total_debits = summary.get("money_out")

        calculated_total_credits = round(
            sum(float(tx.get("paid_in", 0.0) or 0.0) for tx in transactions), 2
        )
        calculated_total_debits = round(
            sum(float(tx.get("paid_out", 0.0) or 0.0) for tx in transactions), 2
        )
        credit_rows_returned = sum(
            1 for tx in transactions if float(tx.get("paid_in", 0.0) or 0.0) > 0
        )
        debit_rows_returned = sum(
            1 for tx in transactions if float(tx.get("paid_out", 0.0) or 0.0) > 0
        )
        rows_with_balance_after = sum(
            1 for tx in transactions if tx.get("balance_after") is not None
        )

        per_page = []
        for stat in page_stats:
            page_number = stat["page_number"]
            page_txns = [t for t in transactions if t.get("page_number") == page_number]
            per_page.append(
                {
                    "page_number": page_number,
                    "credit_rows": sum(1 for t in page_txns if float(t.get("paid_in", 0.0) or 0.0) > 0),
                    "debit_rows": sum(1 for t in page_txns if float(t.get("paid_out", 0.0) or 0.0) > 0),
                    "credit_sum": round(sum(float(t.get("paid_in", 0.0) or 0.0) for t in page_txns), 2),
                    "debit_sum": round(sum(float(t.get("paid_out", 0.0) or 0.0) for t in page_txns), 2),
                }
            )

        statement = {
            "bank_name": self.bank_name,
            "account_holder": account_metadata.get("account_holder"),
            "account_number": account_metadata.get("account_number"),
            "sort_code": account_metadata.get("sort_code"),
            "iban": account_metadata.get("iban"),
            "swift_bic": account_metadata.get("swift_bic"),
            "statement_start_date": statement_start_date,
            "statement_end_date": statement_end_date,
            "opening_balance": opening_balance,
            "closing_balance": closing_balance,
            "total_credits": statement_total_credits,
            "total_debits": statement_total_debits,
            "currency": "GBP",
        }
        reconciliation_result = reconcile(statement, transactions)

        per_page_counts = {
            str(stat["page_number"]): stat["transaction_rows"] for stat in page_stats
        }

        parser_debug = {
            "parser_name": self.parser_name,
            "adapter_selected": self.parser_adapter,
            "parser_adapter": self.parser_adapter,
            "adapter_version": self.adapter_version,
            "deterministic_run_id": deterministic_run_id,
            "page_processing_order": page_processing_order,
            "page_count": page_count,
            "text_layer_detected": context.get("text_layer_detected", False),
            "summary_block_found": any(
                summary.get(key) is not None
                for key in ("start_balance", "end_balance", "money_in", "money_out")
            ),
            "statement_period_found": statement_start_date is not None and statement_end_date is not None,
            "opening_balance_found": opening_balance is not None,
            "closing_balance_found": closing_balance is not None,
            "statement_total_credits_found": statement_total_credits is not None,
            "statement_total_debits_found": statement_total_debits is not None,
            "statement_total_credits": statement_total_credits,
            "statement_total_debits": statement_total_debits,
            "transaction_pages_considered": parse_debug["pages_considered"],
            "transaction_pages_skipped": parse_debug["pages_skipped"],
            "transaction_pages_detected": parse_debug["pages_with_rows"],
            "date_matches_found": parse_debug["date_matches"],
            "candidate_rows_found": parse_debug["candidate_rows"],
            "candidate_transaction_rows": parse_debug["candidate_rows"],
            "transactions_returned": len(transactions),
            "non_transaction_rows_discarded": parse_debug["non_transaction_discarded"],
            "header_rows_discarded": parse_debug["header_discarded"],
            "start_balance_marker_found": parse_debug["start_balance_seen"],
            "end_balance_marker_found": parse_debug["end_balance_seen"],
            "rows_with_paid_out": debit_rows_returned,
            "rows_with_paid_in": credit_rows_returned,
            "rows_with_balance_after": rows_with_balance_after,
            "credit_candidate_rows_found": parse_debug["credit_candidates"],
            "debit_candidate_rows_found": parse_debug["debit_candidates"],
            "credit_rows_returned": credit_rows_returned,
            "debit_rows_returned": debit_rows_returned,
            "credit_amount_sum": calculated_total_credits,
            "debit_amount_sum": calculated_total_debits,
            "missing_credit_examples": parse_debug["missing_credit_examples"],
            "missing_debit_examples": parse_debug["missing_debit_examples"],
            "rows_rejected_by_reason": parse_debug["rows_rejected_by_reason"],
            "per_page": per_page,
            "calculated_total_credits": calculated_total_credits,
            "calculated_total_debits": calculated_total_debits,
            "calculated_total_credits_from_returned_transactions": calculated_total_credits,
            "calculated_total_debits_from_returned_transactions": calculated_total_debits,
            "duplicate_transaction_count": parse_debug["duplicate_count"],
            "per_page_transaction_counts": per_page_counts,
            "first_transaction": transactions[0] if transactions else None,
            "last_transaction": transactions[-1] if transactions else None,
            "first_5_transactions": transactions[:5],
            "last_5_transactions": transactions[-5:],
            "first_rejected_candidate_rows": parse_debug["first_rejected"],
        }

        response = self.build_response(context)
        response.update(
            {
                "bank_name": self.bank_name,
                "statement": statement,
                "accounts": [],
                "transactions": transactions,
                "issues": [],
                "reconciliation": reconciliation_result,
                "parser_debug": parser_debug,
            }
        )
        return response

    # ---- summary / period / account metadata ----------------------------

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
        start_year = end_year - 1 if m1_num > m2_num else end_year
        start = f"{start_year:04d}-{m1_num:02d}-{int(d1):02d}"
        end = f"{end_year:04d}-{m2_num:02d}-{int(d2):02d}"
        return start, end

    def _extract_account_metadata(self, text: str) -> Dict[str, Optional[str]]:
        sort_code_match = SORT_CODE_RE.search(text)
        account_number_match = ACCOUNT_NUMBER_RE.search(text)
        iban_match = IBAN_RE.search(text)
        swift_match = SWIFT_RE.search(text)
        holder_match = HOLDER_RE.search(text)
        return {
            "account_holder": holder_match.group(1).strip() if holder_match else None,
            "sort_code": sort_code_match.group(1) if sort_code_match else None,
            "account_number": account_number_match.group(1) if account_number_match else None,
            "iban": re.sub(r"\s+", " ", iban_match.group(1)).strip() if iban_match else None,
            "swift_bic": swift_match.group(1) if swift_match else None,
        }

    # ---- transaction parsing -------------------------------------------

    def _parse_transactions(
        self,
        pages: List[Dict],
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> Tuple[List[Dict], List[Dict], Dict]:
        pages_considered: List[int] = []
        pages_skipped: List[int] = []
        pages_with_rows: List[int] = []
        first_rejected: List[Dict] = []
        missing_credit_examples: List[Dict] = []
        missing_debit_examples: List[Dict] = []
        debug = {
            "pages_considered": pages_considered,
            "pages_skipped": pages_skipped,
            "pages_with_rows": pages_with_rows,
            "date_matches": 0,
            "candidate_rows": 0,
            "credit_candidates": 0,
            "debit_candidates": 0,
            "non_transaction_discarded": 0,
            "header_discarded": 0,
            "start_balance_seen": False,
            "end_balance_seen": False,
            "first_rejected": first_rejected,
            "missing_credit_examples": missing_credit_examples,
            "missing_debit_examples": missing_debit_examples,
            "rows_rejected_by_reason": {
                "no_amount": 0,
                "ambiguous_direction": 0,
                "header_or_footer": 0,
                "balance_marker": 0,
            },
            "duplicate_count": 0,
        }

        anchor_page = None
        for page in pages:
            text = (page.get("text") or "").lower()
            if TRANSACTION_SECTION_ANCHOR in text:
                anchor_page = page.get("page_number")
                break

        transactions: List[Dict] = []
        page_stats: List[Dict] = []
        stop_reached = False
        carried_date_text: Optional[str] = None

        for page in pages:
            page_number = page.get("page_number")
            page_text = page.get("text", "") or ""

            if anchor_page is None or page_number is None or page_number < anchor_page or stop_reached:
                pages_skipped.append(page_number)
                page_stats.append({"page_number": page_number, "transaction_rows": 0})
                continue

            if page_number == anchor_page:
                idx = page_text.lower().find(TRANSACTION_SECTION_ANCHOR)
                if idx >= 0:
                    section_text = page_text[idx + len(TRANSACTION_SECTION_ANCHOR):]
                else:
                    section_text = page_text
            else:
                section_text = page_text

            stop_offset = self._first_stop_offset(section_text.lower())
            if stop_offset is not None:
                section_text = section_text[:stop_offset]
                stop_reached = True

            if not section_text.strip():
                pages_skipped.append(page_number)
                page_stats.append({"page_number": page_number, "transaction_rows": 0})
                continue

            pages_considered.append(page_number)

            rows, carried_date_text = self._extract_rows(section_text, carried_date_text)
            page_rows: List[Dict] = []
            for row_index, row in enumerate(rows, start=1):
                debug["candidate_rows"] += 1
                transaction, rejection = self._build_transaction(
                    row, page_number, row_index, start_date, end_date
                )

                derived_type = row.get("derived_type")
                if derived_type in CREDIT_TYPES:
                    debug["credit_candidates"] += 1
                elif derived_type in DEBIT_TYPES:
                    debug["debit_candidates"] += 1

                if transaction is None:
                    bucket = self._reason_bucket(rejection)
                    debug["rows_rejected_by_reason"][bucket] = debug["rows_rejected_by_reason"].get(bucket, 0) + 1
                    if rejection in ("start_balance",):
                        debug["start_balance_seen"] = True
                    elif rejection == "end_balance":
                        debug["end_balance_seen"] = True
                    elif rejection == "header_row":
                        debug["header_discarded"] += 1
                    else:
                        debug["non_transaction_discarded"] += 1

                    sample = {
                        "page_number": page_number,
                        "row_index": row_index,
                        "reason": rejection or "unknown",
                        "derived_type": derived_type,
                        "text": " ".join(row["lines"])[:200],
                    }
                    if len(first_rejected) < 15:
                        first_rejected.append(sample)
                    if derived_type in CREDIT_TYPES and len(missing_credit_examples) < 10:
                        missing_credit_examples.append(sample)
                    if derived_type in DEBIT_TYPES and len(missing_debit_examples) < 10:
                        missing_debit_examples.append(sample)
                    continue

                debug["date_matches"] += 1
                page_rows.append(transaction)

            if page_rows:
                pages_with_rows.append(page_number)

            transactions.extend(page_rows)
            page_stats.append(
                {"page_number": page_number, "transaction_rows": len(page_rows)}
            )

        return transactions, page_stats, debug

    def _reason_bucket(self, rejection: Optional[str]) -> str:
        if rejection in ("no_money_tokens", "empty_description", "unparsable_date"):
            return "no_amount"
        if rejection == "no_direction_for_single_amount":
            return "ambiguous_direction"
        if rejection == "header_row":
            return "header_or_footer"
        if rejection in ("start_balance", "end_balance"):
            return "balance_marker"
        return "no_amount"

    def _first_stop_offset(self, lower_text: str) -> Optional[int]:
        offsets = [lower_text.find(marker) for marker in STOP_MARKERS if marker in lower_text]
        return min(offsets) if offsets else None

    def _extract_rows(
        self,
        section_text: str,
        carried_date_text: Optional[str],
    ) -> Tuple[List[Dict], Optional[str]]:
        """Build transaction rows from a section of statement text.

        A Barclays section commonly shows one date followed by several
        transactions (no date prefix per row). The row delimiter is the
        next *transaction phrase* (``Card Payment to``, ``Received From``,
        ``Bill Payment From``…) and the date carries forward until a new
        date appears. Returns ``(rows, last_carried_date_text)`` so the
        carry-forward survives page boundaries.
        """
        rows: List[Dict] = []
        current_date_text = carried_date_text
        current_row: Optional[Dict] = None

        for raw_line in section_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            content = line
            date_match = DATE_PREFIX.match(line)
            if date_match:
                current_date_text = (
                    f"{int(date_match.group(1))} {date_match.group(2).title()}"
                )
                content = line[date_match.end():].strip()
                if not content:
                    continue  # bare date — carries forward, no row yet.

            phrase = self._match_transaction_phrase(content)
            if phrase is not None:
                if current_row is not None:
                    rows.append(current_row)
                derived_type = self._derive_transaction_type(content)
                current_row = {
                    "date_text": current_date_text,
                    "lines": [content],
                    "derived_type": derived_type,
                }
            elif current_row is not None:
                current_row["lines"].append(content)

        if current_row is not None:
            rows.append(current_row)

        return rows, current_date_text

    def _match_transaction_phrase(self, content: str) -> Optional[str]:
        lower = content.lower()
        for phrase in TRANSACTION_PHRASES:
            if lower.startswith(phrase):
                return phrase
        return None

    def _build_transaction(
        self,
        row: Dict,
        page_number: Optional[int],
        row_index: int,
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> Tuple[Optional[Dict], Optional[str]]:
        joined = " ".join(row["lines"])
        body_text = joined  # date prefix already stripped in _extract_rows

        money_matches = list(MONEY_TOKEN.finditer(body_text))
        if not money_matches:
            return None, "no_money_tokens"

        # Identify the amounts run (longest consecutive run of money tokens
        # separated only by whitespace; ties pick the last run).
        runs: List[List["re.Match[str]"]] = []
        current_run: List["re.Match[str]"] = []
        for match in money_matches:
            if not current_run:
                current_run = [match]
            elif body_text[current_run[-1].end():match.start()].strip() == "":
                current_run.append(match)
            else:
                runs.append(current_run)
                current_run = [match]
        if current_run:
            runs.append(current_run)

        if not runs:
            return None, "no_money_tokens"
        if all(len(run) == 1 for run in runs):
            amounts_run = runs[-1]
        else:
            amounts_run = max(runs, key=len)
        if len(amounts_run) > 3:
            amounts_run = amounts_run[-3:]

        amounts_start = amounts_run[0].start()
        amounts_end = amounts_run[-1].end()
        money_values = [self._parse_money(match.group(0)) for match in amounts_run]

        description = body_text[:amounts_start] + " " + body_text[amounts_end:]
        description = re.sub(r"\s{2,}", " ", description).strip(" ,;-.")

        if not re.search(r"[A-Za-z]", description):
            return None, "empty_description"

        derived_type = row.get("derived_type") or self._derive_transaction_type(description)
        if derived_type == "start_balance":
            return None, "start_balance"
        if derived_type == "end_balance":
            return None, "end_balance"

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
                paid_out = amount  # default
        elif len(money_values) == 1:
            amount = money_values[0]
            if derived_type in CREDIT_TYPES:
                paid_in = amount
            elif derived_type in DEBIT_TYPES:
                paid_out = amount
            else:
                return None, "no_direction_for_single_amount"
        else:
            return None, "no_money_tokens"

        transaction_date = self._parse_date(row.get("date_text"), start_date, end_date)
        if transaction_date is None:
            return None, "unparsable_date"

        amount_value = paid_in if paid_in > 0 else paid_out
        direction = "credit" if paid_in > 0 else "debit" if paid_out > 0 else "unknown"

        transaction = {
            "transaction_id": None,
            "transaction_date": transaction_date,
            "description_raw": description,
            "description_clean": description,
            "transaction_type": derived_type or "unknown",
            "derived_transaction_type": derived_type or "unknown",
            "amount": round(amount_value, 2),
            "debit": round(paid_out, 2),
            "credit": round(paid_in, 2),
            "paid_out": round(paid_out, 2),
            "paid_in": round(paid_in, 2),
            "balance_after": round(balance_after, 2) if balance_after is not None else None,
            "type": direction,
            "page_number": page_number or 0,
            "row_index": row_index,
            "source_line_start": None,
            "source_line_end": None,
            "parser_adapter": self.parser_adapter,
            "confidence": 0.9,
        }
        return transaction, None

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
        date_text: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> Optional[str]:
        if not date_text:
            return None
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
            if month >= start_month:
                return start_year
            return end_year
        if start_date:
            return int(start_date[:4])
        if end_date:
            return int(end_date[:4])
        return datetime.now().year

    # ---- duplicate handling --------------------------------------------

    def _deduplicate(
        self,
        transactions: List[Dict],
        debug: Dict,
    ) -> Tuple[List[Dict], Dict]:
        """Strong-key dedup including page_number + row_index, so legitimate
        same-day same-amount Barclays repeats (Apple.com, Greggs, Kalooki,
        National Lottery, internal transfers) are kept."""
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
        debug["duplicate_count"] = removed
        return deduped, debug
