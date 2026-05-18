from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ExtractRequest(BaseModel):
    document_id: str
    file_url: str
    original_filename: Optional[str] = None
    bank_hint: Optional[str] = None


class Transaction(BaseModel):
    transaction_date: str
    description_raw: str
    paid_out: float
    paid_in: float
    amount: float
    direction: str
    balance_after: float
    page_number: int
    row_index: int


class Statement(BaseModel):
    statement_start_date: Optional[str] = None
    statement_end_date: Optional[str] = None
    currency: str = "GBP"
    total_debits: Optional[float] = None
    total_credits: Optional[float] = None
    closing_balance: Optional[float] = None
    derived_opening_balance: Optional[float] = None


class Account(BaseModel):
    bank_name: Optional[str] = None
    account_holder_name: Optional[str] = None
    account_number_masked: Optional[str] = None
    account_number_last4: Optional[str] = None
    sort_code_masked: Optional[str] = None
    currency: str = "GBP"


class Reconciliation(BaseModel):
    status: str
    calculated_total_debits: Optional[float] = None
    calculated_total_credits: Optional[float] = None
    statement_total_debits: Optional[float] = None
    statement_total_credits: Optional[float] = None
    closing_balance: Optional[float] = None
    derived_opening_balance: Optional[float] = None
    difference: Optional[float] = None


class ParserResponse(BaseModel):
    status: str
    parser_version: str
    document_id: str
    bank_name: Optional[str] = None
    page_count: int
    statement: Statement
    accounts: List[Account]
    transactions: List[Transaction]
    reconciliation: Reconciliation
    issues: List[Any]
    parser_debug: Dict[str, Any]
