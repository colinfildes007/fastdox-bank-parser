import unittest

from app.parsers.santander import SantanderStatementParser
from app.services.reconciliation import reconcile


class SantanderParserFinalPageTest(unittest.TestCase):
    def test_final_transaction_before_footer_is_parsed(self):
        context = {
            "document_id": "test-doc",
            "bank_hint": "Santander",
            "original_filename": "statement.pdf",
            "page_count": 10,
            "pages": [
                {
                    "page_number": 10,
                    "text": (
                        "18th Feb 25 Tesco Stores £13.86 £264.67\n"
                        "Total debits £8,273.18\n"
                        "Total credit £8,092.75\n"
                        "Closing Balance £264.67"
                    ),
                }
            ],
            "all_text": (
                "18th Feb 25 Tesco Stores £13.86 £264.67\n"
                "Total debits £8,273.18\n"
                "Total credit £8,092.75\n"
                "Closing Balance £264.67"
            ),
            "text_layer_detected": True,
        }

        parser = SantanderStatementParser()
        result = parser.parse(context)

        self.assertEqual(result["bank_name"], "Santander")
        self.assertEqual(len(result["transactions"]), 1)

        transaction = result["transactions"][0]
        self.assertEqual(transaction["transaction_date"], "2025-02-18")
        self.assertEqual(transaction["description_raw"], "Tesco Stores")
        self.assertEqual(transaction["paid_out"], 13.86)
        self.assertEqual(transaction["paid_in"], 0.0)
        self.assertEqual(transaction["amount"], -13.86)
        self.assertEqual(transaction["direction"], "debit")
        self.assertEqual(transaction["balance_after"], 264.67)
        self.assertEqual(transaction["page_number"], 10)

        final_page_check = result["parser_debug"]["final_page_check"]
        self.assertTrue(final_page_check["last_transaction_detected"])
        self.assertEqual(final_page_check["last_transaction_date"], "2025-02-18")
        self.assertEqual(final_page_check["last_transaction_description"], "Tesco Stores")
        self.assertEqual(final_page_check["last_transaction_amount"], 13.86)
        self.assertEqual(final_page_check["last_transaction_balance"], 264.67)

        self.assertNotIn("unknown_direction", result["issues"])

        reconciliation = reconcile(result["statement"], result["transactions"])
        self.assertEqual(reconciliation["status"], "mismatch")
        self.assertEqual(reconciliation["calculated_total_debits"], 13.86)
        self.assertEqual(reconciliation["calculated_total_credits"], 0.0)
        self.assertEqual(reconciliation["statement_total_debits"], 8273.18)
        self.assertEqual(reconciliation["statement_total_credits"], 8092.75)

    def test_reconcile_matches_expected_santander_totals(self):
        statement = {
            "total_debits": 8273.18,
            "total_credits": 8092.75,
            "closing_balance": 98.10,
            "derived_opening_balance": 278.53,
        }
        transactions = [
            {"direction": "debit", "paid_out": 8273.18, "paid_in": 0.0},
            {"direction": "credit", "paid_out": 0.0, "paid_in": 8092.75},
        ]

        reconciliation = reconcile(statement, transactions)
        self.assertEqual(reconciliation["status"], "matched")
        self.assertEqual(reconciliation["calculated_total_debits"], 8273.18)
        self.assertEqual(reconciliation["calculated_total_credits"], 8092.75)
        self.assertEqual(reconciliation["statement_total_debits"], 8273.18)
        self.assertEqual(reconciliation["statement_total_credits"], 8092.75)
        self.assertEqual(reconciliation["difference"], 0.0)


if __name__ == "__main__":
    unittest.main()
