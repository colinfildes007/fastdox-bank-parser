import re
from typing import Any, Dict, Optional

from app.parsers.base import BaseParser
from app.services.reconciliation import extract_statement_totals


class SantanderParser(BaseParser):
    name = "Santander"

    def can_parse(self, all_text: str, bank_hint: Optional[str] = None) -> bool:
        if bank_hint and "santander" in bank_hint.lower():
            return True

        return (
            "santander" in all_text.lower()
            or bool(re.search(r"Total debits\s+£", all_text, re.IGNORECASE))
        )

    def parse(self, all_text: str) -> Dict[str, Any]:
        totals = extract_statement_totals(all_text)
        return {
            "bank_name": self.name,
            "totals": totals,
            "parser_used": "santander_adapter_v1",
        }
