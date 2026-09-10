"""Checks that every statutory return reports the same gross pay.

Run with:

    bench --site <site> execute upande_payroll.tests.statutory_gross_checks.run

Nothing is written, so this is safe against a working site.

The rule being checked is one sentence: gross pay on a statutory return is the
payslip's gross less whatever the company has mapped to Absence / Unpaid
Deduction. Six reports have to obey it, and for a long time the P9 card did not
- it read gross_pay raw, so it reported a gross the other five disagreed with
and a chargeable pay its own tax figure did not follow from. Nothing failed,
because nothing asked. This asks.

Checked by invariant against whatever slips the site holds rather than against
expected figures, so it is worth running on any site.
"""

import frappe
from frappe.utils import flt

from upande_payroll.kenya_statutory_gross_pay import (
    absence_on_slip,
    get_absence_components,
)

RESULTS = []


def check(name, got, want):
    ok = (abs(flt(got) - flt(want)) <= 0.01) if isinstance(want, (int, float)) else (got == want)
    RESULTS.append((ok, name, got, want))


def run():
    company = frappe.db.get_value("Salary Slip", {"docstatus": ["!=", 2]}, "company")
    if not company:
        print("  no salary slips on this site, nothing to check")
        return

    components = get_absence_components(company)
    check("the company's absence components are readable", isinstance(components, set), True)

    period = frappe.db.sql(
        """SELECT start_date, end_date, docstatus FROM `tabSalary Slip`
           WHERE company = %s AND docstatus != 2
           GROUP BY start_date, end_date, docstatus
           ORDER BY COUNT(*) DESC LIMIT 1""",
        company, as_dict=True,
    )[0]
    status = {0: "Draft", 1: "Submitted"}[period.docstatus]
    filters = frappe._dict(
        company=company, from_date=str(period.start_date),
        to_date=str(period.end_date), docstatus=status,
    )

    # What every report ought to say, worked out here from the payslips.
    expected = {}
    for slip in frappe.get_all(
        "Salary Slip",
        filters={"company": company, "start_date": (">=", filters.from_date),
                 "end_date": ("<=", filters.to_date), "docstatus": period.docstatus},
        fields=["name", "employee", "employee_name", "gross_pay"],
    ):
        net = flt(slip.gross_pay) - absence_on_slip(slip.name, components)
        expected[slip.employee_name] = expected.get(slip.employee_name, 0.0) + net

    _check_range_reports(filters, expected)
    _check_p9(company, filters, period, components)
    _check_helb_reads_loans(filters)

    _report()


def _check_range_reports(filters, expected):
    from upande_payroll.upande_payroll.report.affordable_housing_levy.affordable_housing_levy import (
        execute as ahl,
    )
    from upande_payroll.upande_payroll.report.kenya_p10_report.kenya_p10_report import (
        execute as p10,
    )
    from upande_payroll.upande_payroll.report.national_social_security_fund.national_social_security_fund import (
        execute as nssf,
    )
    from upande_payroll.upande_payroll.report.social_health_insurance_fund.social_health_insurance_fund import (
        execute as shif,
    )

    for label, fn, field in (
        ("NSSF", nssf, "gross_pay"),
        ("SHIF", shif, "gross_salary"),
        ("Housing Levy", ahl, "gross_salary"),
        ("P10", p10, "total_gross_pay"),
    ):
        _columns, rows = fn(filters)
        compared = 0
        for row in rows:
            name = row.get("employee_name") or row.get("full_name")
            if name not in expected:
                continue
            compared += 1
            check(
                f"{label} reports gross less absence for {name}",
                flt(row.get(field)),
                expected[name],
            )
        check(f"{label} returned somebody to compare", compared > 0, True)


def _check_p9(company, filters, period, components):
    """The card is per employee per fiscal year, so it is checked on its own terms."""
    from upande_payroll.upande_payroll.report.kenya_p9_card_report.kenya_p9_card_report import (
        execute as p9,
    )

    fiscal_year = frappe.db.get_value(
        "Fiscal Year",
        {"year_start_date": ("<=", filters.to_date), "year_end_date": (">=", filters.to_date)},
        "name",
    )
    if not fiscal_year:
        RESULTS.append((True, "no fiscal year covers this period, P9 not checked",
                        "skipped", "skipped"))
        return

    slips = frappe.get_all(
        "Salary Slip",
        filters={"company": company, "start_date": (">=", filters.from_date),
                 "end_date": ("<=", filters.to_date), "docstatus": period.docstatus},
        fields=["name", "employee", "gross_pay"],
    )
    # Somebody carrying an absence is the case worth checking; anyone else
    # would pass whether or not the report subtracted anything.
    with_absence = [s for s in slips if absence_on_slip(s.name, components) > 0]
    if not with_absence:
        RESULTS.append((True, "no slip in this period carries an absence, P9 not checked",
                        "skipped", "skipped"))
        return

    employee = with_absence[0].employee
    _columns, rows = p9(frappe._dict(
        company=company, employee=employee, fiscal_year=fiscal_year,
        docstatus=filters.docstatus,
    ))
    total = [r for r in rows if str(r.get("month")) == "Total"]
    check("the P9 card returns a year total", bool(total), True)
    if not total:
        return

    year = frappe.db.get_value("Fiscal Year", fiscal_year,
                               ["year_start_date", "year_end_date"], as_dict=True)
    on_card = frappe.get_all(
        "Salary Slip",
        filters={"company": company, "employee": employee,
                 "end_date": ("between", [year.year_start_date, year.year_end_date]),
                 "docstatus": period.docstatus},
        fields=["name", "gross_pay"],
    )
    want = sum(flt(s.gross_pay) - absence_on_slip(s.name, components) for s in on_card)
    check("the P9 card reports gross less absence", flt(total[0].get("total_gross_pay")), want)


def _check_helb_reads_loans(filters):
    """HELB run as a Loan Product is reported, not silently missed.

    A blank statutory return reads as nobody contributing rather than as a
    misconfiguration, which is the failure worth a check of its own.
    """
    from upande_payroll.upande_payroll.report.helb_report.helb_report import execute as helb

    row = frappe.db.sql(
        """SELECT sl.parent, sl.loan_product, ss.employee_name, SUM(sl.total_payment) paid
           FROM `tabSalary Slip Loan` sl
           JOIN `tabSalary Slip` ss ON ss.name = sl.parent
           WHERE ss.company = %(company)s AND ss.start_date >= %(from_date)s
             AND ss.end_date <= %(to_date)s AND sl.total_payment > 0
           GROUP BY sl.parent, sl.loan_product, ss.employee_name LIMIT 1""",
        filters, as_dict=True,
    )
    if not row:
        RESULTS.append((True, "no loan repayment in this period, HELB loans not checked",
                        "skipped", "skipped"))
        return

    row = row[0]
    _columns, rows = helb(frappe._dict(filters, loan_product=row.loan_product,
                                       salary_component=None))
    mine = [r for r in rows if r.get("full_name") == row.employee_name]
    check("HELB reports a levy run as a loan product", bool(mine), True)
    if mine:
        check("the amount is at least what that payslip repaid",
              flt(mine[0].get("amount")) >= flt(row.paid) - 0.01, True)

    _columns, none_named = helb(frappe._dict(filters, salary_component=None, loan_product=None))
    check("naming neither a component nor a product reports nothing", none_named, [])


def _report():
    failed = [r for r in RESULTS if not r[0]]
    for ok, name, got, want in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            print(f"          got {got!r}, wanted {want!r}")
    print(f"\n  {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
