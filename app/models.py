from typing import Optional

from pydantic import BaseModel


class ExtractRequest(BaseModel):
    document_id: str
    file_url: str
    original_filename: Optional[str] = None
    bank_hint: Optional[str] = None
