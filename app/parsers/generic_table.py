from typing import Any, Dict, Optional

from app.parsers.base import BaseParser
from app.services.reconciliation import extract_statement_totals


class GenericTableParser(BaseParser):
    name = "Generic"

    def can_parse(self, all_text: str, bank_hint: Optional[str] = None) -> bool:
        return True

    def parse(self, all_text: str) -> Dict[str, Any]:
        totals = extract_statement_totals(all_text)
        return {
            "bank_name": None,
            "totals": totals,
            "parser_used": "generic_table_v1",
        }
