from typing import Dict, Optional, Sequence

from app.parsers.base import BaseStatementParser
from app.parsers.generic_table import GenericTableParser
from app.parsers.santander import SantanderStatementParser


def detect_bank(all_text: str, bank_hint: Optional[str] = None) -> str:
    if bank_hint and "santander" in bank_hint.lower():
        return "santander"

    if "santander" in all_text.lower():
        return "santander"

    return "unknown"


def available_parsers() -> Sequence[BaseStatementParser]:
    return [SantanderStatementParser(), GenericTableParser()]


def select_parser(context: Dict) -> BaseStatementParser:
    for parser in available_parsers():
        if parser.can_parse(context):
            return parser
    return GenericTableParser()
