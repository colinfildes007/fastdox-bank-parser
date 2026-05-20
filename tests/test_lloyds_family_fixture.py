import io
import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def build_santander_statement_pdf():
    import fitz

    def _ordinal(day):
        if 10 <= day % 100 <= 20:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        return f"{day}{suffix}"

    header = (
        "Santander UK plc\n"
        "Account Summary\n"
        "Date Description Debits Credits Balance\n"
    )

    debit_values = [64.00] * 127 + [145.18]
    credit_values = [63.00] * 127 + [91.75]
    balance = 98.10
    transaction_lines = []

    for i in range(256):
        day = 28 - (i % 28)
        if i % 2 == 0:
            amount = debit_values[i // 2]
            description = f"Transfer to merchant {i + 1}"
            previous_balance = balance + amount
        else:
            amount = credit_values[i // 2]
            description = f"Bank giro credit {i + 1}"
            previous_balance = balance - amount

        transaction_lines.append(
            f"{_ordinal(day)} Jan 25 {description} £{amount:.2f} £{balance:.2f}"
        )
        balance = previous_balance

    pages = []
    chunk_size = 20
    for start in range(0, len(transaction_lines), chunk_size):
        chunk = transaction_lines[start : start + chunk_size]
        page_text = "\n".join(chunk)
        if start == 0:
            page_text = header + page_text
        pages.append(page_text)

    pages[-1] += "\nTotal debits £8273.18\nTotal credit £8092.75\nClosing Balance £98.10\n"
    doc = fitz.open()
    for page_text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), page_text, fontsize=10)
    return doc.write()


def build_lloyds_blank_format_pdf():
    """A Lloyds 'Classic' statement in the labelled-block layout.

    Empty money cells are rendered as the literal token 'blank.' (sometimes
    inline with the label), amounts use comma grouping, and the table spans
    three pages with a repeated column-header row on page 2.
    """
    import fitz

    page_1 = "\n".join(
        [
            "Lloyds Bank plc",
            "Classic statement",
            "Account name John Smith",
            "Sort code 30-00-00",
            "Account number 12345678",
            "Statement period 01 Jan 26 to 31 Jan 26",
            "Money In £6,537.00",
            "Money Out £6,437.60",
            "Balance on 01 January 2026 £173.00",
            "Balance on 31 January 2026 £272.40",
            "Your Transactions",
            "Date", "01 Jan 26",
            "Description", "AIDAN SHERWOOD",
            "Type", "FPI",
            "Money In (£)", "1,200.00",
            "Money Out (£) blank.",
            "Balance (£)", "1,373.00",
            "Date", "02 Jan 26",
            "Description", "ROCHDALE MBC",
            "Type", "DD",
            "Money In (£) blank.",
            "Money Out (£)", "15.40",
            "Balance (£)", "1,357.60",
            "Date", "03 Jan 26",
            "Description", "TRANSFER IN",
            "Type", "FPI",
            "Money In (£)", "2,000.00",
            "Money Out (£) blank.",
            "Balance (£)", "3,357.60",
        ]
    )

    page_2 = "\n".join(
        [
            "Date Description Type Money In (£) Money Out (£) Balance (£)",
            "Date", "04 Jan 26",
            "Description", "SUPERMARKET",
            "Type", "DD",
            "Money In (£) blank.",
            "Money Out (£)", "1,500.00",
            "Balance (£)", "1,857.60",
            "Date 05 Jan 26",
            "Description", "BONUS PAYMENT",
            "Type", "CR",
            "Money In (£)", "1,000.00",
            "Money Out (£) blank.",
            "Balance (£)", "2,857.60",
            "Date", "06 Jan 26",
            "Description", "UTILITY PROVIDER",
            "Type", "DD",
            "Money In (£) blank.",
            "Money Out (£)", "800.00",
            "Balance (£)", "2,057.60",
            "Page 2 of 3",
        ]
    )

    page_3 = "\n".join(
        [
            "Date", "07 Jan 26",
            "Description", "INSURANCE REFUND",
            "Type", "FPI",
            "Money In (£)", "2,337.00",
            "Money Out (£) blank.",
            "Balance (£)", "4,394.60",
            "Date", "08 Jan 26",
            "Description", "RENT PAYMENT",
            "Type", "DD",
            "Money In (£) blank.",
            "Money Out (£)", "4,122.20",
            "Balance (£)", "272.40",
            "Transaction types",
            "DD Direct Debit",
            "FPI Faster Payment In",
            "CR Credit",
        ]
    )

    doc = fitz.open()
    for page_text in (page_1, page_2, page_3):
        page = doc.new_page()
        page.insert_text((54, 54), page_text, fontsize=9)
    return doc.write()


class LloydsFamilyFixtureTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.fixture_path = Path("tests/fixtures/lloyds_family/sample_statement.pdf")
        self.expected = json.loads(
            Path("tests/fixtures/lloyds_family/expected_output.json").read_text()
        )
        self.halifax_fixture_path = Path("tests/fixtures/lloyds_family/sample_statement_halifax.pdf")
        self.expected_halifax = json.loads(
            Path("tests/fixtures/lloyds_family/expected_output_halifax.json").read_text()
        )
        self.lloyds_2026_fixture_path = Path("tests/fixtures/lloyds_family/Statement_2026_lloyds.pdf")
        self.expected_2026 = json.loads(
            Path("tests/fixtures/lloyds_family/expected_output_2026_lloyds.json").read_text()
        )

    def _post_pdf(self, pdf_bytes):
        files = {
            "file": ("sample_lloyds_statement.pdf", io.BytesIO(pdf_bytes), "application/pdf")
        }
        return self.client.post("/extract-upload", data={"document_id": "fixture-doc"}, files=files)

    def test_lloyds_family_bank_detection(self):
        pdf_bytes = self.fixture_path.read_bytes()
        response = self._post_pdf(pdf_bytes)
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertIn(body["detected_bank"], ["Lloyds Bank", "Halifax", "Bank of Scotland"])
        self.assertGreaterEqual(body["bank_detection_confidence"], self.expected["minimum_detection_confidence"])
        self.assertEqual(body["parser_adapter"], self.expected["parser_adapter"])
        self.assertEqual(body["page_count"], self.expected["page_count"])

    def test_halifax_family_bank_detection(self):
        pdf_bytes = self.halifax_fixture_path.read_bytes()
        response = self._post_pdf(pdf_bytes)
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["detected_bank"], "Halifax")
        self.assertGreaterEqual(body["bank_detection_confidence"], self.expected_halifax["minimum_detection_confidence"])
        self.assertEqual(body["parser_adapter"], self.expected_halifax["parser_adapter"])
        self.assertEqual(body["page_count"], self.expected_halifax["page_count"])

    def test_lloyds_family_extraction_reconciles(self):
        pdf_bytes = self.fixture_path.read_bytes()
        response = self._post_pdf(pdf_bytes)
        body = response.json()

        self.assertEqual(body["transaction_count"], self.expected["transaction_count"])
        self.assertEqual(body["statement"]["opening_balance"], self.expected["opening_balance"])
        self.assertEqual(body["statement"]["closing_balance"], self.expected["closing_balance"])
        self.assertEqual(body["statement"]["total_credits"], self.expected["total_credits"])
        self.assertEqual(body["statement"]["total_debits"], self.expected["total_debits"])
        self.assertEqual(body["statement"]["statement_start_date"], self.expected["statement_start_date"])
        self.assertEqual(body["statement"]["statement_end_date"], self.expected["statement_end_date"])
        self.assertEqual(body["reconciliation"]["status"], self.expected["reconciliation_status"])

        for expected_tx in self.expected["transactions"]:
            self.assertTrue(
                any(
                    tx["transaction_date"] == expected_tx["transaction_date"]
                    and tx["description_raw"] == expected_tx["description_raw"]
                    and abs(abs(tx["amount"]) - expected_tx["amount"]) < 0.01
                    and tx["type"] == expected_tx["type"]
                    and abs(tx["balance_after"] - expected_tx["balance_after"]) < 0.01
                    for tx in body["transactions"]
                ),
                f"Expected transaction not found: {expected_tx}",
            )

    def test_halifax_family_extraction_reconciles(self):
        pdf_bytes = self.halifax_fixture_path.read_bytes()
        response = self._post_pdf(pdf_bytes)
        body = response.json()

        self.assertEqual(body["transaction_count"], self.expected_halifax["transaction_count"])
        self.assertEqual(body["statement"]["opening_balance"], self.expected_halifax["opening_balance"])
        self.assertEqual(body["statement"]["closing_balance"], self.expected_halifax["closing_balance"])
        self.assertEqual(body["statement"]["total_credits"], self.expected_halifax["total_credits"])
        self.assertEqual(body["statement"]["total_debits"], self.expected_halifax["total_debits"])
        self.assertEqual(body["statement"]["statement_start_date"], self.expected_halifax["statement_start_date"])
        self.assertEqual(body["statement"]["statement_end_date"], self.expected_halifax["statement_end_date"])
        self.assertEqual(body["reconciliation"]["status"], self.expected_halifax["reconciliation_status"])

        for expected_tx in self.expected_halifax["transactions"]:
            self.assertTrue(
                any(
                    tx["transaction_date"] == expected_tx["transaction_date"]
                    and tx["description_raw"] == expected_tx["description_raw"]
                    and abs(abs(tx["amount"]) - expected_tx["amount"]) < 0.01
                    and tx["type"] == expected_tx["type"]
                    and abs(tx["balance_after"] - expected_tx["balance_after"]) < 0.01
                    for tx in body["transactions"]
                ),
                f"Expected transaction not found: {expected_tx}",
            )

    def test_lloyds_classic_summary_statement_reconciles(self):
        pdf_bytes = self.lloyds_2026_fixture_path.read_bytes()
        response = self._post_pdf(pdf_bytes)
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["status"], "success")
        self.assertEqual(body["detected_bank"], "Lloyds Bank")
        self.assertEqual(body["parser_adapter"], "lloyds_family_v1")
        self.assertEqual(body["page_count"], 3)
        self.assertEqual(body["transaction_count"], 8)
        self.assertEqual(body["statement"]["bank_name"], "Lloyds")
        self.assertEqual(body["statement"]["opening_balance"], self.expected_2026["opening_balance"])
        self.assertEqual(body["statement"]["closing_balance"], self.expected_2026["closing_balance"])
        self.assertEqual(body["statement"]["total_credits"], self.expected_2026["total_credits"])
        self.assertEqual(body["statement"]["total_debits"], self.expected_2026["total_debits"])
        self.assertEqual(body["reconciliation"]["status"], self.expected_2026["reconciliation_status"])
        self.assertEqual(body["reconciliation"]["calculated_total_credits"], self.expected_2026["total_credits"])
        self.assertEqual(body["reconciliation"]["calculated_total_debits"], self.expected_2026["total_debits"])
        self.assertTrue(body["parser_debug"]["summary_block_found"])
        self.assertEqual(body["parser_debug"]["balance_points_found"][0]["role"], "opening_balance")
        self.assertEqual(body["parser_debug"]["balance_points_found"][1]["role"], "closing_balance")
        self.assertEqual(body["parser_debug"]["transaction_rows_detected"], 8)
        self.assertEqual(body["parser_debug"]["per_page_transaction_counts"], {"1": 3, "2": 3, "3": 2})
        self.assertEqual(body["parser_debug"]["calculated_total_credits"], self.expected_2026["total_credits"])
        self.assertEqual(body["parser_debug"]["calculated_total_debits"], self.expected_2026["total_debits"])
        self.assertIsNotNone(body["parser_debug"]["first_transaction"])
        self.assertIsNotNone(body["parser_debug"]["last_transaction"])

    def test_lloyds_blank_format_transactions_extracted(self):
        pdf_bytes = build_lloyds_blank_format_pdf()
        response = self._post_pdf(pdf_bytes)
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["status"], "success")
        self.assertEqual(body["detected_bank"], "Lloyds Bank")
        self.assertEqual(body["parser_adapter"], "lloyds_family_v1")
        self.assertEqual(body["page_count"], 3)
        self.assertEqual(body["transaction_count"], 8)

        self.assertEqual(body["statement"]["opening_balance"], 173.0)
        self.assertEqual(body["statement"]["closing_balance"], 272.4)
        self.assertEqual(body["statement"]["total_credits"], 6537.0)
        self.assertEqual(body["statement"]["total_debits"], 6437.6)

        recon = body["reconciliation"]
        self.assertEqual(recon["status"], "matched")
        self.assertEqual(recon["calculated_total_credits"], 6537.0)
        self.assertEqual(recon["calculated_total_debits"], 6437.6)

        debug = body["parser_debug"]
        self.assertEqual(debug["adapter_selected"], "lloyds_family_v1")
        self.assertTrue(debug["header_parsed"])
        self.assertEqual(debug["transaction_rows_detected"], 8)
        self.assertEqual(debug["transactions_returned"], 8)
        self.assertEqual(debug["per_page_transaction_counts"], {"1": 3, "2": 3, "3": 2})
        self.assertEqual(debug["calculated_total_credits"], 6537.0)
        self.assertEqual(debug["calculated_total_debits"], 6437.6)
        self.assertIsNotNone(debug["first_transaction"])
        self.assertIsNotNone(debug["last_transaction"])

        by_description = {tx["description_raw"]: tx for tx in body["transactions"]}
        self.assertEqual(len(by_description), 8)

        # Empty Money In cell rendered inline as "blank." -> debit row.
        rochdale = by_description["ROCHDALE MBC"]
        self.assertEqual(rochdale["transaction_date"], "2026-01-02")
        self.assertEqual(rochdale["transaction_type"], "DD")
        self.assertEqual(rochdale["paid_in"], 0.0)
        self.assertEqual(rochdale["paid_out"], 15.4)
        self.assertEqual(rochdale["balance_after"], 1357.6)
        self.assertEqual(rochdale["type"], "debit")

        # Empty Money Out cell rendered inline as "blank." -> credit row,
        # with a comma-grouped amount.
        aidan = by_description["AIDAN SHERWOOD"]
        self.assertEqual(aidan["transaction_date"], "2026-01-01")
        self.assertEqual(aidan["transaction_type"], "FPI")
        self.assertEqual(aidan["paid_in"], 1200.0)
        self.assertEqual(aidan["paid_out"], 0.0)
        self.assertEqual(aidan["balance_after"], 1373.0)
        self.assertEqual(aidan["type"], "credit")

        # Inline "Date 05 Jan 26" row is still detected as a new transaction.
        bonus = by_description["BONUS PAYMENT"]
        self.assertEqual(bonus["transaction_date"], "2026-01-05")
        self.assertEqual(bonus["paid_in"], 1000.0)

        for tx in body["transactions"]:
            self.assertIn(tx["page_number"], (1, 2, 3))
            self.assertGreaterEqual(tx["row_index"], 1)

    def test_health_includes_available_adapters(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["status"], "ok")
        self.assertIsInstance(body.get("available_adapters"), list)
        self.assertIn("santander_v1", body["available_adapters"])
        self.assertIn("lloyds_family_v1", body["available_adapters"])

    def test_santander_regression_still_passes(self):
        pdf_bytes = build_santander_statement_pdf()
        response = self._post_pdf(pdf_bytes)
        body = response.json()

        self.assertEqual(body["status"], "success")
        self.assertEqual(body["detected_bank"], "Santander")
        self.assertGreaterEqual(body["bank_detection_confidence"], 0.95)
        self.assertEqual(body["parser_adapter"], "santander_v1")
        self.assertEqual(body["transaction_count"], 256)
        self.assertEqual(body["reconciliation"]["status"], "matched")
        self.assertEqual(body["statement"]["total_credits"], 8092.75)
        self.assertEqual(body["statement"]["total_debits"], 8273.18)
        self.assertEqual(body["statement"]["closing_balance"], 98.10)


if __name__ == "__main__":
    unittest.main()
