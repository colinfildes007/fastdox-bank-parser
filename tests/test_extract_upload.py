import io
import unittest

import fitz
from fastapi.testclient import TestClient

from app.main import app


def build_pdf_bytes(pages_text):
    doc = fitz.open()
    for page_text in pages_text:
        page = doc.new_page()
        page.insert_text((72, 72), page_text, fontsize=10)
    return doc.write()


def _ordinal(day):
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def build_santander_statement_pdf():
    header = (
        "Santander UK plc\n"
        "Account Summary\n"
        "Date Description Debits Credits Balance\n"
    )
    transaction_lines = []
    starting_balance = 10000.00
    for i in range(1, 257):
        balance = starting_balance + 10.00 * (i - 1)
        day = ((i - 1) % 28) + 1
        transaction_lines.append(
            f"{_ordinal(day)} Jan 25 Transfer to Merchant {i} £10.00 £{balance:.2f}"
        )

    pages = []
    chunk_size = 20
    for start in range(0, len(transaction_lines), chunk_size):
        chunk = transaction_lines[start : start + chunk_size]
        page_text = "\n".join(chunk)
        if start == 0:
            page_text = header + page_text
        pages.append(page_text)

    pages[-1] += "\nTotal debits £2560.00\nTotal credit £0.00\nClosing Balance £12550.00\n"
    return build_pdf_bytes(pages)


def build_unknown_bank_pdf():
    page_text = (
        "Acme Bank statement\n"
        "Date Description Debits Credits Balance\n"
        "1st Jan 25 Transfer to Service £10.00 £990.00\n"
        "Total debits £10.00\n"
        "Total credit £0.00\n"
        "Closing Balance £990.00\n"
    )
    return build_pdf_bytes([page_text])


class ExtractUploadBankDetectionTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.santander_pdf = build_santander_statement_pdf()
        self.unknown_pdf = build_unknown_bank_pdf()

    def _post_pdf(self, pdf_bytes, bank_hint=None):
        data = {"document_id": "test-doc"}
        if bank_hint is not None:
            data["bank_hint"] = bank_hint
        files = {
            "file": ("statement.pdf", io.BytesIO(pdf_bytes), "application/pdf")
        }
        return self.client.post("/extract-upload", data=data, files=files)

    def test_santander_pdf_bank_hint_auto(self):
        response = self._post_pdf(self.santander_pdf, bank_hint="auto")
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["status"], "success")
        self.assertEqual(body["detected_bank"], "Santander")
        self.assertGreaterEqual(body["bank_detection_confidence"], 0.95)
        self.assertEqual(body["parser_adapter"], "santander_v1")
        self.assertEqual(body["bank_hint"], "auto")
        self.assertEqual(body["transaction_count"], 256)
        self.assertEqual(body["reconciliation"]["status"], "matched")

    def test_santander_pdf_no_bank_hint(self):
        response = self._post_pdf(self.santander_pdf)
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["status"], "success")
        self.assertEqual(body["detected_bank"], "Santander")
        self.assertGreaterEqual(body["bank_detection_confidence"], 0.95)
        self.assertEqual(body["parser_adapter"], "santander_v1")
        self.assertEqual(body["transaction_count"], 256)
        self.assertEqual(body["reconciliation"]["status"], "matched")

    def test_santander_pdf_conflicting_bank_hint(self):
        response = self._post_pdf(self.santander_pdf, bank_hint="lloyds")
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["status"], "success")
        self.assertEqual(body["detected_bank"], "Santander")
        self.assertEqual(body["parser_adapter"], "santander_v1")
        self.assertEqual(body["bank_hint"], "lloyds")
        self.assertTrue(body.get("bank_detection_conflict", False))
        self.assertEqual(body["transaction_count"], 256)
        self.assertEqual(body["reconciliation"]["status"], "matched")

    def test_unknown_bank_returns_unsupported(self):
        response = self._post_pdf(self.unknown_pdf, bank_hint="auto")
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["status"], "unsupported_or_uncertain_bank")
        self.assertEqual(body["transactions"], [])
        self.assertIsNotNone(body.get("error"))
        self.assertEqual(body["parser_metadata"]["detected_bank"], "unknown")
        self.assertEqual(body["parser_metadata"]["bank_hint"], "auto")
        self.assertEqual(body["parser_metadata"]["parser_adapter"], None)


if __name__ == "__main__":
    unittest.main()
