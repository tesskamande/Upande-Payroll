"""Checks that a company with no Company Payroll Settings is left alone.

Run with:

    bench --site <site> execute upande_payroll.tests.unconfigured_company_checks.run

Everything is rolled back at the end, so it is safe against a working site.

Installing this app is not a decision to use it. A site can install it for one
doctype and go on running payroll the stock way, and on such a site no company
has a Company Payroll Settings record. Every rule here is opted into on that
record, so the correct behaviour is for all of them to stand down.

What happened instead: Salary Slip goes through this app's regional override
however the site came by it, and the override read the settings unguarded. The
first payroll run on Mona Flowers Kenya Limited died with "Company Payroll
Settings Mona Flowers Kenya Limited not found" and no slip could be made at all
- for a company that had never asked this app to calculate anything.

So the rule checked here is: with no settings record, every entry point either
does nothing or says plainly what is missing. Never a DoesNotExistError.

Also covers the statutory mapping side check - a category is read from one side
of the payslip only, so a component from the other side is a row nothing reads.
"""

import frappe

from upande_payroll.kenya_statutory_gross_pay import CATEGORY_COMPONENT_TYPE
from upande_payroll.upande_payroll.doctype.company_payroll_settings.company_payroll_settings import (
    payroll_settings,
)

RESULTS = []
NO_SUCH_COMPANY = "No Such Company Ltd (check)"


def check(name, got, want):
    RESULTS.append((got == want, name, got, want))


def survives(name, fn, want=None):
    """A check that passes only when nothing is raised.

    DoesNotExistError is called out separately from any other failure: it is
    the specific thing this module exists to catch, and it reads better in the
    output than a bare traceback line.
    """
    frappe.db.savepoint("unconfigured_check")
    try:
        got = fn()
        RESULTS.append((got == want, name, got, want))
    except frappe.DoesNotExistError as e:
        RESULTS.append((False, name, f"DoesNotExistError: {e}", want))
    except Exception as e:
        RESULTS.append((False, name, f"{type(e).__name__}: {e}", want))
    frappe.db.rollback(save_point="unconfigured_check")


def run():
    _check_helper()
    _check_entry_points()
    _check_mapping_sides()
    _check_settings_validations()
    _report()


def _check_helper():
    check(
        "a company with no settings reads as None",
        payroll_settings(NO_SUCH_COMPANY),
        None,
    )
    check("a blank company reads as None", payroll_settings(None), None)
    check("an empty company reads as None", payroll_settings(""), None)

    configured = frappe.db.get_value("Company Payroll Settings", {}, "name")
    if configured:
        settings = payroll_settings(configured)
        check(
            "a configured company still returns its record",
            settings and settings.name,
            configured,
        )


def _check_entry_points():
    """Each hook, called for a company that has no settings."""
    from upande_payroll.deduction_cap import apply_deduction_cap
    from upande_payroll.gratuity_utils import calculate_gratuity
    from upande_payroll.kenya_statutory_calculator import apply_regional_deductions
    from upande_payroll.payroll_journal import rewrite_payroll_journal
    from upande_payroll.upande_payroll.doctype.company_payroll_settings.company_payroll_settings import (
        get_monthly_working_hours,
        get_notice_days,
    )

    slip = frappe._dict(
        doctype="Salary Slip", company=NO_SUCH_COMPANY, employee="NOBODY",
        earnings=[], deductions=[], payroll_frequency="Monthly",
        gross_pay=0, net_pay=0, total_deduction=0,
    )

    survives(
        "the Salary Slip regional override returns instead of throwing",
        lambda: apply_regional_deductions(slip),
    )
    survives(
        "the two thirds cap returns instead of throwing",
        lambda: apply_deduction_cap(slip),
    )
    survives(
        "gratuity returns instead of throwing",
        lambda: calculate_gratuity(frappe._dict(company=NO_SUCH_COMPANY)),
    )
    survives(
        "monthly working hours reads as None",
        lambda: get_monthly_working_hours(NO_SUCH_COMPANY),
    )
    survives(
        "notice days reads as zero",
        lambda: get_notice_days(NO_SUCH_COMPANY, 5),
        want=0,
    )

    # The journal rewrite gets no further than looking for a Payroll Entry on a
    # plain journal, so it is checked against one that does not belong to
    # payroll at all - the shape every journal on such a site has.
    survives(
        "an ordinary Journal Entry is not rewritten",
        lambda: rewrite_payroll_journal(
            frappe._dict(doctype="Journal Entry", voucher_type="Journal Entry", accounts=[])
        ),
    )

    # Leave Encashment and Overtime Slip are mixins over core classes, so they
    # are checked by the branch they take rather than by building a document:
    # settings missing has to fall through to super(), which is what core did
    # before this app arrived.
    from upande_payroll.leave_encashment_utils import LeaveEncashmentMixin
    from upande_payroll.overtime_utils import OvertimeSlipMixin

    check(
        "leave encashment defers to core when unconfigured",
        _falls_through(LeaveEncashmentMixin, "set_encashment_amount"),
        True,
    )
    check(
        "overtime defers to core when unconfigured",
        _falls_through(OvertimeSlipMixin, "on_submit"),
        True,
    )


def _falls_through(mixin, method):
    """True when the method calls super() for a company with no settings.

    The mixin is put over a stand-in whose only job is to record that it was
    reached, so this runs without a real Leave Encashment or Overtime Slip and
    without touching the site's data.
    """
    reached = []

    class Core:
        def set_encashment_amount(self):
            reached.append(True)

        def on_submit(self):
            reached.append(True)

    class Stub(mixin, Core):
        company = NO_SUCH_COMPANY

    getattr(Stub(), method)()
    return bool(reached)


def _check_mapping_sides():
    """A statutory category is read from one side of the payslip only."""
    meta = frappe.get_meta("Statutory Income Component Mapping")
    options = [
        o for o in (meta.get_field("category").options or "").split("\n") if o.strip()
    ]

    check(
        "every category offered has a side to be read from",
        sorted(set(options) - set(CATEGORY_COMPONENT_TYPE)),
        [],
    )
    check(
        "no side is declared for a category that cannot be picked",
        sorted(set(CATEGORY_COMPONENT_TYPE) - set(options)),
        [],
    )
    check(
        "every side is a real Salary Component type",
        sorted(set(CATEGORY_COMPONENT_TYPE.values())),
        ["Deduction", "Earning"],
    )

    # The table is only right if it matches what the calculator actually does,
    # so it is read back off the source rather than trusted.
    import inspect

    from upande_payroll import kenya_statutory_gross_pay as gross

    source = inspect.getsource(gross._mapped_amounts)
    earnings_half, _, deductions_half = source.partition("for row in salary_slip.deductions")
    misplaced = []
    for category, side in CATEGORY_COMPONENT_TYPE.items():
        quoted = f'"{category}"'
        named = quoted in source or (
            category == gross.ABSENCE_CATEGORY and "ABSENCE_CATEGORY" in source
        )
        if not named:
            # Non-Taxable Payment is not adjusted, only excluded from cash, so
            # it is named in EXCLUDED_FROM_CASH instead.
            if category in gross.EXCLUDED_FROM_CASH:
                continue
            misplaced.append(f"{category} is not read anywhere in _mapped_amounts")
            continue
        token = "ABSENCE_CATEGORY" if category == gross.ABSENCE_CATEGORY else quoted
        in_earnings = token in earnings_half
        in_deductions = token in deductions_half
        want_earning = side == "Earning"
        if want_earning and not in_earnings:
            misplaced.append(f"{category} is declared Earning but read from deductions")
        if not want_earning and not in_deductions:
            misplaced.append(f"{category} is declared Deduction but read from earnings")
        if want_earning and in_deductions:
            misplaced.append(f"{category} is declared Earning but also read from deductions")

    check("each category is declared on the side it is read from", misplaced, [])


def refuses(name, fn):
    """A check that passes only when the operation is rejected."""
    frappe.db.savepoint("unconfigured_check")
    try:
        fn()
        RESULTS.append((False, name, "accepted", "rejected"))
    except frappe.ValidationError:
        RESULTS.append((True, name, "rejected", "rejected"))
    except Exception as e:
        RESULTS.append((False, name, f"{type(e).__name__}: {e}", "rejected"))
    frappe.db.rollback(save_point="unconfigured_check")


def _check_settings_validations():
    """The two guards on the settings form itself.

    Both run against the site's own settings record inside a savepoint, so
    nothing here is kept. Skipped rather than failed where the site has no
    settings or no component of the type needed - this module has to be
    runnable on a site that is deliberately unconfigured.
    """
    company = frappe.db.get_value("Company Payroll Settings", {}, "name")
    if not company:
        print("  no Company Payroll Settings on this site, form guards not checked")
        return

    earning = frappe.db.get_value("Salary Component", {"type": "Earning"}, "name")
    deduction = frappe.db.get_value("Salary Component", {"type": "Deduction"}, "name")

    def mapped(component, category):
        def attempt():
            doc = frappe.get_doc("Company Payroll Settings", company)
            doc.append("statutory_income_component_mapping", {
                "salary_component": component, "category": category,
            })
            doc.save()
        return attempt

    if earning:
        refuses(
            "an Earning mapped to a deductions-side category is rejected",
            mapped(earning, "Pension Contribution"),
        )
    if deduction:
        refuses(
            "a Deduction mapped to an earnings-side category is rejected",
            mapped(deduction, "Non-Cash Benefit"),
        )

    _check_dimension_warning(company)


def _check_dimension_warning(company):
    """Tag Journal With, with and without the dimension behind it.

    Both branches are exercised rather than whichever one the site happens to
    be in: the dimension is disabled inside the savepoint to produce the
    missing case, so the check means the same thing on every site.
    """
    dimension = frappe.db.get_value(
        "Accounting Dimension", {"document_type": "Farm"}, "name"
    )
    if not dimension:
        print("  no Farm Accounting Dimension on this site, warning not checked")
        return

    for disabled, want, label in (
        (0, 0, "no warning where the Accounting Dimension is in use"),
        (1, 1, "a warning where the Accounting Dimension is not in use"),
    ):
        frappe.db.savepoint("unconfigured_check")
        frappe.clear_messages()
        try:
            frappe.db.set_value("Accounting Dimension", dimension, "disabled", disabled)
            frappe.clear_cache(doctype="Accounting Dimension")
            doc = frappe.get_doc("Company Payroll Settings", company)
            doc.payroll_dimension_source = "Farm"
            doc.save()
            said = sum(
                1 for m in frappe.get_message_log()
                if "Accounting Dimension" in str(m.get("message", ""))
            )
            RESULTS.append((said == want, label, said, want))
        except Exception as e:
            RESULTS.append((False, label, f"{type(e).__name__}: {e}", want))
        frappe.clear_messages()
        frappe.db.rollback(save_point="unconfigured_check")


def _report():
    failed = [r for r in RESULTS if not r[0]]
    for ok, name, got, want in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            for line in (got if isinstance(got, list) else [got]):
                print(f"          got  {line}")
            for line in (want if isinstance(want, list) else [want]):
                print(f"          want {line}")
    print(f"\n  {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    frappe.db.rollback()
