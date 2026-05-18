from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BaseParser(ABC):
    name: str

    @abstractmethod
    def can_parse(self, all_text: str, bank_hint: Optional[str] = None) -> bool:
        raise NotImplementedError

    @abstractmethod
    def parse(self, all_text: str) -> Dict[str, Any]:
        raise NotImplementedError
