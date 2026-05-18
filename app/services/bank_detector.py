from typing import Optional, Sequence

from app.parsers.base import BaseParser
from app.parsers.generic_table import GenericTableParser
from app.parsers.santander import SantanderParser


def available_parsers() -> Sequence[BaseParser]:
    return [SantanderParser(), GenericTableParser()]


def select_parser(all_text: str, bank_hint: Optional[str] = None) -> BaseParser:
    for parser in available_parsers():
        if parser.can_parse(all_text, bank_hint):
            return parser
    return GenericTableParser()
