from abc import ABC, abstractmethod
from typing import Dict


class BaseStatementParser(ABC):
    bank_name = "unknown"
    parser_name = "base"

    @abstractmethod
    def can_parse(self, context: Dict) -> bool:
        raise NotImplementedError

    @abstractmethod
    def parse(self, context: Dict) -> Dict:
        raise NotImplementedError

    def build_response(self, context: Dict) -> Dict:
        return {
            "bank_name": self.bank_name,
            "statement": {},
            "accounts": [],
            "transactions": [],
            "issues": [],
            "parser_debug": {
                "parser_name": self.parser_name,
                **context.get("parser_debug", {}),
            },
        }
