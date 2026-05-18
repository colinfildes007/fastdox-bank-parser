import re
from typing import Dict

from app.parsers.base import BaseStatementParser


class GenericTableParser(BaseStatementParser):
    bank_name = "unknown"
    parser_name = "generic_table"

    def can_parse(self, context: Dict) -> bool:
        return True

    def parse(self, context: Dict) -> Dict:
        all_text = context.get("all_text", "")
        pages = context.get("pages", [])
        first_page_text = pages[0].get("text", "") if pages else ""
        sample_text = first_page_text[:500]

        headers = self._detect_headers(first_page_text)

        response = self.build_response(context)
        response.update(
            {
                "statement": {
                    "currency": "GBP",
                },
                "accounts": [],
                "transactions": [],
                "issues": ["unsupported_bank"],
                "reconciliation": {
                    "status": "unsupported_bank",
                    "calculated_total_debits": None,
                    "calculated_total_credits": None,
                    "statement_total_debits": None,
                    "statement_total_credits": None,
                    "closing_balance": None,
                    "derived_opening_balance": None,
                    "difference": None,
                },
                "parser_debug": {
                    "parser_name": self.parser_name,
                    "page_count": context.get("page_count", 0),
                    "text_layer_detected": context.get("text_layer_detected", False),
                    "sample_text": sample_text,
                    "headers_detected": headers,
                },
            }
        )

        return response

    def _detect_headers(self, text: str) -> Dict[str, str]:
        candidates = ["Date", "Description", "Debits", "Credits", "Balance", "Amount"]
        detected = {}

        for line in text.splitlines():
            normalized = line.strip()
            if not normalized:
                continue

            lower_line = normalized.lower()
            for candidate in candidates:
                if candidate.lower() in lower_line:
                    detected[candidate] = normalized

        return detected
