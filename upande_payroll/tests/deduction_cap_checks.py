"""Checks for the 1/3 rule (Employment Act 2007 s.19(3)).

Run with:

    bench --site <site> execute upande_payroll.tests.deduction_cap_checks.run

Everything is rolled back at the end, so it is safe against a working site. It
builds its own components, groups, structures and employees, and overwrites the
company's Deduction Priority record while it runs - so do not run it at the same
moment someone is editing that record.

Covers: the tier waterfall and how a shared tier splits a cut, all three wage
bases, boundaries, carry-forward against waive, the arrears lifecycle including
partial recovery and cancellation, weekly payroll, re-saving, the off switch, an
unconfigured company, and leavers.
"""

import frappe
from frappe.utils import cint, flt

COMPANY = "Karen Roses"
RESULTS = []



def wage_base_of(slip):
    """Recompute the base the cap was measured against.

    The slip no longer carries it - the figure was written and never read, so
    it was dropped. Asking the same function payroll asks keeps these checks
    testing the calculation rather than a stored copy of it.
    """
    from upande_payroll.deduction_cap import _config, _wage_base

    settings = frappe.get_cached_doc("Company Payroll Settings", slip.company)
    return flt(_wage_base(slip, settings, _config(slip.company)), 2)


def permitted_of(slip):
    from upande_payroll.deduction_cap import PERMITTED_FRACTION

    return flt(wage_base_of(slip) * PERMITTED_FRACTION, 2)


def check(name, got, want, tol=0.01):
    ok = (abs(flt(got) - flt(want)) <= tol) if isinstance(want, (int, float)) else (got == want)
    RESULTS.append((ok, name, got, want))


# ---------------------------------------------------------------- helpers

def comp(name, abbr, ctype="Deduction"):
    if not frappe.db.exists("Salary Component", name):
        frappe.get_doc({
            "doctype": "Salary Component", "salary_component": name,
            "salary_component_abbr": abbr, "type": ctype,
            "depends_on_payment_days": 0, "remove_if_zero_valued": 0,
        }).insert(ignore_permissions=True)
    return name


def group(gname, priority, reducible, shortfall):
    key = f"{COMPANY}-{gname}"
    g = (frappe.get_doc("Deduction Group", key) if frappe.db.exists("Deduction Group", key)
         else frappe.get_doc({"doctype": "Deduction Group", "company": COMPANY,
                              "group_name": gname}))
    g.priority = priority
    g.reducible = reducible
    g.on_shortfall = shortfall
    g.save(ignore_permissions=True)
    return key


def structure(name, earnings, deductions, freq="Monthly"):
    if frappe.db.exists("Salary Structure", name):
        return name
    d = frappe.get_doc({
        "doctype": "Salary Structure", "name": name, "company": COMPANY,
        "payroll_frequency": freq, "currency": "KES",
        "earnings": earnings, "deductions": deductions,
    })
    d.insert(ignore_permissions=True)
    d.submit()
    return name


def statutory_rows():
    """Zero placeholders for every statutory component, for the calculator to fill.

    Built from the app's own component map rather than copied off some existing
    structure - cloning one made these checks depend on data that happened to be
    on the site, and they stopped running the moment it was cleared.
    """
    from upande_payroll.kenya_statutory_calculator import get_statutory_components

    return [{"salary_component": name, "amount": 0,
             "amount_based_on_formula": 0, "depends_on_payment_days": 0}
            for name in get_statutory_components().values()]


def employee(name):
    e = frappe.db.get_value("Employee", {"employee_name": name}, "name")
    if e:
        return e
    return frappe.get_doc({
        "doctype": "Employee", "first_name": name, "company": COMPANY,
        "date_of_joining": "2026-01-01", "date_of_birth": "1990-01-01",
        "gender": "Female", "status": "Active",
    }).insert(ignore_permissions=True).name


def assign(emp, struct, base):
    if frappe.db.exists("Salary Structure Assignment",
                        {"employee": emp, "salary_structure": struct, "docstatus": 1}):
        return
    a = frappe.get_doc({"doctype": "Salary Structure Assignment", "employee": emp,
                        "salary_structure": struct, "from_date": "2026-07-01",
                        "company": COMPANY, "base": base})
    a.insert(ignore_permissions=True)
    a.submit()


def priority(wage_base, mapping, base_components=None, overrides=None):
    """``overrides`` ranks a component away from its group: {component: rank}."""
    overrides = overrides or {}
    dp = (frappe.get_doc("Deduction Priority", COMPANY)
          if frappe.db.exists("Deduction Priority", COMPANY)
          else frappe.get_doc({"doctype": "Deduction Priority", "company": COMPANY}))
    dp.wage_base = wage_base
    dp.set("deductions", [])
    dp.set("base_components", [])
    for c, g in mapping.items():
        dp.append("deductions", {"salary_component": c, "deduction_group": g,
                                 "override_priority": overrides.get(c)})
    for c in (base_components or []):
        dp.append("base_components", {"salary_component": c})
    dp.save(ignore_permissions=True)
    frappe.clear_cache()
    return dp


def slip(emp, struct, start, end, freq="Monthly"):
    old = frappe.db.get_value("Salary Slip",
                              {"employee": emp, "start_date": start, "docstatus": ("<", 2)}, "name")
    if old:
        frappe.delete_doc("Salary Slip", old, force=True, ignore_permissions=True)
    s = frappe.get_doc({
        "doctype": "Salary Slip", "employee": emp, "company": COMPANY,
        "salary_structure": struct, "payroll_frequency": freq,
        "start_date": start, "end_date": end, "posting_date": end})
    s.insert(ignore_permissions=True)
    return s


def amt(s, component):
    return next((flt(d.amount) for d in s.deductions if d.salary_component == component), 0.0)


def debts(emp):
    return frappe.get_all("Deferred Deduction", filters={"employee": emp},
                          fields=["name", "salary_component", "deferred_amount",
                                  "balance_remaining", "status", "docstatus"],
                          order_by="creation")


# ---------------------------------------------------------------- suite

def run():
    cps = frappe.get_doc("Company Payroll Settings", COMPANY)
    cps.enable_one_third_rule = 1
    cps.save(ignore_permissions=True)

    MAND = group("Mandatory", 1, 0, None)
    COOP = group("Cooperative", 3, 1, "Carry Forward")
    WELF = group("Welfare", 4, 1, "Waive")

    union = comp("Union Dues", "UND")
    adv = comp("Staff Advance Recovery", "SAR")
    sacco = comp("SACCO Loan", "SACL")
    welfare = comp("Welfare Contribution", "WLF")
    car = comp("Company Car Benefit", "CCB", ctype="Earning")
    ot = comp("Overtime Pay", "OTP", ctype="Earning")

    basic = {"salary_component": "Basic Pay", "amount_based_on_formula": 1,
             "formula": "base", "depends_on_payment_days": 0}

    S_MIX = structure("T1 Mix", [basic, {"salary_component": ot, "amount": 6000,
                                         "depends_on_payment_days": 0}],
                      statutory_rows() + [
                          {"salary_component": union, "amount": 1000, "depends_on_payment_days": 0},
                          {"salary_component": adv, "amount": 12000, "depends_on_payment_days": 0},
                          {"salary_component": sacco, "amount": 6000, "depends_on_payment_days": 0},
                          {"salary_component": welfare, "amount": 2000, "depends_on_payment_days": 0}])

    S_BEN = structure("T2 Benefit", [basic, {"salary_component": car, "amount": 100000,
                                             "depends_on_payment_days": 0}],
                      statutory_rows())

    S_EXACT = structure("T3 Exact", [basic],
                        [{"salary_component": adv, "amount": 20000, "depends_on_payment_days": 0}])
    S_OVER = structure("T4 Over", [basic],
                       [{"salary_component": adv, "amount": 25000, "depends_on_payment_days": 0}])
    S_UNDER = structure("T5 Under", [basic],
                        [{"salary_component": adv, "amount": 5000, "depends_on_payment_days": 0}])
    S_PART = structure("T6 Partial", [basic],
                       [{"salary_component": union, "amount": 19000, "depends_on_payment_days": 0},
                        {"salary_component": adv, "amount": 25000, "depends_on_payment_days": 0}])
    S_WEEK = structure("T7 Weekly", [basic],
                       [{"salary_component": adv, "amount": 6000, "depends_on_payment_days": 0}],
                       freq="Weekly")

    MAP = {union: MAND, adv: COOP, sacco: COOP, welfare: WELF}

    # ---- A. waterfall and tie-break -------------------------------------
    priority("Cash Wages", MAP)
    e1 = employee("SUITE Waterfall")
    assign(e1, S_MIX, 30000)
    s = slip(e1, S_MIX, "2026-08-01", "2026-08-31")
    check("A1 base excludes nothing (cash 30k + OT 6k)", wage_base_of(s), 36000)
    check("A2 cap = 2/3 base", permitted_of(s), 24000)
    check("A3 total == cap", s.total_deduction, 24000)
    check("A4 net == 1/3", s.net_pay, 12000)
    check("A5 Welfare (tier 4) emptied first", amt(s, welfare), 0)
    check("A6 Mandatory never cut", amt(s, union), 1000)
    # Same tier, so the cut is shared pro-rata rather than falling on one of them.
    check("A7 tie: SACCO shares the cut", amt(s, sacco), 5744.17)
    check("A8 tie: advance shares it too", amt(s, adv), 11488.33)
    check("A9 tie: both keep the same fraction",
          round(amt(s, sacco) / 6000, 4), round(amt(s, adv) / 12000, 4))
    check("A10 statutory intact (PAYE on 36k cash)", amt(s, "Pay As You Earn"), 2077.50)

    # ---- B. wage bases --------------------------------------------------
    e2 = employee("SUITE Base")
    assign(e2, S_BEN, 20000)
    priority("Cash Wages", MAP)
    s = slip(e2, S_BEN, "2026-08-01", "2026-08-31")
    check("B1 Cash Wages excludes non-cash benefit", wage_base_of(s), 20000)
    check("B2 gross_pay excludes it too (do_not_include_in_total)", s.gross_pay, 20000)
    check("B3 breach flagged", s.custom_unreducible_excess > 0, True)
    check("B4 net goes negative, not clamped", s.net_pay < 0, True)

    # Where the two bases genuinely part company: absence. Cash Wages nets it
    # off the base; Gross Pay does not, because absence is a deduction row.
    absence = comp("Absence Amount", "ABSA")
    cps.reload()
    if not any(r.salary_component == absence
               for r in cps.statutory_income_component_mapping):
        cps.append("statutory_income_component_mapping",
                   {"salary_component": absence, "category": "Absence / Unpaid Deduction"})
        cps.save(ignore_permissions=True)
    frappe.clear_cache()

    S_ABS = structure("T8 Absence", [basic],
                      [{"salary_component": absence, "amount": 5000, "depends_on_payment_days": 0},
                       {"salary_component": adv, "amount": 15000, "depends_on_payment_days": 0}])
    e9 = employee("SUITE Absence")
    assign(e9, S_ABS, 30000)

    priority("Cash Wages", MAP)
    s = slip(e9, S_ABS, "2026-08-01", "2026-08-31")
    check("B5 Cash Wages nets off absence", wage_base_of(s), 25000)
    check("B6 cap on cash wages", permitted_of(s), 16666.67)
    check("B7 advance trimmed", amt(s, adv), 11666.67)

    priority("Gross Pay", MAP)
    s = slip(e9, S_ABS, "2026-08-01", "2026-08-31")
    check("B8 Gross Pay ignores absence", wage_base_of(s), 30000)
    check("B9 higher cap on gross", permitted_of(s), 20000)
    check("B10 advance untouched on gross base", amt(s, adv), 15000)

    priority("Selected Earnings", MAP, base_components=["Basic Pay"])
    e3 = employee("SUITE Selected")
    assign(e3, S_MIX, 30000)
    s = slip(e3, S_MIX, "2026-08-01", "2026-08-31")
    check("B11 Selected Earnings = Basic only", wage_base_of(s), 30000)
    check("B12 lower cap protects more", permitted_of(s), 20000)

    # ---- C. boundaries --------------------------------------------------
    priority("Cash Wages", MAP)
    e4 = employee("SUITE Exact")
    assign(e4, S_EXACT, 30000)
    s = slip(e4, S_EXACT, "2026-08-01", "2026-08-31")
    check("C1 exactly at cap: no cut", amt(s, adv), 20000)
    check("C2 exactly at cap: not flagged", s.custom_deduction_cap_applied, 0)
    check("C3 exactly at cap: no deferral rows", len(s.custom_deferred_deductions), 0)

    e5 = employee("SUITE Under")
    assign(e5, S_UNDER, 30000)
    s = slip(e5, S_UNDER, "2026-08-01", "2026-08-31")
    check("C4 below cap untouched", amt(s, adv), 5000)
    s.submit()
    check("C5 below cap creates no debt", len(debts(e5)), 0)

    # ---- D. carry forward lifecycle -------------------------------------
    e6 = employee("SUITE Carry")
    assign(e6, S_OVER, 30000)
    s1 = slip(e6, S_OVER, "2026-08-01", "2026-08-31")
    check("D1 cut to cap", amt(s1, adv), 20000)
    check("D2 deferred amount", s1.custom_deferred_deductions[0].deferred_amount, 5000)
    s1.submit()
    d = debts(e6)
    check("D3 one debt raised", len(d), 1)
    check("D4 balance = deferred", d[0].balance_remaining, 5000)
    check("D5 status Pending", d[0].status, "Pending")

    # This period is met before anything is put towards an old debt, and this
    # period alone already exceeds the cap - so the debt is not recovered at all.
    s2 = slip(e6, S_OVER, "2026-09-01", "2026-09-30")
    check("D6 no room to recover", len(s2.custom_brought_forward_deductions), 0)
    check("D7 still capped", s2.total_deduction, 20000)
    s2.submit()
    d = debts(e6)
    check("D8 old debt untouched", d[0].status, "Pending")
    check("D9 old balance unchanged", d[0].balance_remaining, 5000)
    check("D10 new debt raised", len(d), 2)
    check("D11 new debt = 25000-20000", d[1].balance_remaining, 5000)

    s2.reload()
    s2.cancel()
    d = debts(e6)
    check("D12 old balance still intact", d[0].balance_remaining, 5000)
    check("D13 cancel reverts status", d[0].status, "Pending")
    check("D14 cancel voids new debt", d[1].docstatus, 2)

    # ---- E. partial recovery, oldest first ------------------------------
    e7 = employee("SUITE Partial")
    assign(e7, S_PART, 30000)
    p1 = slip(e7, S_PART, "2026-08-01", "2026-08-31")
    check("E1 non-reducible consumes budget", amt(p1, union), 19000)
    check("E2 reducible squeezed to remainder", amt(p1, adv), 1000)
    p1.submit()
    check("E3 debt A raised", debts(e7)[0].balance_remaining, 24000)

    # The non-reducible union dues take 19,000 of a 20,000 cap every period, so
    # this period's own advance is already short and nothing reaches the debts.
    p2 = slip(e7, S_PART, "2026-09-01", "2026-09-30")
    p2.submit()
    d = debts(e7)
    check("E4 debt A not recovered", d[0].balance_remaining, 24000)
    check("E5 status still Pending", d[0].status, "Pending")
    check("E6 debt B raised for own instalment", d[1].balance_remaining, 24000)

    p3 = slip(e7, S_PART, "2026-10-01", "2026-10-31")
    p3.submit()
    d = debts(e7)
    check("E7 oldest debt still first in line", d[0].balance_remaining, 24000)
    check("E8 newer debt untouched", d[1].balance_remaining, 24000)
    check("E9 third debt raised", len(d), 3)

    # ---- F. waive creates no debt ---------------------------------------
    d_before = len(debts(e1))
    s = slip(e1, S_MIX, "2026-08-01", "2026-08-31")
    waived = [r for r in s.custom_deferred_deductions if r.treatment == "Waive"]
    check("F1 waive row present", len(waived), 1)
    s.submit()
    after = debts(e1)
    check("F2 waive raised no debt", len([x for x in after if x.salary_component == welfare]), 0)
    check("F3 carry-forward did raise one", len([x for x in after if x.salary_component == adv]), 1)

    # ---- G. weekly ------------------------------------------------------
    e8 = employee("SUITE Weekly")
    assign(e8, S_WEEK, 8000)
    s = slip(e8, S_WEEK, "2026-08-03", "2026-08-09", freq="Weekly")
    check("G1 weekly base = that week's wages", wage_base_of(s), 8000)
    check("G2 weekly cap", permitted_of(s), 5333.33)
    check("G3 weekly deduction capped", amt(s, adv), 5333.33)

    # ---- H. idempotency and off-switch ----------------------------------
    s = slip(e4, S_EXACT, "2026-11-01", "2026-11-30")
    first = amt(s, adv)
    s.save(ignore_permissions=True)
    s.save(ignore_permissions=True)
    check("H1 re-save is idempotent", amt(s, adv), first)

    cps.reload()
    cps.enable_one_third_rule = 0
    cps.save(ignore_permissions=True)
    frappe.clear_cache()
    s = slip(e6, S_OVER, "2026-12-01", "2026-12-31")
    check("H2 rule off: full deduction", amt(s, adv), 25000)
    check("H3 rule off: nothing flagged", s.custom_deduction_cap_applied, 0)
    check("H4 rule off: no deferral rows", len(s.custom_deferred_deductions), 0)

    # ---- I. no config ---------------------------------------------------
    cps.reload()
    cps.enable_one_third_rule = 1
    cps.save(ignore_permissions=True)
    frappe.delete_doc("Deduction Priority", COMPANY, force=True, ignore_permissions=True)
    frappe.clear_cache()
    s = slip(e6, S_OVER, "2027-01-01", "2027-01-31")
    check("I1 no config: default base still computed", wage_base_of(s), 30000)
    check("I2 no config: nothing reduced", amt(s, adv), 25000)
    check("I3 no config: breach reported", s.custom_unreducible_excess, 5000)

    # ---- J. leavers -----------------------------------------------------
    # Section I deleted the config, so rebuild it before carrying on.
    cps.reload()
    cps.enable_one_third_rule = 1
    cps.save(ignore_permissions=True)
    priority("Cash Wages", MAP)

    e10 = employee("SUITE Leaver")
    assign(e10, S_OVER, 30000)
    frappe.db.set_value("Employee", e10,
                        {"relieving_date": "2026-09-15", "status": "Left"})
    frappe.clear_cache()

    # August is run late, after they were flagged Left. They worked it in full
    # and September can still recover a shortfall, so it is not a final slip.
    aug = slip(e10, S_OVER, "2026-08-01", "2026-08-31")
    check("J1 month worked in full is not final", aug.custom_one_third_rule_skipped, 0)
    check("J2 that month is capped", amt(aug, adv), 20000)
    aug.submit()
    check("J3 debt raised", debts(e10)[0].balance_remaining, 5000)

    sep = slip(e10, S_OVER, "2026-09-01", "2026-09-30")
    check("J4 leaving month is final", sep.custom_one_third_rule_skipped, 1)
    check("J5 instalment + arrear taken in full", amt(sep, adv), 30000)
    check("J6 nothing deferred on the way out", len(sep.custom_deferred_deductions), 0)
    check("J7 leaver slip is not flagged as capped", sep.custom_deduction_cap_applied, 0)
    sep.submit()
    check("J8 arrear cleared", debts(e10)[0].status, "Cleared")
    check("J9 no new debt", len(debts(e10)), 1)

    # Someone still employed at the end of the period is not a leaver, even if
    # their leaving date has already been entered for a future month.
    e11 = employee("SUITE Leaving Later")
    assign(e11, S_OVER, 30000)
    frappe.db.set_value("Employee", e11,
                        {"relieving_date": "2026-12-31", "status": "Active"})
    frappe.clear_cache()
    s = slip(e11, S_OVER, "2026-10-01", "2026-10-31")
    check("J10 leaving date in the future is not final", s.custom_one_third_rule_skipped, 0)
    check("J11 that month is still capped", amt(s, adv), 20000)

    # ---- K. a row ranked away from its group ----------------------------
    # Two deductions in one group, one of them overridden. Without the override
    # they share a rank and the cut is split; with it, the overridden one is the
    # lowest rank in the list and gives way on its own. This is the whole point
    # of the field: a group stays the label people recognise, while one member
    # of it can still be told to go first.
    S_OVR = structure("T8 Override", [basic],
                      [{"salary_component": adv, "amount": 12000,
                        "depends_on_payment_days": 0},
                       {"salary_component": sacco, "amount": 12000,
                        "depends_on_payment_days": 0}])
    e12 = employee("SUITE Override")
    assign(e12, S_OVR, 30000)

    # 30,000 wages, so 20,000 permitted against 24,000 of deductions.
    priority("Cash Wages", {adv: COOP, sacco: COOP})
    s = slip(e12, S_OVR, "2026-10-01", "2026-10-31")
    check("K1 same rank: cut shared pro-rata (advance)", amt(s, adv), 10000)
    check("K2 same rank: cut shared pro-rata (sacco)", amt(s, sacco), 10000)

    priority("Cash Wages", {adv: COOP, sacco: COOP}, overrides={sacco: 9})
    s = slip(e12, S_OVR, "2026-10-01", "2026-10-31")
    check("K3 override: sacco alone bears the whole cut", amt(s, sacco), 8000)
    check("K4 override: advance is left whole", amt(s, adv), 12000)

    dp = frappe.get_doc("Deduction Priority", COMPANY)
    row = next(r for r in dp.deductions if r.salary_component == sacco)
    check("K5 the group's own rank is untouched", row.group_priority, 3)
    check("K6 the override is what moved it", row.override_priority, 9)

    # An override equal to the group's rank says nothing, so it is not kept.
    priority("Cash Wages", {adv: COOP, sacco: COOP}, overrides={sacco: 3})
    dp = frappe.get_doc("Deduction Priority", COMPANY)
    row = next(r for r in dp.deductions if r.salary_component == sacco)
    check("K7 an override matching the group is cleared", cint(row.override_priority), 0)

    # ---- report ---------------------------------------------------------
    passed = sum(1 for r in RESULTS if r[0])
    print(f"\n{'':2}{'test':<48}{'got':>14}{'want':>14}")
    print("  " + "-" * 74)
    for ok, name, got, want in RESULTS:
        mark = "ok  " if ok else "FAIL"
        g = f"{got:,.2f}" if isinstance(got, float) else str(got)
        w = f"{want:,.2f}" if isinstance(want, float) else str(want)
        print(f"{mark} {name:<48}{g:>14}{w:>14}")
    print("  " + "-" * 74)
    print(f"  {passed}/{len(RESULTS)} passed")

    frappe.db.rollback()
