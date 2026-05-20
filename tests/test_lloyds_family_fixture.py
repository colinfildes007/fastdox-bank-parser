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
        self.assertEqual(body["statement"]["bank_name"], "Lloyds")
        self.assertEqual(body["statement"]["opening_balance"], self.expected_2026["opening_balance"])
        self.assertEqual(body["statement"]["closing_balance"], self.expected_2026["closing_balance"])
        self.assertEqual(body["statement"]["total_credits"], self.expected_2026["total_credits"])
        self.assertEqual(body["statement"]["total_debits"], self.expected_2026["total_debits"])
        self.assertEqual(body["reconciliation"]["status"], self.expected_2026["reconciliation_status"])
        self.assertTrue(body["parser_debug"]["summary_block_found"])

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
