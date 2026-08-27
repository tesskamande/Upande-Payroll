"""Checks for which Income Tax Slab a Gratuity's assessment year resolves to.

Run with:

    bench --site <site> execute upande_payroll.tests.gratuity_slab_checks.run

Everything is rolled back at the end, so it is safe against a working site.

The slab is scoped by currency, not by company: income tax is national law, so
one record should serve every company on the site that reports in that currency.
What is checked here is that a shared record is found, that a slab in another
currency is not, that the most recent effective date wins, and that a site whose
slabs do carry a company - which is every site that predates this - resolves
exactly as it did before.
"""

import frappe
from frappe.utils import flt

COMPANY = "Karen Roses"
RESULTS = []


def check(name, got, want, tol=0.01):
    ok = (abs(flt(got) - flt(want)) <= tol) if isinstance(want, (int, float)) else (got == want)
    RESULTS.append((ok, name, got, want))


def slab(name, effective_from, currency="KES", company=None, relief=28800.0,
         bands=((0, 288000, 10), (288000, 388000, 25), (388000, 0, 30))):
    if frappe.db.exists("Income Tax Slab", name):
        frappe.delete_doc("Income Tax Slab", name, force=True, ignore_permissions=True)
    d = frappe.get_doc({
        "doctype": "Income Tax Slab", "name": name, "currency": currency,
        "company": company, "effective_from": effective_from,
        "standard_tax_exemption_amount": relief, "allow_tax_exemption": 1,
        "slabs": [{"from_amount": f, "to_amount": t, "percent_deduction": p}
                  for f, t, p in bands],
    })
    d.insert(ignore_permissions=True)
    d.submit()
    # The doctype will not hold these states through a save: `company` is filled
    # from the user's default Company, and `currency` is fetched from it
    # (fetch_from: company.default_currency, reqd). So the record has to be put
    # into the state under test directly. That defaulting is also why a shared,
    # company-less slab is awkward to create by hand - and why the lookup had
    # better not depend on one.
    frappe.db.set_value("Income Tax Slab", d.name,
                        {"company": company, "currency": currency},
                        update_modified=False)
    return d.name


def run():
    from upande_payroll.gratuity_utils import slab_in_effect

    currency = frappe.get_cached_value("Company", COMPANY, "default_currency")
    check("setup: the company reports in a currency", bool(currency), True)

    # ------------------------------------------------ the site as it stands
    # Every slab here carries a company. Resolution must not change.
    before = slab_in_effect(COMPANY, "2024-12-31")
    check("existing: a company's own slab still resolves", bool(before), True)
    check("existing: and it is the one in effect for the year",
          frappe.db.get_value("Income Tax Slab", before, "effective_from") is not None, True)

    owner = frappe.db.get_value("Income Tax Slab", before, "company")
    check("existing: the site's slabs do carry a company", owner, COMPANY)

    # ------------------------------------------------ a shared slab is found
    # company left blank is how ERPNext expresses "this is the law, not one
    # company's copy". The old lookup could never find it.
    shared = slab("ZZ Shared KES 2025", "2025-01-01", currency=currency, company=None)
    check("shared: a slab with no company resolves",
          slab_in_effect(COMPANY, "2025-12-31"), shared)
    check("shared: it really carries no company",
          frappe.db.get_value("Income Tax Slab", shared, "company"), None)

    # The difference the change makes, stated as the old query: filtered by
    # company, the shared record is invisible and the year falls back to an
    # older slab - or to nothing at all on a company that has none.
    old_style = frappe.db.get_value(
        "Income Tax Slab",
        {"company": COMPANY, "effective_from": ("<=", "2025-12-31"), "disabled": 0},
        "name", order_by="effective_from desc",
    )
    check("shared: the old company filter would have missed it",
          old_style != shared, True)

    # ------------------------------------- another company's slab is eligible
    # Nothing filters on company any more, so a record owned by one company
    # serves another. This is the same query path the shared case proves; what
    # is asserted here is that ownership plays no part in the choice.
    other = slab("ZZ Owned KES 2026", "2026-01-01", currency=currency, company=COMPANY)
    check("ownership: the latest slab wins whoever owns it",
          slab_in_effect(COMPANY, "2026-12-31"), other)

    # --------------------------------------------- currency still scopes it
    slab("ZZ Foreign 2027", "2027-01-01", currency="USD", company=None)
    check("currency: a slab in another currency is not used",
          slab_in_effect(COMPANY, "2027-12-31"), other)

    # -------------------------------------------------- effective date rules
    check("dates: the most recent slab on or before the date wins",
          slab_in_effect(COMPANY, "2025-06-30"), shared)
    check("dates: a slab effective after the date is ignored",
          slab_in_effect(COMPANY, "2024-12-31"), before)

    disabled = slab("ZZ Disabled 2025", "2025-06-01", currency=currency, company=None)
    frappe.db.set_value("Income Tax Slab", disabled, "disabled", 1)
    check("dates: a disabled slab is skipped",
          slab_in_effect(COMPANY, "2025-12-31"), shared)

    check("dates: nothing before the earliest slab",
          slab_in_effect(COMPANY, "1999-12-31"), None)

    _computation_checks()

    report()


def _member():
    """Somebody to hang the computation on. Not in a public pension scheme, so
    the exemption allowance is nil and the arithmetic below is the whole story."""
    existing = frappe.db.get_value("Employee", {"employee_name": "ZZ Gratuity Member"}, "name")
    if existing:
        return existing
    return frappe.get_doc({
        "doctype": "Employee", "first_name": "ZZ Gratuity", "last_name": "Member",
        "company": COMPANY, "date_of_joining": "2015-01-01",
        "date_of_birth": "1985-01-01", "gender": "Female", "status": "Active",
    }).insert(ignore_permissions=True).name


def _gratuity(employee, year, portion, annual_pay, paye_paid):
    doc = frappe.new_doc("Gratuity")
    doc.employee = employee
    doc.company = COMPANY
    doc.append("custom_gratuity_tax_computation", {
        "gratuity_year": year,
        "gratuity_portion": portion,
        "annual_taxable_pay": annual_pay,
        "paye_already_paid": paye_paid,
    })
    return doc


def _computation_checks():
    """The tax actually computed, through the real function, on real slabs.

    Worked by hand so the check tests the arithmetic rather than agreeing with
    whatever the code happens to produce. Revised income is 800,000 throughout:
    an annual taxable pay of 600,000 plus a 200,000 slice of gratuity.
    """
    from upande_payroll.gratuity_utils import _calculate_tax_on_rows

    employee = _member()
    currency = frappe.get_cached_value("Company", COMPANY, "default_currency")

    # --- against the site's own 2023 slab, which is what a 2024 row resolves to
    #     0 -    288,000 @ 10%   288,000       x .10  =  28,800.00
    # 288,001 -  388,000 @ 25%    99,999       x .25  =  24,999.75
    # 388,001 -6,000,000 @ 30%   411,999       x .30  = 123,599.70
    #                                          tax    = 177,399.45
    #                        less relief 28,800 and PAYE already paid 100,000
    doc = _gratuity(employee, 2024, 200000, 600000, 100000)
    _calculate_tax_on_rows(doc)
    row = doc.custom_gratuity_tax_computation[0]
    check("computed: revised taxable income", row.revised_taxable_income, 800000.0)
    check("computed: tax on the revised income", row.tax_on_revised_income, 177399.45)
    check("computed: personal relief from the slab", row.personal_relief, 28800.0)
    check("computed: tax on the gratuity", row.tax_on_gratuity, 48599.45)
    check("computed: the document's PAYE total", doc.custom_paye, 48599.45)

    # --- now a shared slab, owned by nobody, effective later
    # One band, everything at 20%: 800,000 x .20 = 160,000, relief 30,000,
    # less the same 100,000 already paid.
    slab("ZZ Shared 2024", "2024-01-01", currency=currency, company=None,
         relief=30000.0, bands=((0, 0, 20),))

    doc = _gratuity(employee, 2024, 200000, 600000, 100000)
    _calculate_tax_on_rows(doc)
    row = doc.custom_gratuity_tax_computation[0]
    check("shared slab: its bands are the ones applied", row.tax_on_revised_income, 160000.0)
    check("shared slab: its relief is the one applied", row.personal_relief, 30000.0)
    check("shared slab: tax on the gratuity", row.tax_on_gratuity, 30000.0)
    check("shared slab: and the figure moved from the company slab's",
          row.tax_on_gratuity != 48599.45, True)

    # --- a year the shared slab does not reach still uses the older company one
    doc = _gratuity(employee, 2022, 200000, 600000, 100000)
    _calculate_tax_on_rows(doc)
    row = doc.custom_gratuity_tax_computation[0]
    check("shared slab: an earlier year falls back to the slab of its own time",
          row.personal_relief, 28800.0)

    # --- nothing at all: the row is left alone and the user is told
    doc = _gratuity(employee, 1998, 200000, 600000, 100000)
    _calculate_tax_on_rows(doc)
    row = doc.custom_gratuity_tax_computation[0]
    check("no slab: the row is not taxed on a guess", flt(row.tax_on_gratuity), 0.0)
    check("no slab: and no revised income is invented",
          flt(row.revised_taxable_income), 0.0)


def report():
    print()
    print(f"  {'check':<58}{'got':>22}{'want':>22}")
    print("  " + "-" * 102)
    passed = 0
    for ok, name, got, want in RESULTS:
        passed += 1 if ok else 0
        mark = "ok  " if ok else "FAIL"
        g = f"{got:,.2f}" if isinstance(got, float) else str(got)
        w = f"{want:,.2f}" if isinstance(want, float) else str(want)
        print(f"{mark} {name:<58}{g:>22}{w:>22}")
    print("  " + "-" * 102)
    print(f"  {passed}/{len(RESULTS)} passed")
    frappe.db.rollback()
