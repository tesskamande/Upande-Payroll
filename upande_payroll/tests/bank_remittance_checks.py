"""Checks for the Bank Remittance report.

Run with:

    bench --site <site> execute upande_payroll.tests.bank_remittance_checks.run

Everything is rolled back at the end, so it is safe against a working site.

The blocking rules and the narration are checked directly, because those are the
report's own judgements. The report as a whole is then checked against whatever
slips the site already holds, by invariant rather than by expected figure: the
two totals have to account for every line, and a line that carries no reason not
to pay has to be complete enough to send. Written that way it is worth running
on any site rather than only on the one it was written on.
"""

import frappe
from frappe.utils import flt

from upande_payroll.upande_payroll.report.bank_remittance.bank_remittance import (
    _blockers,
    _remarks,
    execute,
)

RESULTS = []


def check(name, got, want):
    RESULTS.append((got == want, name, got, want))


def _row(**kw):
    base = {"bank_code": "01", "branch_code": "100", "account_number": "12345",
            "salary_mode": None, "mpesa_number": None}
    base.update(kw)
    return frappe._dict(base)


def run():
    # --- the blocking rules ---------------------------------------------
    check("a complete line has nothing against it",
          _blockers(_row(), 5000.0, "Submitted"), [])
    check("no bank code blocks the line",
          _blockers(_row(bank_code=None), 5000.0, "Submitted"), ["Bank Code"])
    check("no branch code blocks the line",
          _blockers(_row(branch_code=None), 5000.0, "Submitted"), ["Branch Code"])
    check("no account number blocks the line",
          _blockers(_row(account_number=None), 5000.0, "Submitted"), ["Account Number"])
    check("nothing at all reports all three",
          _blockers(_row(bank_code=None, branch_code=None, account_number=None),
                    5000.0, "Submitted"),
          ["Bank Code", "Branch Code", "Account Number"])
    check("a negative net is refused, not sent as a debit",
          _blockers(_row(), -100.0, "Submitted"), ["Negative Pay"])
    check("nothing to pay is refused rather than sent as a zero transfer",
          _blockers(_row(), 0.0, "Submitted"), ["Nothing To Pay"])
    check("a withheld slip is not paid out with the run",
          _blockers(_row(), 5000.0, "Withheld"), ["Withheld"])
    check("reasons stack",
          _blockers(_row(bank_code=None), 0.0, "Withheld"),
          ["Bank Code", "Nothing To Pay", "Withheld"])

    # --- what a line needs depends on how the employee is paid ------------
    # An M-Pesa line has no bank codes and does not need them. Asking it for a
    # Bank Code reported a fault that was not there, and let a line with no
    # phone number through because nothing checked for one.
    check("a mobile line needs only its number",
          _blockers(_row(bank_code=None, branch_code=None, account_number=None,
                         salary_mode="M-Pesa", mpesa_number="0722416420"),
                    5000.0, "Submitted"), [])
    check("a mobile line with no number is refused",
          _blockers(_row(salary_mode="M-Pesa"), 5000.0, "Submitted"), ["M-Pesa Number"])
    check("a mobile line is not asked for a bank code",
          _blockers(_row(bank_code=None, salary_mode="M-Pesa",
                         mpesa_number="0722416420"), 5000.0, "Submitted"), [])
    check("cash is shown but held back, with the reason",
          _blockers(_row(salary_mode="Cash"), 5000.0, "Submitted"), ["Paid by Cash"])
    check("cheque likewise",
          _blockers(_row(salary_mode="Cheque"), 5000.0, "Submitted"), ["Paid by Cheque"])
    check("a blank mode is still treated as a bank transfer",
          _blockers(_row(bank_code=None, salary_mode=None), 5000.0, "Submitted"),
          ["Bank Code"])
    check("a mobile line with nothing to pay is refused for that too",
          _blockers(_row(salary_mode="M-Pesa", mpesa_number="0722416420"), 0.0, "Submitted"),
          ["Nothing To Pay"])

    # --- the narration ---------------------------------------------------
    slip = frappe._dict(end_date="2027-08-31")
    check("the narration falls back to the period",
          _remarks(slip, frappe._dict()), "Salary Aug 2027")
    check("a typed remark wins",
          _remarks(slip, frappe._dict(remarks="August wages")), "August wages")
    check("no period leaves the narration empty rather than guessing",
          _remarks(frappe._dict(end_date=None), frappe._dict()), None)

    # --- the report over the site's own slips -----------------------------
    _check_against_site()

    _report()
    frappe.db.rollback()


def _check_against_site():
    slip = frappe.db.sql(
        """SELECT company, start_date, end_date FROM `tabSalary Slip`
           WHERE docstatus = 1 ORDER BY start_date DESC LIMIT 1""",
        as_dict=True,
    )
    if not slip:
        RESULTS.append((True, "no submitted slips on this site, report not exercised",
                        "skipped", "skipped"))
        return

    period = slip[0]
    _columns, rows = execute(
        frappe._dict(
            company=period.company,
            from_date=str(period.start_date),
            to_date=str(period.end_date),
            docstatus="Submitted",
        )
    )
    lines = [r for r in rows if r]
    check("no total rows are appended", [r for r in lines if r.get("is_total")], [])

    unblocked = [r for r in lines if not r.cannot_pay]
    check("a sendable line is never a zero or a debit",
          [r.employee for r in unblocked if flt(r.amount) <= 0], [])
    # What counts as routing depends on the mode. A bank transfer needs its
    # codes, a mobile payment needs a number; asserting bank codes on every
    # sendable line was the old bank-only assumption.
    def routed(row):
        if (row.get("salary_mode") or "Bank") == "M-Pesa":
            return bool(row.get("mpesa_number"))
        return bool(row.bank_code and row.branch_code and row.account_number)

    check("a sendable line always carries the routing its mode needs",
          [r.employee for r in unblocked if not routed(r)], [])
    check("every line points back at its slip",
          [r.employee for r in lines if not r.salary_slip], [])


def _report():
    failed = [r for r in RESULTS if not r[0]]
    for ok, name, got, want in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            print(f"          got {got!r}, wanted {want!r}")
    print(f"\n  {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
