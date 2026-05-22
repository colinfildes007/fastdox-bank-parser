import io
import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def build_santander_statement_pdf():
    import fitz

    def _ordinal(day):
        if 10 <= day % 100 <= 20:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        return f"{day}{suffix}"

    header = (
        "Santander UK plc\n"
        "Account Summary\n"
        "Date Description Debits Credits Balance\n"
    )

    debit_values = [64.00] * 127 + [145.18]
    credit_values = [63.00] * 127 + [91.75]
    balance = 98.10
    transaction_lines = []

    for i in range(256):
        day = 28 - (i % 28)
        if i % 2 == 0:
            amount = debit_values[i // 2]
            description = f"Transfer to merchant {i + 1}"
            previous_balance = balance + amount
        else:
            amount = credit_values[i // 2]
            description = f"Bank giro credit {i + 1}"
            previous_balance = balance - amount

        transaction_lines.append(
            f"{_ordinal(day)} Jan 25 {description} £{amount:.2f} £{balance:.2f}"
        )
        balance = previous_balance

    pages = []
    chunk_size = 20
    for start in range(0, len(transaction_lines), chunk_size):
        chunk = transaction_lines[start : start + chunk_size]
        page_text = "\n".join(chunk)
        if start == 0:
            page_text = header + page_text
        pages.append(page_text)

    pages[-1] += "\nTotal debits £8273.18\nTotal credit £8092.75\nClosing Balance £98.10\n"
    doc = fitz.open()
    for page_text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), page_text, fontsize=10)
    return doc.write()


def build_lloyds_blank_format_pdf():
    """A Lloyds 'Classic' statement in the labelled-block layout.

    Empty money cells are rendered as the literal token 'blank.' (sometimes
    inline with the label), amounts use comma grouping, and the table spans
    three pages with a repeated column-header row on page 2.
    """
    import fitz

    page_1 = "\n".join(
        [
            "Lloyds Bank plc",
            "Classic statement",
            "Account name John Smith",
            "Sort code 30-00-00",
            "Account number 12345678",
            "Statement period 01 Jan 26 to 31 Jan 26",
            "Money In £6,537.00",
            "Money Out £6,437.60",
            "Balance on 01 January 2026 £173.00",
            "Balance on 31 January 2026 £272.40",
            "Your Transactions",
            "Date", "01 Jan 26",
            "Description", "AIDAN SHERWOOD",
            "Type", "FPI",
            "Money In (£)", "1,200.00",
            "Money Out (£) blank.",
            "Balance (£)", "1,373.00",
            "Date", "02 Jan 26",
            "Description", "ROCHDALE MBC",
            "Type", "DD",
            "Money In (£) blank.",
            "Money Out (£)", "15.40",
            "Balance (£)", "1,357.60",
            "Date", "03 Jan 26",
            "Description", "TRANSFER IN",
            "Type", "FPI",
            "Money In (£)", "2,000.00",
            "Money Out (£) blank.",
            "Balance (£)", "3,357.60",
        ]
    )

    page_2 = "\n".join(
        [
            "Date Description Type Money In (£) Money Out (£) Balance (£)",
            "Date", "04 Jan 26",
            "Description", "SUPERMARKET",
            "Type", "DD",
            "Money In (£) blank.",
            "Money Out (£)", "1,500.00",
            "Balance (£)", "1,857.60",
            "Date 05 Jan 26",
            "Description", "BONUS PAYMENT",
            "Type", "CR",
            "Money In (£)", "1,000.00",
            "Money Out (£) blank.",
            "Balance (£)", "2,857.60",
            "Date", "06 Jan 26",
            "Description", "UTILITY PROVIDER",
            "Type", "DD",
            "Money In (£) blank.",
            "Money Out (£)", "800.00",
            "Balance (£)", "2,057.60",
            "Page 2 of 3",
        ]
    )

    page_3 = "\n".join(
        [
            "Date", "07 Jan 26",
            "Description", "INSURANCE REFUND",
            "Type", "FPI",
            "Money In (£)", "2,337.00",
            "Money Out (£) blank.",
            "Balance (£)", "4,394.60",
            "Date", "08 Jan 26",
            "Description", "RENT PAYMENT",
            "Type", "DD",
            "Money In (£) blank.",
            "Money Out (£)", "4,122.20",
            "Balance (£)", "272.40",
            "Transaction types",
            "DD Direct Debit",
            "FPI Faster Payment In",
            "CR Credit",
        ]
    )

    doc = fitz.open()
    for page_text in (page_1, page_2, page_3):
        page = doc.new_page()
        page.insert_text((54, 54), page_text, fontsize=9)
    return doc.write()


def build_lloyds_flat_table_pdf():
    """A Lloyds 'Classic' statement whose 3-page transaction table is rendered
    as flat single-line rows: '<date> <description> <type> <in> <out> <balance>'
    with the literal token 'blank' in empty money cells. 48 transactions."""
    import fitz

    def money(value):
        return f"{value:,.2f}"

    credit_amounts = [3000.00] + [150.00] * 22 + [237.00]   # 24 rows -> 6537.00
    debit_amounts = [1200.00] + [220.00] * 22 + [397.60]    # 24 rows -> 6437.60
    credit_types = ["FPI", "BGC", "DEP", "PAY"]
    debit_types = ["DD", "DEB", "FPO", "SO", "CPT"]
    credit_names = ["HTEC SOLUTIO LTD", "AIDAN SHERWOOD", "ROCHDALE MBC", "BACS CREDIT"]
    debit_names = ["THREE MOBILE", "WILLIAMHILL*INTERN", "TESCO STORES", "BRITISH GAS", "NETFLIX COM"]

    rows = []
    balance = 173.00
    for i in range(24):
        credit = credit_amounts[i]
        balance = round(balance + credit, 2)
        day = 1 + (len(rows) % 28)
        rows.append(
            f"{day:02d} Jan 26 {credit_names[i % len(credit_names)]} "
            f"{credit_types[i % len(credit_types)]} {money(credit)} blank {money(balance)}"
        )
        debit = debit_amounts[i]
        balance = round(balance - debit, 2)
        day = 1 + (len(rows) % 28)
        rows.append(
            f"{day:02d} Jan 26 {debit_names[i % len(debit_names)]} "
            f"{debit_types[i % len(debit_types)]} blank {money(debit)} {money(balance)}"
        )

    column_header = "Date Description Type Money In (£) Money Out (£) Balance (£)"
    header_lines = [
        "Lloyds Bank plc",
        "Classic statement",
        "Account name John Smith",
        "Sort code 30-00-00",
        "Account number 12345678",
        "Statement period 01 Jan 26 to 31 Jan 26",
        "Money In £6,537.00",
        "Money Out £6,437.60",
        "Balance on 01 January 2026 £173.00",
        "Balance on 31 January 2026 £272.40",
        "Your Transactions",
        column_header,
    ]
    page_1 = "\n".join(header_lines + rows[0:16])
    page_2 = "\n".join([column_header] + rows[16:32])
    page_3 = "\n".join(
        [column_header]
        + rows[32:48]
        + [
            "Transaction types",
            "DD Direct Debit",
            "FPI Faster Payment In",
            "FPO Faster Payment Out",
            "DEB Debit card payment",
            "SO Standing Order",
        ]
    )

    doc = fitz.open()
    for page_text in (page_1, page_2, page_3):
        page = doc.new_page()
        page.insert_text((54, 54), page_text, fontsize=8)
    return doc.write()


LLOYDS_REAL_TRANSACTIONS = [
    # page, date, description, type, money_in, money_out, balance
    (1, "02 Jan 26", "ROCHDALE MBC", "DD", None, 164.00, 9.00),
    (1, "02 Jan 26", "AIDAN SHERWOOD", "FPI", 295.00, None, 304.00),
    (1, "02 Jan 26", "EMELIE BYROM", "FPO", None, 120.00, 184.00),
    (1, "02 Jan 26", "DWF LAW LLP", "FPO", None, 50.00, 134.00),
    (1, "02 Jan 26", "LINDSAY LEONG", "FPO", None, 25.00, 109.00),
    (1, "02 Jan 26", "PAUL SHERWOOD", "FPO", None, 100.00, 9.00),
    (1, "05 Jan 26", "PAUL MICHAEL SHERW", "FPI", 240.00, None, 249.00),
    (1, "05 Jan 26", "LNK TESCO OLDH MID", "CPT", None, 240.00, 9.00),
    (1, "05 Jan 26", "SHERWOOD P M", "FPI", 30.00, None, 39.00),
    (1, "05 Jan 26", "LNK COOPERATIVE M2", "CPT", None, 30.00, 9.00),
    (1, "05 Jan 26", "SHERWOOD P M", "FPI", 500.00, None, 509.00),
    (1, "05 Jan 26", "P.O. G9 MIDDLETON", "CPT", None, 300.00, 209.00),
    (1, "05 Jan 26", "P.O. G9 MIDDLETON", "CPT", None, 200.00, 9.00),
    (1, "09 Jan 26", "SHERWOOD P M", "FPI", 50.00, None, 59.00),
    (1, "09 Jan 26", "DWF LAW LLP", "FPO", None, 50.00, 9.00),
    (1, "12 Jan 26", "SHERWOOD P M", "FPI", 140.00, None, 149.00),
    (2, "12 Jan 26", "KALOOKI SPORTSBOOK", "FPO", None, 100.00, 49.00),
    (2, "12 Jan 26", "PAUL SHERWOOD", "FPO", None, 40.00, 9.00),
    (2, "12 Jan 26", "HTEC SOLUTIO LTD", "FPI", 3000.00, None, 3009.00),
    (2, "12 Jan 26", "COLIN FILDES", "FPO", None, 1500.00, 1509.00),
    (2, "12 Jan 26", "PAUL SHERWOOD", "FPO", None, 1500.00, 9.00),
    (2, "12 Jan 26", "AIDAN SHERWOOD", "FPI", 50.00, None, 59.00),
    (2, "12 Jan 26", "PAUL SHERWOOD", "FPO", None, 50.00, 9.00),
    (2, "16 Jan 26", "AIDAN SHERWOOD", "FPI", 325.00, None, 334.00),
    (2, "16 Jan 26", "EMELIE BYROM", "FPO", None, 120.00, 214.00),
    (2, "16 Jan 26", "DWF LAW LLP", "FPO", None, 50.00, 164.00),
    (2, "16 Jan 26", "LINDSAY LEONG", "FPO", None, 50.00, 114.00),
    (2, "16 Jan 26", "PAUL SHERWOOD", "FPO", None, 105.00, 9.00),
    (2, "23 Jan 26", "AIDAN SHERWOOD", "FPI", 300.00, None, 309.00),
    (2, "23 Jan 26", "DWF LAW LLP", "FPO", None, 50.00, 259.00),
    (2, "23 Jan 26", "EMELIE BYROM", "FPO", None, 120.00, 139.00),
    (2, "23 Jan 26", "LINDSAY LEONG", "FPO", None, 25.00, 114.00),
    (2, "23 Jan 26", "PAUL SHERWOOD", "FPO", None, 105.00, 9.00),
    (2, "23 Jan 26", "PAUL SHERWOOD", "FPO", None, 9.00, 0.00),
    (2, "26 Jan 26", "J JOHNSTONE", "SO", 940.00, None, 940.00),
    (2, "26 Jan 26", "PAUL SHERWOOD", "FPO", None, 700.00, 240.00),
    (2, "26 Jan 26", "PAUL SHERWOOD", "FPO", None, 20.00, 220.00),
    (2, "27 Jan 26", "PAUL SHERWOOD", "FPO", None, 10.00, 210.00),
    (3, "28 Jan 26", "UNITED UTILITIES W", "DD", None, 67.00, 143.00),
    (3, "28 Jan 26", "SHERWOOD P M", "FPI", 30.00, None, 173.00),
    (3, "28 Jan 26", "SHERWOOD P M", "FPI", 50.00, None, 223.00),
    (3, "28 Jan 26", "LNK TESCO OLDH MID", "CPT", None, 50.00, 173.00),
    (3, "28 Jan 26", "SHERWOOD P M", "FPI", 100.00, None, 273.00),
    (3, "28 Jan 26", "SHERWOOD P M", "FPI", 60.00, None, 333.00),
    (3, "29 Jan 26", "SHERWOOD P M", "FPI", 100.00, None, 433.00),
    (3, "29 Jan 26", "WILLIAMHILL*INTERN", "DEB", None, 50.00, 383.00),
    (3, "29 Jan 26", "WILLIAMHILL*INTERN", "DEB", None, 50.00, 333.00),
    (3, "29 Jan 26", "WILLIAMHILL*INTERN", "DEB", None, 60.00, 273.00),
    (3, "30 Jan 26", "AIDAN SHERWOOD", "FPI", 227.00, None, 500.00),
    (3, "30 Jan 26", "DWF LAW LLP", "FPO", None, 50.00, 450.00),
    (3, "30 Jan 26", "EMELIE BYROM", "FPO", None, 120.00, 330.00),
    (3, "30 Jan 26", "THREE MOBILE", "FPO", None, 32.60, 297.40),
    (3, "30 Jan 26", "LINDSAY LEONG", "FPO", None, 25.00, 272.40),
    (3, "30 Jan 26", "AIDAN SHERWOOD", "FPI", 100.00, None, 372.40),
    (3, "30 Jan 26", "WILLIAMHILL*INTERN", "DEB", None, 100.00, 272.40),
]


def build_lloyds_real_classic_pdf():
    """Reproduction of a real Lloyds 'Classic' statement: 3 pages, 55
    transactions in labelled-block layout where every cell value carries a
    trailing full stop and empty money cells read 'blank.'."""
    import fitz

    def money_cell(value):
        return "blank." if value is None else f"{value:,.2f}."

    column_block = [
        "Your Transactions",
        "Column", "Date.",
        "Column", "Description.",
        "Column", "Type.",
        "Column", "Money In (£).",
        "Column", "Money Out (£).",
        "Column", "Balance (£).",
    ]

    def tx_block(date, desc, ttype, money_in, money_out, balance):
        return [
            "Date", f"{date}.",
            "Description", f"{desc}.",
            "Type", f"{ttype}.",
            "Money In (£)", money_cell(money_in),
            "Money Out (£)", money_cell(money_out),
            "Balance (£)", f"{balance:,.2f}.",
        ]

    pages_lines = {
        1: [
            "Page 1 of 3",
            "Lloyds Bank plc",
            "Classic statement",
            "Your Account",
            "Sort Code 77-19-26",
            "Account Number 41496768",
            "Statement period 01 Jan 26 to 31 Jan 26",
            "Money In £6,537.00",
            "Money Out £6,437.60",
            "Balance on 01 January 2026 £173.00",
            "Balance on 31 January 2026 £272.40",
        ],
        2: ["Page 2 of 3", "Lloyds Bank plc", "CLASSIC Sort Code 77-19-26", "Account Number 41496768"],
        3: ["Page 3 of 3", "Lloyds Bank plc", "CLASSIC Sort Code 77-19-26", "Account Number 41496768"],
    }
    for page_number in (1, 2, 3):
        pages_lines[page_number] += column_block
    for (page_number, date, desc, ttype, money_in, money_out, balance) in LLOYDS_REAL_TRANSACTIONS:
        pages_lines[page_number] += tx_block(date, desc, ttype, money_in, money_out, balance)
    pages_lines[1].append("(Continued on next page)")
    pages_lines[2].append("(Continued on next page)")
    pages_lines[3] += [
        "Transaction types.",
        "BGC. Bank Giro Credit. BP. Bill Payments. CHG. Charge. CHQ. Cheque.",
        "DD. Direct Debit. DEB. Debit Card. FPI. Faster Payment In. FPO. Faster Payment Out.",
        "SO. Standing Order. TFR. Transfer.",
    ]

    doc = fitz.open()
    for page_number in (1, 2, 3):
        lines = pages_lines[page_number]
        # Tall page so every line fits and is recovered by the text layer.
        page = doc.new_page(width=612, height=80 + len(lines) * 11 + 60)
        y = 50
        for line in lines:
            page.insert_text((40, y), line, fontsize=8)
            y += 11
    return doc.write()


def build_lloyds_merged_blocks_pdf():
    """Reproduction of the real statement's text layer: labelled blocks where
    the empty money cell ("blank.") and its column label are merged onto the
    preceding value line — e.g. "DD Money In (£) blank." after a "Type" line,
    or "295.00 Money Out (£) blank." after a "Money In (£)" line."""
    import fitz

    def amount(value):
        return f"{value:,.2f}"

    column_block = [
        "Your Transactions",
        "Column", "Date", "Column", "Description", "Column", "Type",
        "Column", "Money In (£)", "Column", "Money Out (£)", "Column", "Balance (£)",
    ]

    def tx_lines(date, desc, ttype, money_in, money_out, balance):
        if money_in is None:
            # Debit: empty Money In cell merges onto the Type value line.
            return [
                "Date", date, "Description", desc,
                "Type", f"{ttype} Money In (£) blank.",
                "Money Out (£)", amount(money_out),
                "Balance (£)", amount(balance),
            ]
        # Credit: empty Money Out cell merges onto the Money In value line.
        return [
            "Date", date, "Description", desc, "Type", ttype,
            "Money In (£)", f"{amount(money_in)} Money Out (£) blank.",
            "Balance (£)", amount(balance),
        ]

    pages_lines = {
        1: [
            "Page 1 of 3", "Lloyds Bank plc", "Classic statement",
            "Your Account", "Sort Code 77-19-26", "Account Number 41496768",
            "Statement period 01 Jan 26 to 31 Jan 26",
            "Money In £6,537.00", "Money Out £6,437.60",
            "Balance on 01 January 2026 £173.00",
            "Balance on 31 January 2026 £272.40",
        ],
        2: ["Page 2 of 3", "Lloyds Bank plc", "CLASSIC Sort Code 77-19-26", "Account Number 41496768"],
        3: ["Page 3 of 3", "Lloyds Bank plc", "CLASSIC Sort Code 77-19-26", "Account Number 41496768"],
    }
    for page_number in (1, 2, 3):
        pages_lines[page_number] += column_block
    for (page_number, date, desc, ttype, money_in, money_out, balance) in LLOYDS_REAL_TRANSACTIONS:
        pages_lines[page_number] += tx_lines(date, desc, ttype, money_in, money_out, balance)
    pages_lines[1].append("(Continued on next page)")
    pages_lines[2].append("(Continued on next page)")
    pages_lines[3] += [
        "Transaction types blank.",
        "BGC Bank Giro Credit BP Bill Payments CHG Charge CHQ Cheque",
        "DD Direct Debit DEB Debit Card FPI Faster Payment In FPO Faster Payment Out",
        "SO Standing Order TFR Transfer",
    ]

    doc = fitz.open()
    for page_number in (1, 2, 3):
        lines = pages_lines[page_number]
        page = doc.new_page(width=612, height=80 + len(lines) * 11 + 60)
        y = 50
        for line in lines:
            page.insert_text((40, y), line, fontsize=8)
            y += 11
    return doc.write()


def build_lloyds_ruled_table_pdf():
    """A Lloyds 'Classic' statement drawn as a real ruled table (visible cell
    borders), so pdfplumber segments it by geometry via extract_tables() rather
    than the (garbled) document-order text layer."""
    import fitz

    col_x = [40, 110, 250, 312, 396, 476, 560]  # 6 columns -> 7 boundaries
    headers = ["Date", "Description", "Type", "Money In (£)", "Money Out (£)", "Balance (£)"]

    def cell_amount(value):
        return "" if value is None else f"{value:,.2f}"

    pages_rows = {1: [], 2: [], 3: []}
    for (page_number, date, desc, ttype, money_in, money_out, balance) in LLOYDS_REAL_TRANSACTIONS:
        pages_rows[page_number].append(
            [date, desc, ttype, cell_amount(money_in), cell_amount(money_out), f"{balance:,.2f}"]
        )

    intros = {
        1: [
            "Lloyds Bank plc",
            "Classic statement",
            "Sort Code 77-19-26  Account Number 41496768",
            "Statement period 01 Jan 26 to 31 Jan 26",
            "Money In £6,537.00   Money Out £6,437.60",
            "Balance on 01 January 2026 £173.00",
            "Balance on 31 January 2026 £272.40",
            "Your Transactions",
        ],
        2: ["Lloyds Bank plc", "CLASSIC Sort Code 77-19-26", "Your Transactions"],
        3: ["Lloyds Bank plc", "CLASSIC Sort Code 77-19-26", "Your Transactions"],
    }

    doc = fitz.open()
    for page_number in (1, 2, 3):
        intro = intros[page_number]
        rows = pages_rows[page_number]
        row_height = 16
        table_top = 60 + len(intro) * 13 + 16
        line_count = len(rows) + 1  # header row + data rows
        table_bottom = table_top + line_count * row_height
        page = doc.new_page(width=600, height=table_bottom + 60)

        y = 50
        for line in intro:
            page.insert_text((40, y), line, fontsize=8)
            y += 13

        for r in range(line_count + 1):
            yy = table_top + r * row_height
            page.draw_line((col_x[0], yy), (col_x[-1], yy), width=0.6)
        for x in col_x:
            page.draw_line((x, table_top), (x, table_bottom), width=0.6)

        for c, header in enumerate(headers):
            page.insert_text((col_x[c] + 3, table_top + 11), header, fontsize=7)
        for ri, row in enumerate(rows, start=1):
            text_y = table_top + ri * row_height + 11
            for c, value in enumerate(row):
                if value:
                    page.insert_text((col_x[c] + 3, text_y), str(value), fontsize=7)

    return doc.write()


def build_lloyds_double_read_pdf():
    """A statement where one transaction block is emitted twice (a parser
    double-read): the copy is identical on every field including balance_after
    and must be removed. A separate pair shares date / description / type /
    amount but has a different running balance — a legitimate repeat that must
    be kept and only surfaced as a duplicate candidate."""
    import fitz

    def block(date, desc, ttype, money_in, money_out, balance):
        return [
            "Date", date,
            "Description", desc,
            "Type", ttype,
            "Money In (£)", ("blank." if money_in is None else f"{money_in:,.2f}"),
            "Money Out (£)", ("blank." if money_out is None else f"{money_out:,.2f}"),
            "Balance (£)", f"{balance:,.2f}",
        ]

    salary = block("02 Jan 26", "SALARY", "FPI", 1000.00, None, 1173.00)
    lines = [
        "Lloyds Bank plc",
        "Classic statement",
        "Statement period 01 Jan 26 to 31 Jan 26",
        "Money In £1,200.00",
        "Money Out £1,000.00",
        "Balance on 01 January 2026 £173.00",
        "Balance on 31 January 2026 £373.00",
        "Your Transactions",
    ]
    lines += salary  # transaction 1
    lines += salary  # transaction 1 again — a true double-read
    lines += block("03 Jan 26", "RENT PAYMENT", "DD", None, 500.00, 673.00)
    lines += block("03 Jan 26", "RENT PAYMENT", "DD", None, 500.00, 173.00)
    lines += block("04 Jan 26", "REFUND", "FPI", 200.00, None, 373.00)
    lines += ["Transaction types"]

    doc = fitz.open()
    page = doc.new_page(width=520, height=80 + len(lines) * 12 + 60)
    y = 50
    for line in lines:
        page.insert_text((40, y), line, fontsize=8)
        y += 12
    return doc.write()


class LloydsFamilyFixtureTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.fixture_path = Path("tests/fixtures/lloyds_family/sample_statement.pdf")
        self.expected = json.loads(
            Path("tests/fixtures/lloyds_family/expected_output.json").read_text()
        )
        self.halifax_fixture_path = Path("tests/fixtures/lloyds_family/sample_statement_halifax.pdf")
        self.expected_halifax = json.loads(
            Path("tests/fixtures/lloyds_family/expected_output_halifax.json").read_text()
        )
        self.lloyds_2026_fixture_path = Path("tests/fixtures/lloyds_family/Statement_2026_lloyds.pdf")
        self.expected_2026 = json.loads(
            Path("tests/fixtures/lloyds_family/expected_output_2026_lloyds.json").read_text()
        )

    def _post_pdf(self, pdf_bytes):
        files = {
            "file": ("sample_lloyds_statement.pdf", io.BytesIO(pdf_bytes), "application/pdf")
        }
        return self.client.post("/extract-upload", data={"document_id": "fixture-doc"}, files=files)

    def test_lloyds_family_bank_detection(self):
        pdf_bytes = self.fixture_path.read_bytes()
        response = self._post_pdf(pdf_bytes)
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertIn(body["detected_bank"], ["Lloyds Bank", "Halifax", "Bank of Scotland"])
        self.assertGreaterEqual(body["bank_detection_confidence"], self.expected["minimum_detection_confidence"])
        self.assertEqual(body["parser_adapter"], self.expected["parser_adapter"])
        self.assertEqual(body["page_count"], self.expected["page_count"])

    def test_halifax_family_bank_detection(self):
        pdf_bytes = self.halifax_fixture_path.read_bytes()
        response = self._post_pdf(pdf_bytes)
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["detected_bank"], "Halifax")
        self.assertGreaterEqual(body["bank_detection_confidence"], self.expected_halifax["minimum_detection_confidence"])
        self.assertEqual(body["parser_adapter"], self.expected_halifax["parser_adapter"])
        self.assertEqual(body["page_count"], self.expected_halifax["page_count"])

    def test_lloyds_family_extraction_reconciles(self):
        pdf_bytes = self.fixture_path.read_bytes()
        response = self._post_pdf(pdf_bytes)
        body = response.json()

        self.assertEqual(body["transaction_count"], self.expected["transaction_count"])
        self.assertEqual(body["statement"]["opening_balance"], self.expected["opening_balance"])
        self.assertEqual(body["statement"]["closing_balance"], self.expected["closing_balance"])
        self.assertEqual(body["statement"]["total_credits"], self.expected["total_credits"])
        self.assertEqual(body["statement"]["total_debits"], self.expected["total_debits"])
        self.assertEqual(body["statement"]["statement_start_date"], self.expected["statement_start_date"])
        self.assertEqual(body["statement"]["statement_end_date"], self.expected["statement_end_date"])
        self.assertEqual(body["reconciliation"]["status"], self.expected["reconciliation_status"])

        for expected_tx in self.expected["transactions"]:
            self.assertTrue(
                any(
                    tx["transaction_date"] == expected_tx["transaction_date"]
                    and tx["description_raw"] == expected_tx["description_raw"]
                    and abs(abs(tx["amount"]) - expected_tx["amount"]) < 0.01
                    and tx["type"] == expected_tx["type"]
                    and abs(tx["balance_after"] - expected_tx["balance_after"]) < 0.01
                    for tx in body["transactions"]
                ),
                f"Expected transaction not found: {expected_tx}",
            )

    def test_halifax_family_extraction_reconciles(self):
        pdf_bytes = self.halifax_fixture_path.read_bytes()
        response = self._post_pdf(pdf_bytes)
        body = response.json()

        self.assertEqual(body["transaction_count"], self.expected_halifax["transaction_count"])
        self.assertEqual(body["statement"]["opening_balance"], self.expected_halifax["opening_balance"])
        self.assertEqual(body["statement"]["closing_balance"], self.expected_halifax["closing_balance"])
        self.assertEqual(body["statement"]["total_credits"], self.expected_halifax["total_credits"])
        self.assertEqual(body["statement"]["total_debits"], self.expected_halifax["total_debits"])
        self.assertEqual(body["statement"]["statement_start_date"], self.expected_halifax["statement_start_date"])
        self.assertEqual(body["statement"]["statement_end_date"], self.expected_halifax["statement_end_date"])
        self.assertEqual(body["reconciliation"]["status"], self.expected_halifax["reconciliation_status"])

        for expected_tx in self.expected_halifax["transactions"]:
            self.assertTrue(
                any(
                    tx["transaction_date"] == expected_tx["transaction_date"]
                    and tx["description_raw"] == expected_tx["description_raw"]
                    and abs(abs(tx["amount"]) - expected_tx["amount"]) < 0.01
                    and tx["type"] == expected_tx["type"]
                    and abs(tx["balance_after"] - expected_tx["balance_after"]) < 0.01
                    for tx in body["transactions"]
                ),
                f"Expected transaction not found: {expected_tx}",
            )

    def test_lloyds_classic_summary_statement_reconciles(self):
        pdf_bytes = self.lloyds_2026_fixture_path.read_bytes()
        response = self._post_pdf(pdf_bytes)
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["status"], "success")
        self.assertEqual(body["detected_bank"], "Lloyds Bank")
        self.assertEqual(body["parser_adapter"], "lloyds_family_v1")
        self.assertEqual(body["page_count"], 3)
        self.assertEqual(body["transaction_count"], 8)
        self.assertEqual(body["statement"]["bank_name"], "Lloyds")
        self.assertEqual(body["statement"]["opening_balance"], self.expected_2026["opening_balance"])
        self.assertEqual(body["statement"]["closing_balance"], self.expected_2026["closing_balance"])
        self.assertEqual(body["statement"]["total_credits"], self.expected_2026["total_credits"])
        self.assertEqual(body["statement"]["total_debits"], self.expected_2026["total_debits"])
        self.assertEqual(body["reconciliation"]["status"], self.expected_2026["reconciliation_status"])
        self.assertEqual(body["reconciliation"]["calculated_total_credits"], self.expected_2026["total_credits"])
        self.assertEqual(body["reconciliation"]["calculated_total_debits"], self.expected_2026["total_debits"])
        self.assertTrue(body["parser_debug"]["summary_block_found"])
        self.assertEqual(body["parser_debug"]["balance_points_found"][0]["role"], "opening_balance")
        self.assertEqual(body["parser_debug"]["balance_points_found"][1]["role"], "closing_balance")
        self.assertEqual(body["parser_debug"]["transaction_rows_detected"], 8)
        self.assertEqual(body["parser_debug"]["per_page_transaction_counts"], {"1": 3, "2": 3, "3": 2})
        self.assertEqual(body["parser_debug"]["calculated_total_credits"], self.expected_2026["total_credits"])
        self.assertEqual(body["parser_debug"]["calculated_total_debits"], self.expected_2026["total_debits"])
        self.assertIsNotNone(body["parser_debug"]["first_transaction"])
        self.assertIsNotNone(body["parser_debug"]["last_transaction"])

    def test_lloyds_blank_format_transactions_extracted(self):
        pdf_bytes = build_lloyds_blank_format_pdf()
        response = self._post_pdf(pdf_bytes)
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["status"], "success")
        self.assertEqual(body["detected_bank"], "Lloyds Bank")
        self.assertEqual(body["parser_adapter"], "lloyds_family_v1")
        self.assertEqual(body["page_count"], 3)
        self.assertEqual(body["transaction_count"], 8)

        self.assertEqual(body["statement"]["opening_balance"], 173.0)
        self.assertEqual(body["statement"]["closing_balance"], 272.4)
        self.assertEqual(body["statement"]["total_credits"], 6537.0)
        self.assertEqual(body["statement"]["total_debits"], 6437.6)

        recon = body["reconciliation"]
        self.assertEqual(recon["status"], "matched")
        self.assertEqual(recon["calculated_total_credits"], 6537.0)
        self.assertEqual(recon["calculated_total_debits"], 6437.6)

        debug = body["parser_debug"]
        self.assertEqual(debug["adapter_selected"], "lloyds_family_v1")
        self.assertTrue(debug["header_parsed"])
        self.assertEqual(debug["transaction_rows_detected"], 8)
        self.assertEqual(debug["transactions_returned"], 8)
        self.assertEqual(debug["per_page_transaction_counts"], {"1": 3, "2": 3, "3": 2})
        self.assertEqual(debug["calculated_total_credits"], 6537.0)
        self.assertEqual(debug["calculated_total_debits"], 6437.6)
        self.assertIsNotNone(debug["first_transaction"])
        self.assertIsNotNone(debug["last_transaction"])

        by_description = {tx["description_raw"]: tx for tx in body["transactions"]}
        self.assertEqual(len(by_description), 8)

        # Empty Money In cell rendered inline as "blank." -> debit row.
        rochdale = by_description["ROCHDALE MBC"]
        self.assertEqual(rochdale["transaction_date"], "2026-01-02")
        self.assertEqual(rochdale["transaction_type"], "DD")
        self.assertEqual(rochdale["paid_in"], 0.0)
        self.assertEqual(rochdale["paid_out"], 15.4)
        self.assertEqual(rochdale["balance_after"], 1357.6)
        self.assertEqual(rochdale["type"], "debit")

        # Empty Money Out cell rendered inline as "blank." -> credit row,
        # with a comma-grouped amount.
        aidan = by_description["AIDAN SHERWOOD"]
        self.assertEqual(aidan["transaction_date"], "2026-01-01")
        self.assertEqual(aidan["transaction_type"], "FPI")
        self.assertEqual(aidan["paid_in"], 1200.0)
        self.assertEqual(aidan["paid_out"], 0.0)
        self.assertEqual(aidan["balance_after"], 1373.0)
        self.assertEqual(aidan["type"], "credit")

        # Inline "Date 05 Jan 26" row is still detected as a new transaction.
        bonus = by_description["BONUS PAYMENT"]
        self.assertEqual(bonus["transaction_date"], "2026-01-05")
        self.assertEqual(bonus["paid_in"], 1000.0)

        for tx in body["transactions"]:
            self.assertIn(tx["page_number"], (1, 2, 3))
            self.assertGreaterEqual(tx["row_index"], 1)

    def test_lloyds_flat_row_example_rows_parse(self):
        from app.parsers.lloyds import LloydsStatementParser

        parser = LloydsStatementParser()
        cases = [
            ("02 Jan 26 ROCHDALE MBC DD blank 164.00 9.00",
             "2026-01-02", "ROCHDALE MBC", "DD", 0.0, 164.00, 9.00),
            ("02 Jan 26 AIDAN SHERWOOD FPI 295.00 blank 304.00",
             "2026-01-02", "AIDAN SHERWOOD", "FPI", 295.00, 0.0, 304.00),
            ("12 Jan 26 HTEC SOLUTIO LTD FPI 3,000.00 blank 3,009.00",
             "2026-01-12", "HTEC SOLUTIO LTD", "FPI", 3000.00, 0.0, 3009.00),
            ("29 Jan 26 WILLIAMHILL*INTERN DEB blank 50.00 383.00",
             "2026-01-29", "WILLIAMHILL*INTERN", "DEB", 0.0, 50.00, 383.00),
            ("30 Jan 26 THREE MOBILE FPO blank 32.60 297.40",
             "2026-01-30", "THREE MOBILE", "FPO", 0.0, 32.60, 297.40),
        ]
        for line, date, desc, ttype, paid_in, paid_out, balance in cases:
            tx = parser._parse_flat_transaction_row(line, 1, None, None)
            self.assertIsNotNone(tx, f"row not parsed: {line}")
            self.assertEqual(tx["transaction_date"], date)
            self.assertEqual(tx["description_raw"], desc)
            self.assertEqual(tx["transaction_type"], ttype)
            self.assertEqual(tx["paid_in"], paid_in)
            self.assertEqual(tx["paid_out"], paid_out)
            self.assertEqual(tx["balance_after"], balance)

    def test_lloyds_flat_table_extraction_reconciles(self):
        pdf_bytes = build_lloyds_flat_table_pdf()
        response = self._post_pdf(pdf_bytes)
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["status"], "success")
        self.assertEqual(body["detected_bank"], "Lloyds Bank")
        self.assertEqual(body["parser_adapter"], "lloyds_family_v1")
        self.assertEqual(body["page_count"], 3)
        self.assertEqual(body["transaction_count"], 48)

        self.assertEqual(body["statement"]["opening_balance"], 173.0)
        self.assertEqual(body["statement"]["closing_balance"], 272.4)
        self.assertEqual(body["statement"]["total_credits"], 6537.0)
        self.assertEqual(body["statement"]["total_debits"], 6437.6)

        recon = body["reconciliation"]
        self.assertEqual(recon["status"], "matched")
        self.assertEqual(recon["calculated_total_credits"], 6537.0)
        self.assertEqual(recon["calculated_total_debits"], 6437.6)

        debug = body["parser_debug"]
        self.assertEqual(debug["adapter_selected"], "lloyds_family_v1")
        self.assertTrue(debug["header_parsed"])
        self.assertTrue(debug["transaction_parser_called"])
        self.assertEqual(debug["your_transactions_sections_found"], 1)
        self.assertEqual(debug["date_matches_found"], 48)
        self.assertEqual(debug["type_matches_found"], 48)
        self.assertEqual(debug["candidate_transaction_blocks"], 48)
        self.assertEqual(debug["transactions_returned"], 48)
        self.assertEqual(debug["per_page_transaction_counts"], {"1": 16, "2": 16, "3": 16})
        self.assertEqual(len(debug["first_5_date_matches"]), 5)
        self.assertEqual(len(debug["first_5_candidate_blocks"]), 5)
        self.assertIsNotNone(debug["first_transaction"])
        self.assertIsNotNone(debug["last_transaction"])

        first = body["transactions"][0]
        self.assertEqual(first["description_raw"], "HTEC SOLUTIO LTD")
        self.assertEqual(first["transaction_type"], "FPI")
        self.assertEqual(first["paid_in"], 3000.0)
        self.assertEqual(first["paid_out"], 0.0)
        self.assertEqual(first["type"], "credit")
        self.assertEqual(first["page_number"], 1)
        self.assertEqual(first["row_index"], 1)

        second = body["transactions"][1]
        self.assertEqual(second["transaction_type"], "DD")
        self.assertEqual(second["paid_out"], 1200.0)
        self.assertEqual(second["type"], "debit")

    def test_lloyds_real_classic_statement_reconciles(self):
        pdf_bytes = build_lloyds_real_classic_pdf()
        response = self._post_pdf(pdf_bytes)
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["status"], "success")
        self.assertEqual(body["detected_bank"], "Lloyds Bank")
        self.assertEqual(body["parser_adapter"], "lloyds_family_v1")
        self.assertEqual(body["page_count"], 3)
        # The real statement has 55 transaction rows (not 48).
        self.assertEqual(body["transaction_count"], 55)

        self.assertEqual(body["statement"]["opening_balance"], 173.0)
        self.assertEqual(body["statement"]["closing_balance"], 272.4)
        self.assertEqual(body["statement"]["total_credits"], 6537.0)
        self.assertEqual(body["statement"]["total_debits"], 6437.6)

        recon = body["reconciliation"]
        self.assertEqual(recon["status"], "matched")
        self.assertEqual(recon["calculated_total_credits"], 6537.0)
        self.assertEqual(recon["calculated_total_debits"], 6437.6)

        debug = body["parser_debug"]
        self.assertTrue(debug["transaction_parser_called"])
        self.assertTrue(debug["header_parsed"])
        self.assertEqual(debug["transactions_returned"], 55)
        self.assertEqual(debug["per_page_transaction_counts"], {"1": 16, "2": 22, "3": 17})
        self.assertEqual(debug["date_matches_found"], 55)
        self.assertEqual(debug["type_matches_found"], 55)

        # The two 29 Jan WILLIAMHILL*INTERN £50.00 rows share date / description
        # / type / amount but differ in balance_after -> a legitimate repeat,
        # not a true duplicate: kept, and only surfaced as a candidate.
        self.assertEqual(debug["duplicate_transaction_count"], 0)
        candidate_keys = [c["key"] for c in debug["duplicate_candidates"]]
        self.assertIn("2026-01-29 | WILLIAMHILL*INTERN | DEB | 0.0 | 50.0", candidate_keys)
        for tx in body["transactions"]:
            self.assertEqual(tx["parser_adapter"], "lloyds_family_v1")

        by_desc = {}
        for tx in body["transactions"]:
            by_desc.setdefault(tx["description_raw"], []).append(tx)

        rochdale = by_desc["ROCHDALE MBC"][0]
        self.assertEqual(rochdale["transaction_date"], "2026-01-02")
        self.assertEqual(rochdale["transaction_type"], "DD")
        self.assertEqual(rochdale["paid_in"], 0.0)
        self.assertEqual(rochdale["paid_out"], 164.0)
        self.assertEqual(rochdale["balance_after"], 9.0)
        self.assertEqual(rochdale["type"], "debit")

        htec = by_desc["HTEC SOLUTIO LTD"][0]
        self.assertEqual(htec["transaction_type"], "FPI")
        self.assertEqual(htec["paid_in"], 3000.0)
        self.assertEqual(htec["paid_out"], 0.0)
        self.assertEqual(htec["balance_after"], 3009.0)
        self.assertEqual(htec["type"], "credit")

        three = by_desc["THREE MOBILE"][0]
        self.assertEqual(three["paid_out"], 32.6)
        self.assertEqual(three["transaction_type"], "FPO")

        # trailing full stop dropped, internal dots preserved
        self.assertIn("P.O. G9 MIDDLETON", by_desc)
        # a zero-balance row is still captured
        self.assertTrue(any(tx["balance_after"] == 0.0 for tx in body["transactions"]))

    def test_lloyds_merged_blocks_extraction_reconciles(self):
        pdf_bytes = build_lloyds_merged_blocks_pdf()
        response = self._post_pdf(pdf_bytes)
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["status"], "success")
        self.assertEqual(body["detected_bank"], "Lloyds Bank")
        self.assertEqual(body["parser_adapter"], "lloyds_family_v1")
        self.assertEqual(body["page_count"], 3)
        self.assertEqual(body["transaction_count"], 55)

        self.assertEqual(body["statement"]["opening_balance"], 173.0)
        self.assertEqual(body["statement"]["closing_balance"], 272.4)
        self.assertEqual(body["statement"]["total_credits"], 6537.0)
        self.assertEqual(body["statement"]["total_debits"], 6437.6)

        recon = body["reconciliation"]
        self.assertEqual(recon["status"], "matched")
        self.assertEqual(recon["calculated_total_credits"], 6537.0)
        self.assertEqual(recon["calculated_total_debits"], 6437.6)

        debug = body["parser_debug"]
        self.assertTrue(debug["transaction_parser_called"])
        self.assertEqual(debug["transactions_returned"], 55)
        self.assertEqual(debug["date_matches_found"], 55)
        self.assertEqual(debug["type_matches_found"], 55)
        self.assertEqual(debug["per_page_transaction_counts"], {"1": 16, "2": 22, "3": 17})

        by_desc = {}
        for tx in body["transactions"]:
            by_desc.setdefault(tx["description_raw"], []).append(tx)

        # Debit row: "Type" line is "DD Money In (£) blank." in the text layer.
        rochdale = by_desc["ROCHDALE MBC"][0]
        self.assertEqual(rochdale["transaction_type"], "DD")
        self.assertEqual(rochdale["paid_in"], 0.0)
        self.assertEqual(rochdale["paid_out"], 164.0)
        self.assertEqual(rochdale["balance_after"], 9.0)

        # Credit row: money-in value line is "295.00 Money Out (£) blank.".
        aidan = by_desc["AIDAN SHERWOOD"][0]
        self.assertEqual(aidan["transaction_type"], "FPI")
        self.assertEqual(aidan["paid_in"], 295.0)
        self.assertEqual(aidan["paid_out"], 0.0)
        self.assertEqual(aidan["balance_after"], 304.0)

        htec = by_desc["HTEC SOLUTIO LTD"][0]
        self.assertEqual(htec["paid_in"], 3000.0)

    def test_lloyds_ruled_table_extraction_reconciles(self):
        pdf_bytes = build_lloyds_ruled_table_pdf()
        response = self._post_pdf(pdf_bytes)
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["status"], "success")
        self.assertEqual(body["detected_bank"], "Lloyds Bank")
        self.assertEqual(body["parser_adapter"], "lloyds_family_v1")
        self.assertEqual(body["page_count"], 3)
        self.assertEqual(body["transaction_count"], 55)

        self.assertEqual(body["statement"]["opening_balance"], 173.0)
        self.assertEqual(body["statement"]["closing_balance"], 272.4)
        self.assertEqual(body["statement"]["total_credits"], 6537.0)
        self.assertEqual(body["statement"]["total_debits"], 6437.6)

        recon = body["reconciliation"]
        self.assertEqual(recon["status"], "matched")
        self.assertEqual(recon["calculated_total_credits"], 6537.0)
        self.assertEqual(recon["calculated_total_debits"], 6437.6)

        debug = body["parser_debug"]
        self.assertTrue(debug["transaction_parser_called"])
        self.assertTrue(debug["table_extraction_used"])
        self.assertGreaterEqual(debug["tables_detected"], 3)
        self.assertEqual(debug["transactions_returned"], 55)
        self.assertEqual(debug["per_page_transaction_counts"], {"1": 16, "2": 22, "3": 17})

        by_desc = {}
        for tx in body["transactions"]:
            by_desc.setdefault(tx["description_raw"], []).append(tx)

        rochdale = by_desc["ROCHDALE MBC"][0]
        self.assertEqual(rochdale["transaction_date"], "2026-01-02")
        self.assertEqual(rochdale["transaction_type"], "DD")
        self.assertEqual(rochdale["paid_in"], 0.0)
        self.assertEqual(rochdale["paid_out"], 164.0)
        self.assertEqual(rochdale["balance_after"], 9.0)

        htec = by_desc["HTEC SOLUTIO LTD"][0]
        self.assertEqual(htec["paid_in"], 3000.0)
        self.assertEqual(htec["transaction_type"], "FPI")

    def test_lloyds_double_read_is_deduplicated(self):
        pdf_bytes = build_lloyds_double_read_pdf()
        response = self._post_pdf(pdf_bytes)
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["status"], "success")
        # Five rows parsed; the duplicated SALARY block is removed -> four.
        self.assertEqual(body["transaction_count"], 4)
        self.assertEqual(body["reconciliation"]["status"], "matched")
        self.assertEqual(body["reconciliation"]["calculated_total_credits"], 1200.0)
        self.assertEqual(body["reconciliation"]["calculated_total_debits"], 1000.0)

        debug = body["parser_debug"]
        self.assertEqual(debug["duplicate_transaction_count"], 1)

        # The kept RENT PAYMENT pair (same amount, different balance) is the
        # only remaining duplicate candidate.
        self.assertEqual(len(debug["duplicate_candidates"]), 1)
        candidate = debug["duplicate_candidates"][0]
        self.assertEqual(len(candidate["rows"]), 2)
        self.assertEqual(
            sorted(row["balance_after"] for row in candidate["rows"]),
            [173.0, 673.0],
        )

        # No surviving row is an exact duplicate of another.
        identities = [
            (
                tx["transaction_date"],
                tx["description_raw"],
                tx["transaction_type"],
                tx["paid_in"],
                tx["paid_out"],
                tx["balance_after"],
            )
            for tx in body["transactions"]
        ]
        self.assertEqual(len(identities), len(set(identities)))

    def _assert_protected_parser_fixture(self, body, *, detected_bank, parser_adapter, transaction_count):
        """Shared assertions for a protected parser fixture (Santander, Lloyds).

        Returns the reconciliation block for any fixture-specific follow-up
        checks.
        """
        # correct bank detection and adapter selection
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["detected_bank"], detected_bank)
        self.assertEqual(body["parser_adapter"], parser_adapter)

        # non-empty transactions[], frozen transaction count
        transactions = body["transactions"]
        self.assertGreater(len(transactions), 0, f"{parser_adapter}: transactions[] is empty")
        self.assertEqual(body["transaction_count"], transaction_count)
        self.assertEqual(len(transactions), transaction_count)

        # every row carries the core fields
        for tx in transactions:
            for field in ("transaction_date", "description_raw", "paid_in", "paid_out", "balance_after"):
                self.assertIn(field, tx)
                self.assertIsNotNone(tx[field], f"{parser_adapter}: {field} missing on {tx}")

        # no two transactions share a full identity key
        identities = [
            (
                tx["transaction_date"],
                tx["description_raw"],
                tx.get("transaction_type"),
                round(float(tx["paid_in"]), 2),
                round(float(tx["paid_out"]), 2),
                round(float(tx["balance_after"]), 2),
            )
            for tx in transactions
        ]
        self.assertEqual(
            len(identities), len(set(identities)),
            f"{parser_adapter}: duplicate transaction identity key",
        )

        # reconciliation: matched, and calculated totals == statement totals
        recon = body["reconciliation"]
        self.assertEqual(recon["status"], "matched")
        self.assertEqual(recon["calculated_total_credits"], recon["statement_total_credits"])
        self.assertEqual(recon["calculated_total_debits"], recon["statement_total_debits"])

        # calculated totals are exactly the sum of the rows
        self.assertEqual(
            round(sum(float(tx["paid_in"]) for tx in transactions), 2),
            recon["calculated_total_credits"],
        )
        self.assertEqual(
            round(sum(float(tx["paid_out"]) for tx in transactions), 2),
            recon["calculated_total_debits"],
        )

        # opening + credits - debits == closing
        opening = recon.get("opening_balance")
        if opening is None:
            opening = recon.get("derived_opening_balance")
        self.assertIsNotNone(opening, f"{parser_adapter}: no opening balance available")
        self.assertAlmostEqual(
            round(opening + recon["calculated_total_credits"] - recon["calculated_total_debits"], 2),
            round(recon["closing_balance"], 2),
            places=2,
        )
        return recon

    def test_protected_fixture_santander(self):
        """Protected Santander fixture — guards the santander_v1 adapter."""
        response = self._post_pdf(build_santander_statement_pdf())
        self.assertEqual(response.status_code, 200)
        body = response.json()

        recon = self._assert_protected_parser_fixture(
            body,
            detected_bank="Santander",
            parser_adapter="santander_v1",
            transaction_count=256,
        )
        self.assertEqual(recon["calculated_total_credits"], 8092.75)
        self.assertEqual(recon["calculated_total_debits"], 8273.18)
        self.assertEqual(recon["closing_balance"], 98.10)

    def test_lloyds_phase1_acceptance_regression(self):
        """Frozen Phase 1 acceptance result for the real Lloyds 'Classic'
        Statement_2026_lloyds.pdf (55-transaction statement) — the protected
        Lloyds fixture.

        Fails if transaction_count drifts, the calculated totals stop matching
        the statement totals, reconciliation is not "matched", a duplicate is
        reported, the wrong adapter is used, or transactions[] is empty.
        """
        expected = json.loads(
            Path("tests/fixtures/lloyds_family/expected_output_lloyds_acceptance.json").read_text()
        )

        response = self._post_pdf(build_lloyds_merged_blocks_pdf())
        self.assertEqual(response.status_code, 200)
        body = response.json()

        recon = self._assert_protected_parser_fixture(
            body,
            detected_bank=expected["detected_bank"],
            parser_adapter=expected["parser_adapter"],
            transaction_count=expected["transaction_count"],
        )

        # frozen statement header / total values
        self.assertEqual(body["statement"]["opening_balance"], expected["opening_balance"])
        self.assertEqual(body["statement"]["closing_balance"], expected["closing_balance"])
        self.assertEqual(body["statement"]["total_credits"], expected["statement_total_credits"])
        self.assertEqual(body["statement"]["total_debits"], expected["statement_total_debits"])
        self.assertEqual(recon["calculated_total_credits"], expected["calculated_total_credits"])
        self.assertEqual(recon["calculated_total_debits"], expected["calculated_total_debits"])

        # adapter confirmation + duplicate diagnostics
        self.assertEqual(body["parser_debug"]["adapter_selected"], "lloyds_family_v1")
        self.assertEqual(body["parser_debug"]["parser_adapter"], "lloyds_family_v1")
        self.assertTrue(body["parser_debug"]["transaction_parser_called"])
        self.assertEqual(
            body["parser_debug"]["duplicate_transaction_count"],
            expected["duplicate_transaction_count"],
        )

    def test_lloyds_post_import_pipeline_contract(self):
        """Post-import pipeline regression note for the Lloyds fixture.

        The summary, categorisation and risk LOGIC lives in Base44, not in this
        parser repo. This test locks the PARSER OUTPUT the pipeline consumes so
        a future parser change cannot feed the pipeline the inputs that caused
        the Phase 2 bug (total_income = 0, total_spending = 99.40, and a false
        Negative Net Cashflow flag).

        Pipeline mapping:
            total_income   <- statement.total_credits
            total_spending <- statement.total_debits
            net_movement   =  total_credits - total_debits

        Risk expectations the parser output must support:
            * Negative Net Cashflow must NOT fire — net_movement is +99.40.
            * Gambling Transactions may fire — 5 rows totalling 360.00.
            * High Discretionary Spend is computed against total_spending
              (6437.60), never the net movement.
            * Review concentration is a Base44 concern (AI categories) and is
              not asserted here.
        """
        expected = json.loads(
            Path("tests/fixtures/lloyds_family/expected_pipeline_lloyds.json").read_text()
        )

        response = self._post_pdf(build_lloyds_merged_blocks_pdf())
        self.assertEqual(response.status_code, 200)
        body = response.json()

        statement = body["statement"]
        total_income = statement["total_credits"]
        total_spending = statement["total_debits"]
        net_movement = round(total_income - total_spending, 2)

        # --- summary values the pipeline derives from the parser output ---
        self.assertEqual(total_income, expected["total_income"])
        self.assertEqual(total_spending, expected["total_spending"])
        self.assertEqual(net_movement, expected["net_movement"])
        self.assertEqual(statement["opening_balance"], expected["opening_balance"])
        self.assertEqual(statement["closing_balance"], expected["closing_balance"])
        self.assertEqual(body["reconciliation"]["status"], expected["reconciliation_status"])

        # --- regression guards: the Phase 2 bug surfaced as income 0 /
        #     spending 99.40 (the net movement misread as spending) ---
        self.assertNotEqual(total_income, 0, "total_income must not regress to 0")
        self.assertNotEqual(total_spending, 99.40, "total_spending must not be the net movement")
        self.assertEqual(total_spending, 6437.60)
        self.assertGreater(
            net_movement, 0,
            "net movement is positive — Negative Net Cashflow must not be raised",
        )

        # --- gambling transactions must be present for the risk rule ---
        gambling_markers = ("WILLIAMHILL", "KALOOKI")
        gambling = [
            tx for tx in body["transactions"]
            if any(marker in (tx["description_raw"] or "").upper() for marker in gambling_markers)
        ]
        self.assertEqual(len(gambling), expected["gambling_transaction_count"])
        self.assertEqual(
            round(sum(float(tx["paid_out"]) for tx in gambling), 2),
            expected["gambling_transaction_total"],
        )

    def test_health_includes_available_adapters(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["status"], "ok")
        self.assertIsInstance(body.get("available_adapters"), list)
        self.assertIn("santander_v1", body["available_adapters"])
        self.assertIn("lloyds_family_v1", body["available_adapters"])

    def test_health_and_version_expose_deploy_identity(self):
        for path in ("/health", "/version"):
            body = self.client.get(path).json()
            self.assertEqual(body["service_name"], "fastdox-bank-parser")
            self.assertEqual(body["parser_version"], "fastdox_parser_v1.1.0")
            self.assertEqual(body["adapter_versions"]["lloyds_family_v1"], "1.0.1")
            self.assertEqual(body["adapter_versions"]["santander_v1"], "1.0.0")
            self.assertIn("git_commit", body)

        root = self.client.get("/")
        self.assertEqual(root.status_code, 200)
        self.assertIn("/extract-upload", root.json()["endpoints"])

    def test_santander_regression_still_passes(self):
        pdf_bytes = build_santander_statement_pdf()
        response = self._post_pdf(pdf_bytes)
        body = response.json()

        self.assertEqual(body["status"], "success")
        self.assertEqual(body["detected_bank"], "Santander")
        self.assertGreaterEqual(body["bank_detection_confidence"], 0.95)
        self.assertEqual(body["parser_adapter"], "santander_v1")
        self.assertEqual(body["transaction_count"], 256)
        self.assertEqual(body["reconciliation"]["status"], "matched")
        self.assertEqual(body["statement"]["total_credits"], 8092.75)
        self.assertEqual(body["statement"]["total_debits"], 8273.18)
        self.assertEqual(body["statement"]["closing_balance"], 98.10)


if __name__ == "__main__":
    unittest.main()
