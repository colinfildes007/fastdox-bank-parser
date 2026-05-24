import io
import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def build_barclays_sample_pdf():
    """Synthetic Barclays Current Account statement reproducing the layout of
    Statement 18-aug-23 ac 13604152.PDF: an 'At a glance' summary, a multi-page
    transaction table with Money out / Money in / Balance columns, and an
    informational page that must not be parsed as transactions.

    Six real transactions reconcile to:
        opening 7.20 + 51025.30 - 51032.04 = 0.46
    Plus start_balance and end_balance markers (skipped from totals).
    """
    import fitz

    page_1 = [
        "Barclays Bank UK PLC",
        "Current account statement",
        "Sort Code 20-55-59  Account Number 13604152",
        "Statement period: 19 May - 18 Aug 2023",
        "",
        "At a glance",
        "Start balance       £7.20",
        "Money in            £51,025.30",
        "Money out           £51,032.04",
        "End balance         £0.46",
        "",
        "Your transactions",
        "Date    Description                                                  Money out    Money in    Balance",
        "19 May  Start balance                                                                          7.20",
        "20 May  Received From United Aluminium L Ref: United Aluminium                    50,000.00   50,007.20",
        "22 May  Transfer to Sort Code 20-55-59 Account 93180263 Ref: Mobile-Channel  50,000.00         7.20",
    ]
    page_2 = [
        "Date    Description                                                  Money out    Money in    Balance",
        "01 Jun  Received From Apple Inc Ref: Refund                                       1,000.00    1,007.20",
        "05 Jun  Direct Debit to O2 Ref: Ged63493634                          1,000.00                 7.20",
        "18 Jul  Received From Greggs Ref: Refund                                          25.30       32.50",
        "17 Aug  Cash Machine Withdrawal at Notemachine Shell                 32.04                    0.46",
        "18 Aug  End balance                                                                           0.46",
    ]
    page_3 = [
        "Important information about your account",
        "Your benefits at a glance",
        "How to contact us",
        "If you change your mind",
        "How to make a complaint",
    ]

    doc = fitz.open()
    for lines in (page_1, page_2, page_3):
        page = doc.new_page(width=720, height=80 + len(lines) * 12 + 60)
        y = 50
        for line in lines:
            page.insert_text((40, y), line, fontsize=8)
            y += 12
    return doc.write()


class BarclaysFixtureTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.expected = json.loads(
            Path("tests/fixtures/barclays/expected_001.json").read_text()
        )

    def _post_pdf(self, pdf_bytes):
        files = {"file": ("statement_001.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
        return self.client.post(
            "/extract-upload",
            data={"document_id": "fixture-doc"},
            files=files,
        )

    def test_barclays_fixture_reconciles(self):
        response = self._post_pdf(build_barclays_sample_pdf())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        expected = self.expected

        # --- parser identity ---
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["detected_bank"], expected["bank_name"])
        self.assertEqual(body["parser_adapter"], expected["parser_adapter"])
        self.assertEqual(body["statement"]["bank_name"], expected["bank_name"])

        # --- summary header ---
        statement = body["statement"]
        self.assertEqual(statement["statement_start_date"], expected["statement_start_date"])
        self.assertEqual(statement["statement_end_date"], expected["statement_end_date"])
        self.assertEqual(statement["opening_balance"], expected["opening_balance"])
        self.assertEqual(statement["closing_balance"], expected["closing_balance"])
        self.assertEqual(statement["total_credits"], expected["statement_total_credits"])
        self.assertEqual(statement["total_debits"], expected["statement_total_debits"])

        # --- reconciliation ---
        recon = body["reconciliation"]
        self.assertEqual(recon["status"], expected["reconciliation_status"])
        self.assertEqual(recon["calculated_total_credits"], expected["statement_total_credits"])
        self.assertEqual(recon["calculated_total_debits"], expected["statement_total_debits"])

        # opening + credits - debits == closing
        self.assertAlmostEqual(
            round(
                expected["opening_balance"]
                + expected["statement_total_credits"]
                - expected["statement_total_debits"],
                2,
            ),
            expected["closing_balance"],
            places=2,
        )

        # --- parser_debug diagnostics ---
        debug = body["parser_debug"]
        self.assertEqual(debug["adapter_selected"], "barclays_family_v1")
        self.assertEqual(debug["parser_adapter"], "barclays_family_v1")
        self.assertTrue(debug["summary_block_found"])
        self.assertTrue(debug["statement_period_found"])
        self.assertTrue(debug["opening_balance_found"])
        self.assertTrue(debug["closing_balance_found"])
        self.assertTrue(debug["statement_total_credits_found"])
        self.assertTrue(debug["statement_total_debits_found"])
        self.assertGreater(debug["transactions_returned"], 0)
        self.assertEqual(debug["duplicate_transaction_count"], 0)

        # transactions are parsed on pages 1 and 2 — never on the
        # informational page 3.
        self.assertIn(1, debug["transaction_pages_detected"])
        self.assertIn(2, debug["transaction_pages_detected"])
        self.assertNotIn(3, debug["transaction_pages_detected"])

        # --- every transaction carries the required fields ---
        required_fields = (
            "transaction_date", "description_raw", "paid_in", "paid_out",
            "balance_after", "derived_transaction_type", "page_number",
            "row_index", "parser_adapter",
        )
        for tx in body["transactions"]:
            for field in required_fields:
                self.assertIn(field, tx)
            self.assertEqual(tx["parser_adapter"], "barclays_family_v1")

        # start/end balance markers must not appear as transactions.
        for tx in body["transactions"]:
            self.assertNotEqual(tx["derived_transaction_type"], "start_balance")
            self.assertNotEqual(tx["derived_transaction_type"], "end_balance")

        # --- Defect #2 regression: amounts MUST be present on returned rows ---
        # Base44 sums paid_in/paid_out from transactions[]. If the parser
        # returns rows with paid_in == 0 and paid_out == 0 the reconciliation
        # silently goes to 0/0. Guard that explicitly.
        self.assertTrue(
            any(tx["paid_in"] > 0 for tx in body["transactions"]),
            "every transaction had paid_in == 0",
        )
        self.assertTrue(
            any(tx["paid_out"] > 0 for tx in body["transactions"]),
            "every transaction had paid_out == 0",
        )
        # The sum of paid_in / paid_out across the returned rows must equal
        # the calculated totals (this is what Base44 recomputes).
        self.assertEqual(
            round(sum(tx["paid_in"] for tx in body["transactions"]), 2),
            expected["statement_total_credits"],
        )
        self.assertEqual(
            round(sum(tx["paid_out"] for tx in body["transactions"]), 2),
            expected["statement_total_debits"],
        )

        # --- rich parser_debug: the fields Base44 asked the parser to surface ---
        for field in (
            "transaction_pages_considered",
            "transaction_pages_skipped",
            "candidate_rows_found",
            "non_transaction_rows_discarded",
            "header_rows_discarded",
            "start_balance_marker_found",
            "end_balance_marker_found",
            "rows_with_paid_in",
            "rows_with_paid_out",
            "rows_with_balance_after",
            "calculated_total_credits_from_returned_transactions",
            "calculated_total_debits_from_returned_transactions",
            "first_5_transactions",
            "last_5_transactions",
            "first_rejected_candidate_rows",
        ):
            self.assertIn(field, debug)

        # the synthetic statement contains a start_balance and end_balance row;
        # both must be detected as markers, not returned as transactions.
        self.assertTrue(debug["start_balance_marker_found"])
        self.assertTrue(debug["end_balance_marker_found"])
        self.assertEqual(
            debug["calculated_total_credits_from_returned_transactions"],
            expected["statement_total_credits"],
        )
        self.assertEqual(
            debug["calculated_total_debits_from_returned_transactions"],
            expected["statement_total_debits"],
        )

        # known transactions are present with the right derived type and amount.
        by_desc_substring = {
            "United Aluminium": ("received_from", 50000.00, 0.0),
            "Mobile-Channel": ("transfer_to", 0.0, 50000.00),
            "Apple Inc": ("received_from", 1000.00, 0.0),
            "Direct Debit to O2": ("direct_debit", 0.0, 1000.00),
            "Greggs": ("received_from", 25.30, 0.0),
            "Cash Machine Withdrawal": ("cash_machine_withdrawal", 0.0, 32.04),
        }
        for marker, (expected_type, expected_in, expected_out) in by_desc_substring.items():
            match = next((tx for tx in body["transactions"] if marker in tx["description_raw"]), None)
            self.assertIsNotNone(match, f"missing transaction for marker {marker!r}")
            self.assertEqual(match["derived_transaction_type"], expected_type)
            self.assertEqual(match["paid_in"], expected_in)
            self.assertEqual(match["paid_out"], expected_out)

    def test_health_lists_barclays_adapter(self):
        body = self.client.get("/health").json()
        self.assertIn("barclays_family_v1", body["available_adapters"])
        self.assertEqual(body["adapter_versions"]["barclays_family_v1"], "1.0.5")

    def test_barclays_grouped_dates_and_credit_phrases(self):
        """Real Barclays statements group several transactions under a single
        date line, with no date prefix per row. The date must carry forward
        until the next date appears, and each transaction is delimited by its
        leading phrase (Card Payment to, Received From, Bill Payment From,
        Transfer From, Direct Debit to, Transfer to, ...).

        This guards against the Defect #2 follow-up — a single date row was
        being collapsed into one mega-row, and Bill Payment From / Transfer
        From / Received From were not being recognised as credits.
        """
        import fitz

        lines = [
            "Barclays Bank UK PLC",
            "Your Barclays Bank Account statement",
            "Current account statement",
            "Mr Paul Michael Sherwood",
            "Sort Code 20-55-59  Account Number 13604152",
            "IBAN GB56 BUKB 2055 5913 6041 52",
            "BIC BUKBGB22",
            "Statement period: 30 May - 01 Jun 2023",
            "At a glance",
            "Start balance       £100.00",
            "Money in            £5,215.65",
            "Money out           £5,056.45",
            "End balance         £259.20",
            "Your transactions",
            "Date Description Money out Money in Balance",
            # grouped under "30 May" — no per-row date prefix
            "30 May",
            "Card Payment to Greggs @ Mfg",
            "Green On 28 May",
            "6.45",
            "Bill Payment From Kalooki Off Cour F",
            "Ref: PS379",
            "1,600.00",
            "Transfer From Sort Code 20-55-59",
            "Account 93180263",
            "Ref: Mobile-Channel",
            "200.00",
            # next date — carries forward
            "01 Jun",
            "Received From United Aluminium L",
            "Ref: United Aluminium",
            "3,415.65",
            "Transfer to Sort Code 20-55-59",
            "Account 93180263",
            "Ref: Mobile-Channel",
            "5,000.00",
            "Direct Debit to O2",
            "Ref: Ged63493634",
            "50.00",
        ]
        info_page = ["Important information about your account"]

        doc = fitz.open()
        page = doc.new_page(width=720, height=80 + len(lines) * 12 + 60)
        y = 50
        for line in lines:
            page.insert_text((40, y), line, fontsize=8)
            y += 12
        page = doc.new_page(width=720, height=80 + len(info_page) * 12 + 60)
        y = 50
        for line in info_page:
            page.insert_text((40, y), line, fontsize=8)
            y += 12
        pdf_bytes = doc.write()

        response = self._post_pdf(pdf_bytes)
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["detected_bank"], "Barclays")
        self.assertEqual(body["parser_adapter"], "barclays_family_v1")
        self.assertEqual(body["statement"]["statement_start_date"], "2023-05-30")
        self.assertEqual(body["statement"]["statement_end_date"], "2023-06-01")
        self.assertEqual(body["statement"]["opening_balance"], 100.0)
        self.assertEqual(body["statement"]["closing_balance"], 259.2)
        self.assertEqual(body["statement"]["total_credits"], 5215.65)
        self.assertEqual(body["statement"]["total_debits"], 5056.45)

        recon = body["reconciliation"]
        self.assertEqual(recon["status"], "matched")
        self.assertEqual(recon["calculated_total_credits"], 5215.65)
        self.assertEqual(recon["calculated_total_debits"], 5056.45)

        debug = body["parser_debug"]
        self.assertIn("deterministic_run_id", debug)
        self.assertIn("page_processing_order", debug)
        self.assertEqual(debug["adapter_version"], "1.0.5")
        self.assertEqual(debug["credit_rows_returned"], 3)
        self.assertEqual(debug["debit_rows_returned"], 3)
        self.assertEqual(debug["credit_amount_sum"], 5215.65)
        self.assertEqual(debug["debit_amount_sum"], 5056.45)
        self.assertGreaterEqual(debug["credit_candidate_rows_found"], 3)
        self.assertGreaterEqual(debug["debit_candidate_rows_found"], 3)
        self.assertIn("rows_rejected_by_reason", debug)
        self.assertIn("per_page", debug)

        # Account metadata
        statement = body["statement"]
        self.assertEqual(statement["sort_code"], "20-55-59")
        self.assertEqual(statement["account_number"], "13604152")
        self.assertEqual(statement["iban"], "GB56 BUKB 2055 5913 6041 52")
        self.assertEqual(statement["swift_bic"], "BUKBGB22")
        self.assertIn("Paul Michael Sherwood", statement["account_holder"] or "")

        # The six transactions are present with the right derived types.
        by_marker = {
            "Greggs": ("card_payment", "debit", 6.45, "2023-05-30"),
            "Kalooki Off Cour": ("bill_payment_from", "credit", 1600.0, "2023-05-30"),
            "Transfer From Sort Code": ("transfer_from", "credit", 200.0, "2023-05-30"),
            "United Aluminium": ("received_from", "credit", 3415.65, "2023-06-01"),
            "Transfer to Sort Code": ("transfer_to", "debit", 5000.0, "2023-06-01"),
            "O2": ("direct_debit", "debit", 50.0, "2023-06-01"),
        }
        for marker, (expected_type, direction, amount, date) in by_marker.items():
            tx = next(
                (t for t in body["transactions"] if marker in t["description_raw"]),
                None,
            )
            self.assertIsNotNone(tx, f"missing transaction for marker {marker!r}")
            self.assertEqual(tx["derived_transaction_type"], expected_type)
            self.assertEqual(tx["transaction_date"], date)
            if direction == "credit":
                self.assertEqual(tx["paid_in"], amount)
                self.assertEqual(tx["paid_out"], 0.0)
            else:
                self.assertEqual(tx["paid_out"], amount)
                self.assertEqual(tx["paid_in"], 0.0)

        # Determinism: parsing the same PDF twice gives an identical result.
        body_again = self._post_pdf(pdf_bytes).json()
        self.assertEqual(body_again["transaction_count"], body["transaction_count"])
        self.assertEqual(
            body_again["parser_debug"]["deterministic_run_id"],
            debug["deterministic_run_id"],
        )
        self.assertEqual(body_again["transactions"], body["transactions"])

    def test_barclays_rejection_block_does_not_merge_with_previous_credit(self):
        """A 'Received From' credit followed by a rejection/refund block must
        produce TWO separate credit rows, not one merged row that loses the
        first amount. This guards the £3,415.65 missing-credit regression
        reported on the real Statement 18-aug-23 ac 13604152.PDF.
        """
        import fitz

        lines = [
            "Barclays Bank UK PLC",
            "Your Barclays Bank Account statement",
            "Current account statement",
            "Sort Code 20-55-59  Account Number 13604152",
            "Statement period: 26 May - 27 May 2023",
            "At a glance",
            "Start balance       £100.00",
            "Money in            £3,915.65",
            "Money out           £0.00",
            "End balance         £4,015.65",
            "Your transactions",
            "26 May",
            "Received From United Aluminium L",
            "Ref: United Aluminium",
            "3,415.65",
            # Rejection block — must NOT merge into the Received From row.
            "Valerie Sherwo Ref: Paul",
            "Payee Bank Response: Account Unable to Receive Credits.",
            "Rejection",
            "500.00 816.85",
        ]
        info_page = ["Important information about your account"]

        doc = fitz.open()
        page = doc.new_page(width=720, height=80 + len(lines) * 12 + 60)
        y = 50
        for line in lines:
            page.insert_text((40, y), line, fontsize=8)
            y += 12
        page = doc.new_page(width=720, height=80 + len(info_page) * 12 + 60)
        y = 50
        for line in info_page:
            page.insert_text((40, y), line, fontsize=8)
            y += 12
        pdf_bytes = doc.write()

        response = self._post_pdf(pdf_bytes)
        self.assertEqual(response.status_code, 200)
        body = response.json()

        debug = body["parser_debug"]
        united = next(
            (tx for tx in body["transactions"]
             if "united aluminium" in tx["description_raw"].lower()),
            None,
        )
        self.assertIsNotNone(united, "Received From United Aluminium row missing")
        self.assertEqual(united["derived_transaction_type"], "received_from")
        self.assertEqual(united["paid_in"], 3415.65)
        self.assertEqual(united["paid_out"], 0.0)
        self.assertEqual(united["transaction_date"], "2023-05-26")

        rejection = next(
            (tx for tx in body["transactions"]
             if "rejection" in tx["description_raw"].lower()),
            None,
        )
        self.assertIsNotNone(rejection, "Rejection refund row missing")
        self.assertEqual(rejection["derived_transaction_type"], "rejection")
        self.assertEqual(rejection["paid_in"], 500.0)
        self.assertEqual(rejection["paid_out"], 0.0)
        self.assertEqual(rejection["balance_after"], 816.85)
        # the rejection block inherits the carried-forward date.
        self.assertEqual(rejection["transaction_date"], "2023-05-26")

        # The targeted diagnostic slices exist and contain both rows.
        self.assertIn("transactions_on_2023_05_26", debug)
        self.assertIn("transactions_matching_united_aluminium", debug)
        self.assertEqual(len(debug["transactions_matching_united_aluminium"]), 1)
        dates = {tx["transaction_date"] for tx in debug["transactions_on_2023_05_26"]}
        self.assertEqual(dates, {"2023-05-26"})
        self.assertGreaterEqual(len(debug["transactions_on_2023_05_26"]), 2)

        # per_page_credit_sums / per_page_debit_sums are reported.
        self.assertIn("per_page_credit_sums", debug)
        self.assertIn("per_page_debit_sums", debug)

    def test_barclays_rejection_block_amount_in_middle_layout(self):
        """The real Statement 18-aug-23 ac 13604152.PDF emits this row order:
            Received From United Aluminium L 3,415.65
            Ref: United Aluminium
            Valerie Sherwo Ref: Paul
            Payee Bank Response: Account Unable to Receive Credits.
            Rejection
            500.00 816.85

        The amount sits BEFORE the Ref: line (not after), so the previous
        money-only-line rule never fired and the whole rejection block
        merged into the United Aluminium row. The 'Payee Bank Response'
        split trigger must close the United Aluminium row cleanly, and
        the popped 'Valerie Sherwo Ref: Paul' line must land in the
        rejection row.
        """
        import fitz

        lines = [
            "Barclays Bank UK PLC",
            "Your Barclays Bank Account statement",
            "Current account statement",
            "Sort Code 20-55-59  Account Number 13604152",
            "Statement period: 26 May - 27 May 2023",
            "At a glance",
            "Start balance       £100.00",
            "Money in            £3,915.65",
            "Money out           £0.00",
            "End balance         £4,015.65",
            "Your transactions",
            "26 May",
            "Received From United Aluminium L 3,415.65",
            "Ref: United Aluminium",
            "Valerie Sherwo Ref: Paul",
            "Payee Bank Response: Account Unable to Receive Credits.",
            "Rejection",
            "500.00 816.85",
        ]
        info = ["Important information about your account"]

        doc = fitz.open()
        for block in (lines, info):
            page = doc.new_page(width=720, height=80 + len(block) * 12 + 60)
            y = 50
            for line in block:
                page.insert_text((40, y), line, fontsize=8)
                y += 12

        body = self._post_pdf(doc.write()).json()

        united = next(
            (tx for tx in body["transactions"]
             if "united aluminium" in tx["description_raw"].lower()),
            None,
        )
        self.assertIsNotNone(united)
        self.assertEqual(united["derived_transaction_type"], "received_from")
        self.assertEqual(united["paid_in"], 3415.65)
        self.assertEqual(united["paid_out"], 0.0)
        # The United Aluminium row keeps Ref: United Aluminium but the
        # rejection's Valerie payee line must NOT leak into its description.
        self.assertNotIn("valerie", united["description_raw"].lower())
        self.assertNotIn("payee bank response", united["description_raw"].lower())

        rejection = next(
            (tx for tx in body["transactions"]
             if "rejection" in tx["description_raw"].lower()),
            None,
        )
        self.assertIsNotNone(rejection)
        self.assertEqual(rejection["derived_transaction_type"], "rejection")
        self.assertEqual(rejection["paid_in"], 500.0)
        self.assertEqual(rejection["balance_after"], 816.85)
        # The Valerie payee was popped INTO the rejection row.
        self.assertIn("valerie", rejection["description_raw"].lower())

        debug = body["parser_debug"]
        self.assertIn("transactions_matching_valerie", debug)
        self.assertIn("rejection_related_rows", debug)
        self.assertEqual(len(debug["transactions_matching_united_aluminium"]), 1)
        self.assertEqual(len(debug["transactions_matching_valerie"]), 1)
        self.assertGreaterEqual(len(debug["rejection_related_rows"]), 1)

    def test_barclays_detected_even_with_noisy_other_bank_mentions(self):
        """A bare 'santander' or 'lloyds' mention buried in a Barclays
        statement (e.g. a merchant description or a footer reference) must
        not flip detection away from Barclays."""
        import fitz

        page_1 = [
            "Barclays Bank UK PLC",
            "Your Barclays Bank Account statement",
            "Current account statement",
            "Sort Code 20-55-59  Account Number 13604152",
            "Statement period: 19 May - 18 Aug 2023",
            "",
            "At a glance",
            "Start balance       £7.20",
            "Money in            £51,025.30",
            "Money out           £51,032.04",
            "End balance         £0.46",
            "",
            "Your transactions",
            "Date    Description                                          Money out    Money in    Balance",
            # noisy: a merchant whose name contains "Santander", plus a stray
            # "lloyds" mention in a transfer reference.
            "20 May  Card Payment to Santander ATM On 20 May                                          7.20      0.00",
            "21 May  Transfer to Lloyds Pharmacy Ref: Mobile-Channel       3.50                              0.00",
        ]

        doc = fitz.open()
        page = doc.new_page(width=720, height=80 + len(page_1) * 12 + 60)
        y = 50
        for line in page_1:
            page.insert_text((40, y), line, fontsize=8)
            y += 12
        pdf_bytes = doc.write()

        response = self._post_pdf(pdf_bytes)
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["detected_bank"], "Barclays")
        self.assertEqual(body["parser_adapter"], "barclays_family_v1")
        self.assertGreaterEqual(body["bank_detection_confidence"], 0.95)

        debug = body["parser_debug"]
        self.assertIn("bank_detection_candidates", debug)
        candidates = debug["bank_detection_candidates"]
        self.assertEqual(debug["selected_bank"], "Barclays")
        self.assertEqual(debug["selected_adapter"], "barclays_family_v1")
        self.assertIn("Barclays", candidates)
        self.assertIn("Santander", candidates)
        self.assertIn("Lloyds Bank", candidates)
        # Barclays score must beat the runner-up by a comfortable margin.
        barclays_score = candidates["Barclays"]["score"]
        other_scores = [
            data["score"] for bank, data in candidates.items() if bank != "Barclays"
        ]
        self.assertGreater(barclays_score, max(other_scores) + 20)


if __name__ == "__main__":
    unittest.main()
