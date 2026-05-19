import io
import unittest

import fitz
from fastapi.testclient import TestClient

from app.main import app
from app.parsers.lloyds import LloydsStatementParser
from app.services.bank_detector import detect_bank
from app.services.reconciliation import reconcile


def build_pdf_bytes(pages_text):
    doc = fitz.open()
    for page_text in pages_text:
        page = doc.new_page()
        page.insert_text((72, 72), page_text, fontsize=10)
    return doc.write()


def build_lloyds_statement_pdf(bank_name: str):
    header = (
        f"{bank_name} plc\n"
        "Account name John Smith\n"
        "Sort code 30-00-00\n"
        "Account number 12345678\n"
        "Statement period 01 Jan 25 to 06 Jan 25\n"
        "Date Transaction details Debit Credit Balance\n"
    )

    transactions = [
        "01 Jan 25 Salary 0.00 1200.00 2200.00",
        "02 Jan 25 Coffee 3.20 0.00 2196.80",
        "03 Jan 25 Grocery 45.80 0.00 2151.00",
        "04 Jan 25 Refund 0.00 10.00 2161.00",
        "05 Jan 25 Bill payment 100.00 0.00 2061.00",
        "06 Jan 25 Interest 0.00 0.80 2061.80",
    ]

    page_text = header + "\n".join(transactions)
    page_text += "\nTotal debits £149.00\nTotal credits £1,210.80\nClosing balance £2,061.80\n"
    return build_pdf_bytes([page_text])


class LloydsParserTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.lloyds_pdf = build_lloyds_statement_pdf("Lloyds Bank")
        self.halifax_pdf = build_lloyds_statement_pdf("Halifax")
        self.bos_pdf = build_lloyds_statement_pdf("Bank of Scotland")

    def _post_pdf(self, pdf_bytes, bank_hint=None):
        data = {"document_id": "test-doc"}
        if bank_hint is not None:
            data["bank_hint"] = bank_hint
        files = {
            "file": ("statement.pdf", io.BytesIO(pdf_bytes), "application/pdf")
        }
        return self.client.post("/extract-upload", data=data, files=files)

    def test_lloyds_pdf_auto_detects_and_parses(self):
        response = self._post_pdf(self.lloyds_pdf)
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["status"], "success")
        self.assertEqual(body["detected_bank"], "Lloyds Bank")
        self.assertGreaterEqual(body["bank_detection_confidence"], 0.90)
        self.assertEqual(body["parser_adapter"], "lloyds_family_v1")
        self.assertEqual(body["transaction_count"], 6)
        self.assertEqual(body["reconciliation"]["status"], "matched")
        self.assertEqual(body["statement"]["opening_balance"], 1000.0)
        self.assertEqual(body["statement"]["closing_balance"], 2061.8)
        self.assertEqual(body["statement"]["total_debits"], 149.0)
        self.assertEqual(body["statement"]["total_credits"], 1210.8)

    def test_halifax_pdf_auto_detects_using_shared_parser(self):
        response = self._post_pdf(self.halifax_pdf)
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["status"], "success")
        self.assertEqual(body["detected_bank"], "Halifax")
        self.assertEqual(body["parser_adapter"], "lloyds_family_v1")
        self.assertEqual(body["transaction_count"], 6)
        self.assertEqual(body["reconciliation"]["status"], "matched")

    def test_bank_of_scotland_pdf_auto_detects_using_shared_parser(self):
        response = self._post_pdf(self.bos_pdf)
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["status"], "success")
        self.assertEqual(body["detected_bank"], "Bank of Scotland")
        self.assertEqual(body["parser_adapter"], "lloyds_family_v1")
        self.assertEqual(body["transaction_count"], 6)
        self.assertEqual(body["reconciliation"]["status"], "matched")

    def test_bank_detector_recognizes_supported_bank_names(self):
        response = detect_bank(
            [{"page_number": 1, "text": "Halifax account statement Statement period 01 Jan 25 to 06 Jan 25"}],
            bank_hint=None,
        )
        self.assertEqual(response["detected_bank"], "Halifax")
        self.assertGreaterEqual(response["bank_detection_confidence"], 0.90)

    def test_lloyds_parser_can_parse_by_hint(self):
        context = {
            "document_id": "test-doc",
            "bank_hint": "lloyds",
            "detected_bank": "Lloyds Bank",
            "page_count": 1,
            "pages": [{"page_number": 1, "text": "01 Jan 25 Salary 0.00 1200.00 2200.00"}],
            "all_text": "01 Jan 25 Salary 0.00 1200.00 2200.00",
            "text_layer_detected": True,
        }
        parser = LloydsStatementParser()
        self.assertTrue(parser.can_parse(context))
        result = parser.parse(context)
        self.assertEqual(result["bank_name"], "Lloyds Bank")
        self.assertEqual(len(result["transactions"]), 1)
        self.assertEqual(result["transactions"][0]["type"], "credit")
