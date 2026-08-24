"""Checks for Employee Salary Advance and its recovery through payroll.

Run with:

    bench --site <site> execute upande_payroll.tests.salary_advance_checks.run

Everything is rolled back at the end, so it is safe against a working site. It
builds its own components, advance types, structures and employees. A high base
salary is used throughout so the two thirds cap never binds - what is checked
here is what the advance claims, not how the cap divides a shortage, which
deduction_cap_checks already covers.

Covers: schedule generation at zero and non zero interest including the rounding
remainder, advance type validation, the limits and which of them are enforced
only when set, the sum that must not move when HR reshapes a plan, refusing to
cut a period below what it has already collected, arrears re-claimed from the
schedule, the catch-up ceiling, suspending recovery, the recovery log, and full
reversal when a slip is cancelled.
"""

import frappe
from frappe.utils import flt

COMPANY = "Karen Roses"
BASE = 200000.0
RESULTS = []


def check(name, got, want, tol=0.01):
    ok = (abs(flt(got) - flt(want)) <= tol) if isinstance(want, (int, float)) else (got == want)
    RESULTS.append((ok, name, got, want))


def refuses(name, fn):
    """A check that passes only when the operation is rejected.

    Wrapped in a savepoint: a throw part way through an insert can leave the
    transaction holding half a document, and the next check would then be
    testing the wreckage rather than what it meant to.
    """
    frappe.db.savepoint("advance_check")
    try:
        fn()
        RESULTS.append((False, name, "accepted", "rejected"))
    except Exception:
        frappe.db.rollback(save_point="advance_check")
        RESULTS.append((True, name, "rejected", "rejected"))


# ---------------------------------------------------------------- helpers

def comp(name, abbr, ctype="Deduction"):
    if not frappe.db.exists("Salary Component", name):
        frappe.get_doc({
            "doctype": "Salary Component", "salary_component": name,
            "salary_component_abbr": abbr, "type": ctype,
            "depends_on_payment_days": 0, "remove_if_zero_valued": 0,
        }).insert(ignore_permissions=True)
    return name


def statutory_rows():
    from upande_payroll.kenya_statutory_calculator import get_statutory_components

    return [{"salary_component": name, "amount": 0,
             "amount_based_on_formula": 0, "depends_on_payment_days": 0}
            for name in get_statutory_components().values()]


def structure(name):
    if frappe.db.exists("Salary Structure", name):
        return name
    d = frappe.get_doc({
        "doctype": "Salary Structure", "name": name, "company": COMPANY,
        "payroll_frequency": "Monthly", "currency": "KES",
        "earnings": [{"salary_component": comp("SAC Basic", "SACBAS", "Earning"),
                      "amount_based_on_formula": 1, "formula": "base"}],
        "deductions": statutory_rows(),
    })
    d.insert(ignore_permissions=True)
    d.submit()
    return name


def employee(name):
    existing = frappe.db.get_value("Employee", {"employee_name": name}, "name")
    if existing:
        return existing
    return frappe.get_doc({
        "doctype": "Employee", "first_name": name, "company": COMPANY,
        "date_of_joining": "2026-01-01", "date_of_birth": "1990-01-01",
        "gender": "Female", "status": "Active",
    }).insert(ignore_permissions=True).name


def assign(emp, struct):
    a = frappe.get_doc({"doctype": "Salary Structure Assignment", "employee": emp,
                        "salary_structure": struct, "from_date": "2026-07-01",
                        "company": COMPANY, "base": BASE})
    a.insert(ignore_permissions=True)
    a.submit()


def staff(name, struct):
    emp = employee(name)
    assign(emp, struct)
    return emp


def adv_type(name, method="Interest Free", rate=0, component=None, **limits):
    if frappe.db.exists("Employee Salary Advance Type", name):
        frappe.delete_doc("Employee Salary Advance Type", name, force=True,
                          ignore_permissions=True)
    d = frappe.get_doc(dict(
        doctype="Employee Salary Advance Type", advance_type_name=name,
        company=COMPANY, interest_method=method, interest_rate=rate,
        salary_component=component or comp("SAC Advance", "SACADV"), **limits))
    d.insert(ignore_permissions=True)
    return d.name


def advance(emp, atype, amount, periods, start="2026-09-01",
            posting="2026-08-01", submit=True):
    a = frappe.get_doc(dict(
        doctype="Employee Salary Advance", employee=emp, company=COMPANY,
        advance_type=atype, posting_date=posting, advance_amount=amount,
        repayment_periods=periods, repayment_start_date=start))
    a.insert(ignore_permissions=True)
    if submit:
        a.submit()
    return a


def slip(emp, struct, start, end, submit=False):
    old = frappe.db.get_value("Salary Slip",
                              {"employee": emp, "start_date": start,
                               "docstatus": ("<", 2)}, "name")
    if old:
        frappe.delete_doc("Salary Slip", old, force=True, ignore_permissions=True)
    s = frappe.get_doc({
        "doctype": "Salary Slip", "employee": emp, "company": COMPANY,
        "salary_structure": struct, "payroll_frequency": "Monthly",
        "start_date": start, "end_date": end, "posting_date": end})
    s.insert(ignore_permissions=True)
    if submit:
        s.submit()
    return s


def amt(s, component):
    return next((flt(d.amount) for d in s.deductions
                 if d.salary_component == component), 0.0)


def instalments(a):
    return [flt(r.instalment_amount) for r in a.repayment_schedule]


# ---------------------------------------------------------------- suite

def run():
    struct = structure("SAC Structure")
    component = comp("SAC Advance", "SACADV")

    # ---------------------------------------------------- schedule generation
    free = adv_type("SAC Free")
    ten = adv_type("SAC Ten", "Per Annum Simple", 10)

    emp = staff("SAC Schedule", struct)
    a = advance(emp, free, 60000, 7, submit=False)
    check("0%: total repayable", a.total_repayable, 60000.0)
    check("0%: total interest", a.total_interest, 0.0)
    check("0%: schedule sums to the total", sum(instalments(a)), 60000.0)
    check("0%: even instalment", instalments(a)[0], 8571.43)
    check("0%: last absorbs the rounding", instalments(a)[-1], 8571.42)
    check("0%: one row per period", len(a.repayment_schedule), 7)

    b = advance(emp, ten, 60000, 6, submit=False)
    check("10% p.a. over 6 months: interest", b.total_interest, 3000.0)
    check("10% p.a.: total repayable", b.total_repayable, 63000.0)
    check("10% p.a.: instalment", instalments(b)[0], 10500.0)
    check("10% p.a.: interest spread evenly", b.repayment_schedule[0].interest_amount, 500.0)
    check("10% p.a.: principal per period", b.repayment_schedule[0].principal_amount, 10000.0)

    # ---------------------------------------------------- advance type policy
    refuses("type: per annum with no rate", lambda: adv_type("SAC Broken", "Per Annum Simple", 0))
    stray = frappe.get_doc("Employee Salary Advance Type",
                           adv_type("SAC Stray", "Interest Free", 7))
    check("type: interest free normalises a stray rate", stray.interest_rate, 0.0)
    refuses("type: earning component refused", lambda: adv_type(
        "SAC Earning", component=comp("SAC Bonus", "SACBON", "Earning")))

    # ---------------------------------------------------------------- limits
    capped = adv_type("SAC Capped", max_advance_amount=10000,
                      max_repayment_periods=3, max_active_advances=1)
    lim = staff("SAC Limits", struct)
    refuses("limit: above maximum amount", lambda: advance(lim, capped, 20000, 2, submit=False))
    refuses("limit: above maximum periods", lambda: advance(lim, capped, 5000, 6, submit=False))
    advance(lim, capped, 5000, 2)
    refuses("limit: second active advance", lambda: advance(lim, capped, 5000, 2, submit=False))

    exposed = adv_type("SAC Exposed", max_total_exposure=8000)
    exp = staff("SAC Exposure", struct)
    advance(exp, exposed, 5000, 2)
    refuses("limit: above total exposure", lambda: advance(exp, exposed, 5000, 2, submit=False))

    unset = frappe.get_doc("Employee Salary Advance Type", free)
    check("limit: blank means not enforced", flt(unset.max_advance_amount), 0.0)

    # ------------------------------------------------------- input validation
    val = staff("SAC Inputs", struct)
    refuses("input: repayment before posting date",
            lambda: advance(val, free, 5000, 2, start="2026-01-01", submit=False))
    refuses("input: zero amount", lambda: advance(val, free, 0, 2, submit=False))
    refuses("input: zero periods", lambda: advance(val, free, 5000, 0, submit=False))

    # ------------------------------------------------------ reshaping a plan
    res = staff("SAC Reshape", struct)
    plan = advance(res, free, 20000, 4)

    def shave():
        p = frappe.get_doc("Employee Salary Advance", plan.name)
        p.repayment_schedule[2].instalment_amount = 2000
        p.save(ignore_permissions=True)

    refuses("reshape: total may not shrink", shave)

    def negative():
        p = frappe.get_doc("Employee Salary Advance", plan.name)
        p.repayment_schedule[2].instalment_amount = -1000
        p.repayment_schedule[3].instalment_amount = 11000
        p.save(ignore_permissions=True)

    refuses("reshape: negative instalment", negative)

    plan = frappe.get_doc("Employee Salary Advance", plan.name)
    plan.repayment_schedule[2].instalment_amount = 2000
    plan.repayment_schedule[3].instalment_amount = 8000
    plan.save(ignore_permissions=True)
    plan.reload()
    check("reshape: accepted when the total holds", sum(instalments(plan)), 20000.0)
    check("reshape: moved amount lands", instalments(plan)[2], 2000.0)

    plan = frappe.get_doc("Employee Salary Advance", plan.name)
    plan.repayment_schedule[3].instalment_amount = 3000
    plan.append("repayment_schedule", {"instalment_amount": 5000})
    plan.save(ignore_permissions=True)
    plan.reload()
    check("reshape: term extended by a period", len(plan.repayment_schedule), 5)
    check("reshape: extended term still sums", sum(instalments(plan)), 20000.0)
    check("reshape: new period dated on", str(plan.repayment_schedule[-1].due_date),
          "2027-01-31")

    # ------------------------------------------------------ payroll: claiming
    pay = staff("SAC Payroll", struct)
    run_adv = advance(pay, free, 20000, 4)

    s = slip(pay, struct, "2026-09-01", "2026-09-30")
    check("payroll: claims this period's instalment", amt(s, component), 5000.0)

    # The instalment belongs with the deductions the employee bears, not below
    # the employer contributions and Taxable Income.
    order = [(d.salary_component, bool(d.do_not_include_in_total)) for d in s.deductions]
    advance_at = next(i for i, (c, _s) in enumerate(order) if c == component)
    first_statistical = next((i for i, (_c, stat) in enumerate(order) if stat), len(order))
    check("payroll: row sits above the statistical block",
          advance_at < first_statistical, True)
    check("payroll: row is inside the total",
          bool(next(d.do_not_include_in_total for d in s.deductions
                    if d.salary_component == component)), False)

    s.submit()
    run_adv.reload()
    check("payroll: paid recorded", run_adv.total_paid, 5000.0)
    check("payroll: outstanding falls", run_adv.outstanding_amount, 15000.0)
    check("payroll: status moves on", run_adv.status, "Partially Repaid")
    check("payroll: period marked paid", run_adv.repayment_schedule[0].status, "Paid")
    check("payroll: one recovery logged", len(run_adv.recoveries), 1)
    check("payroll: recovery names the slip", run_adv.recoveries[0].reference_name, s.name)
    check("payroll: recovery names the doctype",
          run_adv.recoveries[0].reference_doctype, "Salary Slip")

    # October skipped entirely, so November owes two periods
    nov = slip(pay, struct, "2026-11-01", "2026-11-30")
    check("payroll: arrears re-claimed from the schedule", amt(nov, component), 10000.0)

    # the ceiling limits the overdue part only
    frappe.db.set_value("Employee Salary Advance Type", free, "max_catch_up_amount", 2000)
    frappe.clear_cache()
    nov.save(ignore_permissions=True)
    check("payroll: catch-up ceiling caps arrears", amt(nov, component), 7000.0)
    frappe.db.set_value("Employee Salary Advance Type", free, "max_catch_up_amount", 0)
    frappe.clear_cache()

    # suspending recovery
    frappe.db.set_value("Employee Salary Advance", run_adv.name, "repay_from_salary", 0)
    nov.save(ignore_permissions=True)
    check("payroll: nothing claimed while recovery is off", amt(nov, component), 0.0)
    frappe.db.set_value("Employee Salary Advance", run_adv.name, "repay_from_salary", 1)

    # ---------------------------------------------------- payroll: reversal
    nov.save(ignore_permissions=True)
    nov.submit()
    run_adv.reload()
    check("reversal: two more periods collected", run_adv.total_paid, 15000.0)

    nov.cancel()
    run_adv.reload()
    check("reversal: paid returns to what the first slip took", run_adv.total_paid, 5000.0)
    check("reversal: outstanding restored", run_adv.outstanding_amount, 15000.0)
    check("reversal: entries removed from the log", len(run_adv.recoveries), 1)
    check("reversal: period owes again", run_adv.repayment_schedule[1].status, "Pending")

    # ------------------------------------------------------- clearing it out
    done = staff("SAC Cleared", struct)
    small = advance(done, free, 10000, 2)
    slip(done, struct, "2026-09-01", "2026-09-30", submit=True)
    slip(done, struct, "2026-10-01", "2026-10-31", submit=True)
    small.reload()
    check("cleared: fully collected", small.total_paid, 10000.0)
    check("cleared: nothing outstanding", small.outstanding_amount, 0.0)
    check("cleared: status closes", small.status, "Repaid")

    later = slip(done, struct, "2026-11-01", "2026-11-30")
    check("cleared: nothing claimed afterwards", amt(later, component), 0.0)

    # ------------------------------------------------------------- leavers
    # An advance running past the leaving date must be taken on the last
    # payslip: there is no later payroll to take it on.
    gone = staff("SAC Leaver", struct)
    leaving = advance(gone, free, 20000, 4)

    sep = slip(gone, struct, "2026-09-01", "2026-09-30")
    check("leaver: ordinary slip claims one instalment", amt(sep, component), 5000.0)
    sep.submit()

    frappe.db.set_value("Employee", gone, "relieving_date", "2026-10-31")
    frappe.db.set_value("Employee", gone, "status", "Left")
    frappe.clear_cache()

    last = slip(gone, struct, "2026-10-01", "2026-10-31")
    check("leaver: last slip claims the whole balance", amt(last, component), 15000.0)
    last.submit()
    leaving.reload()
    check("leaver: advance cleared on leaving", leaving.outstanding_amount, 0.0)
    check("leaver: status closes", leaving.status, "Repaid")
    check("leaver: every period settled",
          all(r.status == "Paid" for r in leaving.repayment_schedule), True)

    # A ceiling must not hold money back on a last payslip either.
    held = staff("SAC Leaver Capped", struct)
    tight = adv_type("SAC Tight", max_catch_up_amount=500)
    stuck = advance(held, tight, 20000, 4)
    frappe.db.set_value("Employee", held, "relieving_date", "2026-09-30")
    frappe.db.set_value("Employee", held, "status", "Left")
    frappe.clear_cache()
    only = slip(held, struct, "2026-09-01", "2026-09-30")
    check("leaver: ceiling does not apply to a last payslip",
          amt(only, comp("SAC Advance", "SACADV")), 20000.0)

    # A waived period is not collected, even on the way out.
    part = staff("SAC Leaver Waived", struct)
    waived = advance(part, free, 20000, 4)
    waived.repayment_schedule[3].status = "Waived"
    waived.save(ignore_permissions=True)
    frappe.db.set_value("Employee", part, "relieving_date", "2026-09-30")
    frappe.db.set_value("Employee", part, "status", "Left")
    frappe.clear_cache()
    wslip = slip(part, struct, "2026-09-01", "2026-09-30")
    check("leaver: waived period excluded from the final claim",
          amt(wslip, component), 15000.0)

    # ------------------------------------------------- terminal dues recovery
    # Whatever a leaver's last payslip could not take, the settlement takes.
    dues = staff("SAC Dues", struct)
    owing = advance(dues, free, 20000, 4)
    slip(dues, struct, "2026-09-01", "2026-09-30", submit=True)
    owing.reload()
    check("dues: something still owed after one payslip", owing.outstanding_amount, 15000.0)

    # What the settlement would put on its deductions table, without needing a
    # whole configured settlement to exist.
    settlement = frappe.new_doc("Terminal Dues Settlement")
    settlement.employee = dues
    settlement.company = COMPANY
    settlement._sync_salary_advance_recovery()
    rows = [r for r in (settlement.deductions or [])
            if r.source_doctype == "Employee Salary Advance"]
    check("dues: one deduction row per advance", len(rows), 1)
    check("dues: claims the whole balance", flt(rows[0].amount), 15000.0)
    check("dues: deducted as the advance's component", rows[0].deduction_type, component)
    check("dues: points back at the advance", rows[0].source_document, owing.name)

    # Recovery from a document that is not a payslip, including future periods.
    owing = frappe.get_doc("Employee Salary Advance", owing.name)
    # reference_name is a Dynamic Link, so it insists the document exists - which
    # is what we want in production, where _recover_salary_advances always passes
    # a real submitted settlement. Standing one up here would mean configuring
    # every terminal dues component just to test the recovery, so the link check
    # is waived and the settlement name is a stand-in.
    owing.flags.ignore_links = True
    spent = owing.apply_recovery("Terminal Dues Settlement", "SAC-TDS-01",
                                 "2026-09-30", 15000.0)
    check("dues: settlement recovery placed in full", spent, 15000.0)
    owing.reload()
    check("dues: advance cleared by the settlement", owing.outstanding_amount, 0.0)
    check("dues: status closes", owing.status, "Repaid")
    check("dues: future periods creditable too",
          all(r.status == "Paid" for r in owing.repayment_schedule), True)
    check("dues: log records the settlement, not a slip",
          owing.recoveries[-1].reference_doctype, "Terminal Dues Settlement")

    owing = frappe.get_doc("Employee Salary Advance", owing.name)
    owing.flags.ignore_links = True
    owing.reverse_recovery("Terminal Dues Settlement", "SAC-TDS-01")
    owing.reload()
    check("dues: cancelling the settlement puts the balance back",
          owing.outstanding_amount, 15000.0)
    check("dues: the payslip recovery survives it", owing.total_paid, 5000.0)

    # Nothing to recover once an advance is settled.
    settlement = frappe.new_doc("Terminal Dues Settlement")
    settlement.employee = done
    settlement.company = COMPANY
    settlement._sync_salary_advance_recovery()
    check("dues: nothing claimed for a cleared advance",
          len([r for r in (settlement.deductions or [])
               if r.source_doctype == "Employee Salary Advance"]), 0)

    # -------------------------------------------------------------- report
    print()
    print(f"  {'check':<52}{'got':>14}{'want':>14}")
    print("  " + "-" * 80)
    passed = 0
    for ok, name, got, want in RESULTS:
        passed += 1 if ok else 0
        mark = "ok  " if ok else "FAIL"
        g = f"{got:,.2f}" if isinstance(got, float) else str(got)
        w = f"{want:,.2f}" if isinstance(want, float) else str(want)
        print(f"{mark} {name:<52}{g:>14}{w:>14}")
    print("  " + "-" * 80)
    print(f"  {passed}/{len(RESULTS)} passed")

    frappe.db.rollback()
