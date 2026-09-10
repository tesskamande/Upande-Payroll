"""Checks for Bank File Format and the file it builds.

Run with:

    bench --site <site> execute upande_payroll.tests.bank_file_checks.run

Everything is rolled back at the end, so it is safe against a working site.

The shaping is checked directly - a title line, a heading row, a composed
clearing code, a constant, a trailer that counts and totals - and the record's
own guards are checked by trying to save something wrong. What is not checked
here is any one bank's template: those live in Bank File Format records, and a
record is data, not behaviour.
"""

import frappe
from frappe.utils import flt

from upande_payroll.bank_file import _cell, _render, build

RESULTS = []


def check(name, got, want):
    RESULTS.append((got == want, name, got, want))


def refuses(name, fn):
    frappe.db.savepoint("bank_file_check")
    try:
        fn()
        RESULTS.append((False, name, "accepted", "rejected"))
    except frappe.ValidationError:
        RESULTS.append((True, name, "rejected", "rejected"))
    except Exception as e:
        RESULTS.append((False, name, f"{type(e).__name__}: {e}", "ValidationError"))
    frappe.db.rollback(save_point="bank_file_check")


def _col(**kw):
    base = {"section": "Detail", "row": 1, "source": "Field",
            "column_label": None, "value": None}
    base.update(kw)
    return frappe._dict(base)


def _fmt(**kw):
    base = {
        "bank": "ZZ File Bank", "company": "Karen Roses", "layout": "Flat Table",
        "title_row": None, "include_header_row": 1, "columns": [],
    }
    base.update(kw)
    return frappe._dict(base)


CONTEXT = {
    "to_date_ddmmyyyy": "31082027",
    "bank": "ZZ File Bank", "company": "Karen Roses", "period": "Aug 2027",
    "debit_account": "1100000000123", "debit_bank_code": "01",
    "debit_branch_code": "100", "count": 2, "total": 300.0,
    "from_date": "2027-08-01", "to_date": "2027-08-31", "today": "2027-09-01",
    "swift": "ZZZZKENX",
}

ROWS = [
    frappe._dict(payroll_number="1001", employee_name="A One", account_number="111",
                 bank_code="01", branch_code="100", amount=100.0),
    frappe._dict(payroll_number="1002", employee_name="B Two", account_number="222",
                 bank_code="01", branch_code="103", amount=200.0),
]


def run():
    # --- placeholders -----------------------------------------------------
    check("a placeholder is replaced",
          _render("TITLE:{company} FOR {period}", CONTEXT), "TITLE:Karen Roses FOR Aug 2027")
    check("an unknown placeholder is left alone, not blanked",
          _render("{nonsense}", CONTEXT), "{nonsense}")

    # --- cell sources -----------------------------------------------------
    check("a Field cell reads the report row",
          _cell(_col(source="Field", value="account_number"), ROWS[0], CONTEXT), "111")
    check("a Composed cell joins the codes into a clearing code",
          _cell(_col(source="Composed", value="bank_code+branch_code"), ROWS[1], CONTEXT), "01103")
    check("a Constant cell is the same on every row",
          _cell(_col(source="Constant", value="KAREN ROSES LTD"), ROWS[0], CONTEXT),
          "KAREN ROSES LTD")
    check("a Constant may draw on the paying account",
          _cell(_col(source="Constant", value="{debit_account}"), None, CONTEXT), "1100000000123")
    check("Row Count counts the lines being sent",
          _cell(_col(source="Row Count"), None, CONTEXT), 2)
    check("Amount Total totals them", _cell(_col(source="Amount Total"), None, CONTEXT), 300.0)
    check("a Field with no value on the row is blank, not the word None",
          _cell(_col(source="Field", value="not_a_column"), ROWS[0], CONTEXT), "")

    # --- a flat table -----------------------------------------------------
    flat = _fmt(
        title_row="TITLE:{company}  SALARY FOR {period}",
        columns=[
            _col(column_label="PAYROLL CODE", value="payroll_number"),
            _col(column_label="CLEARING CODE", source="Composed", value="bank_code+branch_code"),
            _col(column_label="AMOUNT", value="amount"),
        ],
    )
    data = build(flat, ROWS, CONTEXT)
    check("a flat table is title, headings, then a row each", len(data), 4)
    check("the title is on its own line", data[0], ["TITLE:Karen Roses  SALARY FOR Aug 2027"])
    check("the headings come from the Detail columns",
          data[1], ["PAYROLL CODE", "CLEARING CODE", "AMOUNT"])
    check("the first employee line is in column order", data[2], ["1001", "01100", 100.0])

    # --- record types -----------------------------------------------------
    records = _fmt(
        layout="Record Types",
        include_header_row=1,
        columns=[
            _col(section="Header", source="Constant", value="1"),
            _col(section="Header", source="Constant", value="{debit_account}"),
            _col(section="Detail", source="Constant", value="3"),
            _col(section="Detail", value="account_number"),
            _col(section="Trailer", source="Constant", value="9"),
            _col(section="Trailer", source="Row Count"),
            _col(section="Trailer", source="Amount Total"),
        ],
    )
    data = build(records, ROWS, CONTEXT)
    check("a record type file is header, a line each, then trailer", len(data), 4)
    check("the header record is written once", data[0], ["1", "1100000000123"])
    check("no heading row is written even when the box is ticked",
          data[1], ["3", "111"])
    check("the trailer carries the count and the total", data[3], ["9", 2, 300.0])

    # --- a blank column is kept, not dropped ------------------------------
    # Absa's employee record has an unused column between the currency and the
    # name. Dropping it would shift every column after it by one.
    check("a Blank cell writes an empty cell",
          _cell(_col(source="Blank"), ROWS[0], CONTEXT), None)
    blanked = build(
        _fmt(include_header_row=0, layout="Record Types", columns=[
            _col(source="Constant", value="3"),
            _col(source="Blank"),
            _col(value="employee_name"),
        ]),
        ROWS[:1], CONTEXT)
    check("the blank holds its place between its neighbours",
          blanked[0], ["3", None, "A One"])

    # --- two header records -----------------------------------------------
    two = _fmt(layout="Record Types", include_header_row=0, columns=[
        _col(section="Header", row=1, source="Constant", value="1"),
        _col(section="Header", row=1, source="Constant", value="LOCAL"),
        _col(section="Header", row=2, source="Constant", value="2"),
        _col(section="Header", row=2, source="Constant", value="{debit_account}"),
        _col(section="Detail", row=1, value="account_number"),
    ])
    data = build(two, ROWS, CONTEXT)
    check("each header row is written as its own line", data[0], ["1", "LOCAL"])
    check("the second header row follows it", data[1], ["2", "1100000000123"])
    check("the employees come after both headers", data[2], ["111"])
    check("two headers and two employees make four lines", len(data), 4)

    # --- two lines per employee -------------------------------------------
    paired = _fmt(layout="Record Types", include_header_row=0, columns=[
        _col(row=1, source="Constant", value="A"),
        _col(row=2, source="Constant", value="B"),
    ])
    check("a Detail row 2 writes a second line for each employee",
          build(paired, ROWS, CONTEXT), [["A"], ["B"], ["A"], ["B"]])

    # --- a record type file has to agree with itself ----------------------
    # Absa states the batch total on its header record, the amount on every
    # employee record and the total again on its trailer. A bank rejects the
    # file when those three disagree, so the three come from the same place.
    absa_like = _fmt(layout="Record Types", include_header_row=0, columns=[
        _col(section="Header", row=2, source="Constant", value="2"),
        _col(section="Header", row=2, source="Amount Total"),
        _col(section="Detail", row=1, source="Constant", value="3"),
        _col(section="Detail", row=1, value="amount"),
        _col(section="Trailer", row=1, source="Constant", value="9"),
        _col(section="Trailer", row=1, source="Row Count"),
        _col(section="Trailer", row=1, source="Amount Total"),
    ])
    data = build(absa_like, ROWS, CONTEXT)
    batch_total = data[0][1]
    detail_sum = flt(sum(flt(r[1]) for r in data[1:-1]), 2)
    trailer_count, trailer_total = data[-1][1], data[-1][2]
    check("the batch total equals the sum of the employee lines",
          flt(batch_total, 2), detail_sum)
    check("the trailer total equals it too", flt(trailer_total, 2), detail_sum)
    check("the trailer count equals the number of employee lines",
          trailer_count, len(data) - 2)

    # --- the date form banks ask for --------------------------------------
    check("a date can be asked for without separators",
          _cell(_col(source="Constant", value="{to_date_ddmmyyyy}"), None, CONTEXT), "31082027")

    # --- a cut-short preview still speaks for the whole file --------------
    # The trailer is built from the context, and the context counts every
    # sendable line. Were it counted off the shown lines instead, a preview of
    # the first page would announce a count and a total the file does not have.
    data = build(records, ROWS[:1], CONTEXT)
    check("a preview showing one line still trailers the whole file",
          data[-1], ["9", 2, 300.0])
    check("only the shown line is written as detail", len(data), 3)

    # --- the record's own guards ------------------------------------------
    if not frappe.db.exists("Bank", "ZZ File Bank"):
        frappe.get_doc({"doctype": "Bank", "bank_name": "ZZ File Bank"}).insert()

    def save(**kw):
        base = {
            "doctype": "Bank File Format", "format_name": "ZZ Check Format",
            "bank": "ZZ File Bank", "company": "Karen Roses", "layout": "Flat Table",
            "columns": [{"section": "Detail", "source": "Field", "value": "amount"}],
        }
        base.update(kw)
        frappe.get_doc(base).insert()

    refuses("a format with no Detail column is refused",
            lambda: save(columns=[{"section": "Header", "source": "Constant", "value": "1"}]))
    refuses("a Field column with no value is refused",
            lambda: save(columns=[{"section": "Detail", "source": "Field", "value": ""}]))
    refuses("a paying account held at another bank is refused",
            lambda: save(debit_bank_account="KCB Salaries - KCB"))

    _report()
    frappe.db.rollback()


def _report():
    failed = [r for r in RESULTS if not r[0]]
    for ok, name, got, want in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            print(f"          got {got!r}, wanted {want!r}")
    print(f"\n  {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
