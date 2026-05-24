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

# Description prefix -> derived transaction type. Explicit Barclays phrases
# are matched first; the bare rejection / refund / reversal keywords are a
# catch-all credit indicator for rows that don't start with a known phrase
# (e.g. the rejection block "Valerie Sherwo Ref: Paul ... Rejection 500.00").
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
    # Additional Barclays phrases (1.0.7 — debit-gap diagnostic widening).
    (re.compile(r"standing\s+order\s+to", re.IGNORECASE), "standing_order_to"),
    (re.compile(r"standing\s+order\s+from", re.IGNORECASE), "standing_order_from"),
    (re.compile(r"standing\s+order", re.IGNORECASE), "standing_order"),
    (re.compile(r"bacs\s+payment\s+to", re.IGNORECASE), "bacs_payment_to"),
    (re.compile(r"bacs\s+payment\s+from", re.IGNORECASE), "bacs_payment_from"),
    (re.compile(r"bacs\s+credit", re.IGNORECASE), "bacs_credit"),
    (re.compile(r"bacs\s+payment", re.IGNORECASE), "bacs_payment"),
    (re.compile(r"\batm\s+withdrawal\b", re.IGNORECASE), "atm_withdrawal"),
    (re.compile(r"\bcashpoint\b", re.IGNORECASE), "cashpoint"),
    (re.compile(r"non[\-\s]?sterling\s+(?:transaction\s+)?fee", re.IGNORECASE), "non_sterling_fee"),
    (re.compile(r"foreign\s+currency\s+fee", re.IGNORECASE), "foreign_currency_fee"),
    (re.compile(r"overdraft\s+fee", re.IGNORECASE), "overdraft_fee"),
    (re.compile(r"bank\s+charge", re.IGNORECASE), "bank_charge"),
    (re.compile(r"service\s+charge", re.IGNORECASE), "service_charge"),
    (re.compile(r"unpaid\s+(?:item|cheque)", re.IGNORECASE), "unpaid_item"),
    (re.compile(r"returned\s+cheque", re.IGNORECASE), "returned_cheque"),
    (re.compile(r"cheque\s+paid", re.IGNORECASE), "cheque_paid"),
    (re.compile(r"interest\s+charged", re.IGNORECASE), "interest_charged"),
    (re.compile(r"interest\s+paid", re.IGNORECASE), "interest_paid"),
    # Generic credit indicators — last so they only fire on rows without an
    # explicit phrase prefix.
    (re.compile(r"\brejection\b", re.IGNORECASE), "rejection"),
    (re.compile(r"\brefund\b", re.IGNORECASE), "refund"),
    (re.compile(r"\breversal\b", re.IGNORECASE), "reversal"),
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
    # Additional Barclays row-starting phrases (1.0.7).
    "standing order to",
    "standing order from",
    "standing order",
    "bacs payment to",
    "bacs payment from",
    "bacs payment",
    "bacs credit",
    "atm withdrawal",
    "cashpoint",
    "non-sterling transaction fee",
    "non sterling transaction fee",
    "non-sterling fee",
    "non sterling fee",
    "foreign currency fee",
    "overdraft fee",
    "bank charge",
    "service charge",
    "unpaid item",
    "unpaid cheque",
    "returned cheque",
    "cheque paid",
    "interest charged",
    "interest paid",
]

CREDIT_TYPES = {
    "received_from", "transfer_from", "bill_payment_from",
    "standing_order_from", "bacs_payment_from", "bacs_credit",
    "interest_paid",
    # rejected outbound payments come back as money in -> credit.
    "rejection", "refund", "reversal",
}
DEBIT_TYPES = {
    "card_payment", "card_purchase", "bill_payment", "bill_payment_to",
    "transfer_to", "direct_debit", "cash_machine_withdrawal",
    "standing_order_to", "standing_order",
    "bacs_payment_to", "bacs_payment",
    "atm_withdrawal", "cashpoint",
    "non_sterling_fee", "foreign_currency_fee", "overdraft_fee",
    "bank_charge", "service_charge",
    "unpaid_item", "returned_cheque", "cheque_paid",
    "interest_charged",
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
    adapter_version = "1.0.8"

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
        per_page_credit_sums: Dict[str, float] = {}
        per_page_debit_sums: Dict[str, float] = {}
        per_page_totals: Dict[str, Dict] = {}
        for stat in page_stats:
            page_number = stat["page_number"]
            page_txns = [t for t in transactions if t.get("page_number") == page_number]
            credit_sum = round(sum(float(t.get("paid_in", 0.0) or 0.0) for t in page_txns), 2)
            debit_sum = round(sum(float(t.get("paid_out", 0.0) or 0.0) for t in page_txns), 2)
            per_page.append(
                {
                    "page_number": page_number,
                    "credit_rows": sum(1 for t in page_txns if float(t.get("paid_in", 0.0) or 0.0) > 0),
                    "debit_rows": sum(1 for t in page_txns if float(t.get("paid_out", 0.0) or 0.0) > 0),
                    "credit_sum": credit_sum,
                    "debit_sum": debit_sum,
                }
            )
            per_page_credit_sums[str(page_number)] = credit_sum
            per_page_debit_sums[str(page_number)] = debit_sum
            per_page_totals[str(page_number)] = {
                "paid_in": credit_sum,
                "paid_out": debit_sum,
                "transaction_count": len(page_txns),
                "candidate_rows": stat.get("candidate_rows", 0),
                "rejected_rows": stat.get("rejected_rows", 0),
            }

        # Targeted diagnostic slices for inspecting reconciliation deltas.
        transactions_on_2023_05_26 = [
            tx for tx in transactions if tx.get("transaction_date") == "2023-05-26"
        ]
        transactions_matching_united_aluminium = [
            tx for tx in transactions
            if "united aluminium" in (tx.get("description_raw") or "").lower()
        ]
        transactions_matching_valerie = [
            tx for tx in transactions
            if "valerie" in (tx.get("description_raw") or "").lower()
        ]
        rejection_related_rows = [
            tx for tx in transactions
            if tx.get("derived_transaction_type") in ("rejection", "refund", "reversal")
            or any(
                keyword in (tx.get("description_raw") or "").lower()
                for keyword in (
                    "rejection", "payee bank response",
                    "unable to receive credits", "refund", "reversal",
                )
            )
        ]
        rows_near_missing_credit = [
            sample for sample in parse_debug["first_rejected"]
            if "united aluminium" in sample.get("text", "").lower()
            or sample.get("text", "").lower().startswith("rejection")
        ]
        ambiguous_amount_rows = [
            sample for sample in parse_debug["first_rejected"]
            if sample.get("reason") == "no_direction_for_single_amount"
        ]

        # ---- delta + subset-sum diagnostics for reconciliation gaps ----
        debit_delta = round(
            (statement_total_debits or 0.0) - calculated_total_debits, 2
        )
        credit_delta = round(
            (statement_total_credits or 0.0) - calculated_total_credits, 2
        )

        all_rejected = parse_debug["all_rejected"]
        orphan_lines = parse_debug["orphan_lines"]
        # Candidate pools: rejected rows whose derived_type was a credit/debit
        # phrase OR that are unclassified, PLUS orphan lines (lines with a
        # money token that never became a row — typically a missing debit).
        orphans_as_candidates = [
            {
                "page_number": orphan.get("page_number"),
                "row_index": None,
                "reason": "orphan_line_with_money",
                "derived_type": None,
                "text": orphan.get("text"),
                "amounts": orphan.get("amounts") or [],
            }
            for orphan in orphan_lines
        ]
        rejected_debit_pool = [
            sample for sample in all_rejected
            if sample.get("derived_type") in DEBIT_TYPES
            or sample.get("derived_type") in (None, "unknown")
        ] + orphans_as_candidates
        rejected_credit_pool = [
            sample for sample in all_rejected
            if sample.get("derived_type") in CREDIT_TYPES
        ]

        candidate_rows_totalling_debit_delta = (
            self._subsets_summing_to(rejected_debit_pool, debit_delta)
            if abs(debit_delta) > 0.005 else []
        )
        candidate_rows_totalling_credit_delta = (
            self._subsets_summing_to(rejected_credit_pool, credit_delta)
            if abs(credit_delta) > 0.005 else []
        )

        # Rows where a debit amount might have been mis-attributed to
        # balance_after — captured but with paid_out == 0 despite the row
        # context not being a credit.
        possible_debit_as_balance_rows = [
            tx for tx in transactions
            if float(tx.get("paid_in", 0.0) or 0.0) == 0.0
            and float(tx.get("paid_out", 0.0) or 0.0) == 0.0
            and tx.get("balance_after") not in (None, 0.0)
        ]

        # Per-page rejected debit/credit sums (first amount in each row).
        per_page_rejected_debit_sums: Dict[str, float] = {}
        per_page_rejected_credit_sums: Dict[str, float] = {}
        for sample in all_rejected:
            amounts = sample.get("amounts") or []
            if not amounts:
                continue
            amount = amounts[0]
            page_key = str(sample.get("page_number"))
            derived = sample.get("derived_type")
            if derived in DEBIT_TYPES or derived in (None, "unknown"):
                per_page_rejected_debit_sums[page_key] = round(
                    per_page_rejected_debit_sums.get(page_key, 0.0) + amount, 2
                )
            if derived in CREDIT_TYPES:
                per_page_rejected_credit_sums[page_key] = round(
                    per_page_rejected_credit_sums.get(page_key, 0.0) + amount, 2
                )

        rows_near_missing_debit = [
            sample for sample in all_rejected
            if any(
                kw in sample.get("text", "").lower()
                for kw in ("card payment", "direct debit", "transfer to", "cash machine", "card purchase", "bill payment to", "fee")
            )
        ][:25]

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
            "rejected_credit_candidates": parse_debug["missing_credit_examples"],
            "rejected_debit_candidates": parse_debug["missing_debit_examples"],
            "ambiguous_amount_rows": ambiguous_amount_rows,
            "rows_near_missing_credit": rows_near_missing_credit,
            "transactions_on_2023_05_26": transactions_on_2023_05_26,
            "transactions_matching_united_aluminium": transactions_matching_united_aluminium,
            "transactions_matching_valerie": transactions_matching_valerie,
            "rejection_related_rows": rejection_related_rows,
            "rows_rejected_by_reason": parse_debug["rows_rejected_by_reason"],
            "per_page": per_page,
            "per_page_credit_sums": per_page_credit_sums,
            "per_page_debit_sums": per_page_debit_sums,
            "calculated_total_credits": calculated_total_credits,
            "calculated_total_debits": calculated_total_debits,
            "calculated_total_credits_from_returned_transactions": calculated_total_credits,
            "calculated_total_debits_from_returned_transactions": calculated_total_debits,
            "duplicate_transaction_count": parse_debug["duplicate_count"],
            "dedupe_removed_rows": parse_debug["duplicate_rows"],
            "per_page_transaction_counts": per_page_counts,
            "first_transaction": transactions[0] if transactions else None,
            "last_transaction": transactions[-1] if transactions else None,
            "first_5_transactions": transactions[:5],
            "last_5_transactions": transactions[-5:],
            "first_rejected_candidate_rows": parse_debug["first_rejected"],
            "all_rejected_candidate_rows": parse_debug["all_rejected"],
            "debit_delta": debit_delta,
            "credit_delta": credit_delta,
            "per_page_totals": per_page_totals,
            "expected_vs_actual": {
                "statement_total_credits": statement_total_credits,
                "calculated_total_credits": calculated_total_credits,
                "credit_difference": credit_delta,
                "statement_total_debits": statement_total_debits,
                "calculated_total_debits": calculated_total_debits,
                "debit_difference": debit_delta,
            },
            "rows_near_missing_debit": rows_near_missing_debit,
            "possible_debit_as_balance_rows": possible_debit_as_balance_rows,
            "per_page_rejected_debit_sums": per_page_rejected_debit_sums,
            "per_page_rejected_credit_sums": per_page_rejected_credit_sums,
            "candidate_rows_totalling_debit_delta": candidate_rows_totalling_debit_delta,
            "candidate_rows_totalling_credit_delta": candidate_rows_totalling_credit_delta,
            "orphan_lines_with_money": orphan_lines,
            "bunched_redistributions": parse_debug.get("bunched_redistributions", []),
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
        all_rejected: List[Dict] = []
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
            "all_rejected": all_rejected,
            "missing_credit_examples": missing_credit_examples,
            "missing_debit_examples": missing_debit_examples,
            "rows_rejected_by_reason": {
                "no_amount": 0,
                "ambiguous_direction": 0,
                "header_or_footer": 0,
                "balance_marker": 0,
            },
            "duplicate_count": 0,
            "duplicate_rows": [],
            "orphan_lines": [],
            "bunched_redistributions": [],
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
                page_stats.append({
                    "page_number": page_number,
                    "transaction_rows": 0,
                    "candidate_rows": 0,
                    "rejected_rows": 0,
                })
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
                page_stats.append({
                    "page_number": page_number,
                    "transaction_rows": 0,
                    "candidate_rows": 0,
                    "rejected_rows": 0,
                })
                continue

            pages_considered.append(page_number)

            page_orphans: List[Dict] = []
            rows, carried_date_text = self._extract_rows(
                section_text, carried_date_text, page_orphans
            )
            redistributions = self._redistribute_bunched_amounts(rows)
            for move in redistributions:
                move["page_number"] = page_number
                debug["bunched_redistributions"].append(move)
            for orphan in page_orphans:
                orphan["page_number"] = page_number
                debug["orphan_lines"].append(orphan)
            page_rows: List[Dict] = []
            for row_index, row in enumerate(rows, start=1):
                debug["candidate_rows"] += 1
                transaction, rejection = self._build_transaction(
                    row, page_number, row_index, start_date, end_date
                )

                row_text = " ".join(row.get("lines") or [])
                if transaction is not None:
                    derived_type = transaction.get("derived_transaction_type")
                else:
                    derived_type = self._derive_transaction_type(row_text)
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

                    full_text = " ".join(row["lines"])
                    money_in_text = MONEY_TOKEN.findall(full_text)
                    parsed_amounts = [self._parse_money(token) for token in money_in_text]
                    sample = {
                        "page_number": page_number,
                        "row_index": row_index,
                        "reason": rejection or "unknown",
                        "derived_type": derived_type,
                        "text": full_text[:200],
                        "amounts": parsed_amounts,
                    }
                    debug["all_rejected"].append(sample)
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
            page_rejected_count = sum(
                1 for sample in all_rejected
                if sample.get("page_number") == page_number
            )
            page_stats.append(
                {
                    "page_number": page_number,
                    "transaction_rows": len(page_rows),
                    "candidate_rows": len(rows),
                    "rejected_rows": page_rejected_count,
                }
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
        orphan_lines: Optional[List[Dict]] = None,
    ) -> Tuple[List[Dict], Optional[str]]:
        """Build transaction rows from a section of statement text.

        A Barclays section commonly shows one date followed by several
        transactions (no date prefix per row). Rows are delimited two ways:

        1. A *transaction phrase* (``Card Payment to``, ``Received From``,
           ``Bill Payment From``, …) always starts a new row.
        2. When the current row's last line is *money-only* (e.g. a bare
           ``3,415.65`` on its own line) the row is considered closed; the
           next non-empty content line starts a new *anonymous* row even
           if it doesn't begin with a known phrase. This is how the
           rejection block ``Valerie Sherwo Ref: Paul ... Rejection 500.00``
           gets separated from the preceding ``Received From United
           Aluminium L Ref: United Aluminium 3,415.65`` row.

        The date carries forward across rows and across page boundaries.
        Returns ``(rows, last_carried_date_text)``.
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
                current_row = {
                    "date_text": current_date_text,
                    "lines": [content],
                }
                continue

            # Rejection block split: when a "Payee Bank Response" / "Account
            # Unable to Receive Credits" line arrives, finalise the current
            # row. If its last accumulated line is a payee-style reference
            # ("Valerie Sherwo Ref: Paul" — contains "Ref:" mid-text), pop
            # that line into the new rejection row so it isn't tacked onto
            # the previous credit's description.
            if self._is_rejection_split_trigger(content):
                new_first_line: Optional[str] = None
                if current_row is not None:
                    if self._last_line_is_rejection_payee(current_row):
                        new_first_line = current_row["lines"].pop()
                    rows.append(current_row)
                new_lines = [new_first_line, content] if new_first_line else [content]
                current_row = {
                    "date_text": current_date_text,
                    "lines": new_lines,
                }
                continue

            if current_row is not None:
                current_row["lines"].append(content)
            elif orphan_lines is not None and MONEY_TOKEN.search(content):
                # A line with a money token that did not start a row (no
                # phrase) and has no row to continue. These are the lines
                # most likely to hide a missing debit — the row-extractor
                # cannot see them otherwise.
                amounts = [
                    self._parse_money(token)
                    for token in MONEY_TOKEN.findall(content)
                ]
                orphan_lines.append(
                    {
                        "text": content[:200],
                        "amounts": amounts,
                        "carried_date_text": current_date_text,
                    }
                )

        if current_row is not None:
            rows.append(current_row)

        return rows, current_date_text

    def _redistribute_bunched_amounts(self, rows: List[Dict]) -> List[Dict]:
        """Repair pdfplumber-bunched amounts.

        pdfplumber's text extraction sometimes carries the amount of one
        transaction past the next transaction's description, so two
        consecutive same-phrase rows end up with the first row carrying
        zero money tokens and the second carrying both rows' amounts.
        Example (from the real Statement 18-aug-23 ac 13604152.PDF):

            Bill Payment to Daniel Sherwood
            Ref: Dad
            Bill Payment to Charlotte Latchfor
            Ref: PS
            50.00      <- actually Daniel's amount
            75.00      <- actually Charlotte's amount

        Heuristic: when row N has no money tokens and row N+1 has the same
        transaction phrase and 2+ money tokens, the first money token in
        row N+1 belongs to row N — move it back. The same-phrase guard
        keeps this from disturbing legitimate amount+balance pairs (which
        belong to a single row, not two).

        Returns a list of {borrowed_amount, from_row_index, to_row_index}
        records so the redistribution is auditable from parser_debug.
        """
        moves: List[Dict] = []
        if not rows:
            return moves

        def collect_money(row):
            collected = []
            for line_index, line in enumerate(row.get("lines", [])):
                for match in MONEY_TOKEN.finditer(line):
                    collected.append((line_index, match.group(0), match.start(), match.end()))
            return collected

        def row_phrase(row):
            lines = row.get("lines") or []
            if not lines:
                return None
            return self._match_transaction_phrase(lines[0])

        changed = True
        while changed:
            changed = False
            for i in range(len(rows) - 1):
                row = rows[i]
                next_row = rows[i + 1]
                if collect_money(row):
                    continue
                next_money = collect_money(next_row)
                if len(next_money) < 2:
                    continue
                row_p = row_phrase(row)
                next_p = row_phrase(next_row)
                if row_p is None or row_p != next_p:
                    continue
                line_index, token, start, end = next_money[0]
                line = next_row["lines"][line_index]
                new_line = (line[:start] + line[end:]).strip()
                if new_line:
                    next_row["lines"][line_index] = new_line
                else:
                    next_row["lines"].pop(line_index)
                row["lines"].append(token)
                moves.append(
                    {
                        "borrowed_amount": token,
                        "from_row_index": i + 1,
                        "to_row_index": i,
                        "phrase": row_p,
                    }
                )
                changed = True
                break  # restart the scan after a mutation
        return moves

    def _is_rejection_split_trigger(self, content: str) -> bool:
        lower = content.lower().strip()
        return (
            lower.startswith("payee bank response")
            or lower.startswith("account unable to receive credits")
        )

    def _last_line_is_rejection_payee(self, row: Dict) -> bool:
        """Heuristic: a row's last line belongs to the *next* rejection block
        (and should be popped) when it looks like a payee reference — contains
        ``Ref:`` somewhere mid-text but does not start with ``Ref:``, is not a
        money-only line, and is not the phrase line that started the row.
        """
        lines = row.get("lines") or []
        if len(lines) < 2:  # never pop the only line (the phrase line)
            return False
        last_line = lines[-1].strip()
        if not last_line:
            return False
        lower = last_line.lower()
        if lower.startswith("ref:"):
            return False
        if self._match_transaction_phrase(last_line) is not None:
            return False
        if MONEY_TOKEN.search(last_line):
            remainder = MONEY_TOKEN.sub("", last_line)
            remainder = re.sub(r"\s+", "", remainder).strip()
            if remainder == "":
                return False  # money-only line
        return "ref:" in lower

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

        derived_type = self._derive_transaction_type(description)
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
        removed_rows: List[Dict] = []
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
                removed_rows.append(tx)
                continue
            seen.add(key)
            deduped.append(tx)
        debug["duplicate_count"] = len(removed_rows)
        debug["duplicate_rows"] = removed_rows
        return deduped, debug

    @staticmethod
    def _subsets_summing_to(
        candidates: List[Dict],
        target: float,
        max_results: int = 3,
        max_n: int = 18,
        tolerance: float = 0.01,
    ) -> List[List[Dict]]:
        """Find subsets of ``candidates`` whose first amount sums to
        ``target``. Bounded brute-force (2**max_n) so it cannot explode on
        long rejection lists; returns up to ``max_results`` subsets."""
        # Only consider candidates that contributed a sensible amount.
        pool = [
            (round(c["amounts"][0] * 100), c)
            for c in candidates
            if c.get("amounts")
        ]
        if not pool or abs(target) < tolerance:
            return []
        pool = pool[:max_n]
        target_cents = round(abs(target) * 100)
        tolerance_cents = max(1, round(tolerance * 100))
        results: List[List[Dict]] = []
        n = len(pool)
        for mask in range(1, 1 << n):
            total = 0
            for i in range(n):
                if mask & (1 << i):
                    total += pool[i][0]
            if abs(total - target_cents) <= tolerance_cents:
                results.append(
                    [pool[i][1] for i in range(n) if mask & (1 << i)]
                )
                if len(results) >= max_results:
                    break
        return results
