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
        self.assertEqual(body["adapter_versions"]["barclays_family_v1"], "1.0.0")


if __name__ == "__main__":
    unittest.main()
