"""Seed a demo you can click through in the UI.

    bench --site <site> execute upande_payroll.tests.seed_ui_demo.run

Unlike the check suites, this COMMITS. Every employee is named TEST nn so the
whole lot can be removed again:

    bench --site <site> execute upande_payroll.tests.seed_ui_demo.wipe

Each employee demonstrates exactly one thing, and the guide printed at the end
says what to look at and what it should say.
"""

import frappe
from frappe.utils import flt

COMPANY = "Karen Roses"
NOTES = []


def note(who, what, expect):
    NOTES.append((who, what, expect))


# ----------------------------------------------------------------------
# building blocks
# ----------------------------------------------------------------------

def component(name, abbr, ctype="Deduction", **extra):
    if not frappe.db.exists("Salary Component", name):
        frappe.get_doc({
            "doctype": "Salary Component", "salary_component": name,
            "salary_component_abbr": abbr, "type": ctype,
            "depends_on_payment_days": 0, "remove_if_zero_valued": 0, **extra,
        }).insert(ignore_permissions=True)
    return name


def statutory_rows():
    from upande_payroll.kenya_statutory_calculator import get_statutory_components
    return [{"salary_component": n, "amount": 0, "amount_based_on_formula": 0,
             "depends_on_payment_days": 0}
            for n in get_statutory_components().values()]


def structure(name, earnings, deductions, freq="Monthly"):
    if frappe.db.exists("Salary Structure", name):
        return name
    doc = frappe.get_doc({
        "doctype": "Salary Structure", "name": name, "company": COMPANY,
        "payroll_frequency": freq, "currency": "KES",
        "earnings": earnings, "deductions": deductions})
    doc.insert(ignore_permissions=True)
    doc.submit()
    return name


def employee(label, **extra):
    existing = frappe.db.get_value("Employee", {"employee_name": label}, "name")
    if existing:
        return existing
    doc = frappe.get_doc({
        "doctype": "Employee", "first_name": label, "company": COMPANY,
        "date_of_joining": "2021-03-01", "date_of_birth": "1990-01-01",
        "gender": "Female", "status": "Active"}).insert(ignore_permissions=True)
    if extra:
        frappe.db.set_value("Employee", doc.name, extra)
    return doc.name


def assign(emp, struct, base, from_date="2026-07-01"):
    if frappe.db.exists("Salary Structure Assignment",
                        {"employee": emp, "salary_structure": struct, "docstatus": 1}):
        return
    doc = frappe.get_doc({
        "doctype": "Salary Structure Assignment", "employee": emp,
        "salary_structure": struct, "from_date": from_date,
        "company": COMPANY, "base": base})
    doc.insert(ignore_permissions=True)
    doc.submit()


def payslip(emp, struct, start, end, freq="Monthly", submit=True):
    doc = frappe.get_doc({
        "doctype": "Salary Slip", "employee": emp, "company": COMPANY,
        "salary_structure": struct, "payroll_frequency": freq,
        "start_date": start, "end_date": end, "posting_date": end})
    doc.insert(ignore_permissions=True)
    if submit:
        doc.submit()
    return doc


BASIC = {"salary_component": "Basic Pay", "amount_based_on_formula": 1,
         "formula": "base", "depends_on_payment_days": 0}


# ----------------------------------------------------------------------
# configuration the demo needs
# ----------------------------------------------------------------------

def configure():
    settings = frappe.get_doc("Company Payroll Settings", COMPANY)
    settings.enable_taxable_income_calculation = 1
    settings.enable_one_third_rule = 1
    settings.personal_relief_method = "Flat Monthly"

    expense = frappe.db.get_value("Account", {"company": COMPANY,
                                              "root_type": "Expense",
                                              "is_group": 0}, "name")
    liability = frappe.db.get_value("Account", {"company": COMPANY,
                                               "root_type": "Liability",
                                               "is_group": 0}, "name")
    settings.leave_provision_expense_account = expense
    settings.leave_provision_liability_account = liability
    settings.leave_provision_basic_pay_component = "Basic Pay"
    settings.leave_provision_divisor = 26
    settings.set("leave_provision_leave_types", [])
    settings.append("leave_provision_leave_types", {"leave_type": "Annual Leave"})

    car = component("Company Car Benefit", "CCB", ctype="Earning",
                    do_not_include_in_total=1)
    if not any(r.salary_component == car
               for r in settings.statutory_income_component_mapping):
        settings.append("statutory_income_component_mapping",
                        {"salary_component": car, "category": "Non-Cash Benefit"})
    settings.save(ignore_permissions=True)

    # tiers for the 1/3 rule
    advance = component("Staff Advance", "STADV")
    key = f"{COMPANY}-Cooperative"
    group = (frappe.get_doc("Deduction Group", key)
             if frappe.db.exists("Deduction Group", key)
             else frappe.get_doc({"doctype": "Deduction Group", "company": COMPANY,
                                  "group_name": "Cooperative"}))
    group.priority, group.reducible = 3, 1
    group.on_shortfall, group.tie_breaker = "Carry Forward", "Pro-rata"
    group.save(ignore_permissions=True)

    priority = (frappe.get_doc("Deduction Priority", COMPANY)
                if frappe.db.exists("Deduction Priority", COMPANY)
                else frappe.get_doc({"doctype": "Deduction Priority", "company": COMPANY}))
    priority.wage_base = "Cash Wages"
    priority.set("deductions", [])
    priority.append("deductions", {"salary_component": advance,
                                   "deduction_group": group.name})
    priority.save(ignore_permissions=True)
    frappe.clear_cache()
    return advance, car


# ----------------------------------------------------------------------
# the cases
# ----------------------------------------------------------------------

def statutory_cases(car):
    monthly = structure("DEMO Monthly", [BASIC], statutory_rows())
    weekly = structure("DEMO Weekly", [BASIC], statutory_rows(), freq="Weekly")
    benefit = structure("DEMO Benefit",
                        [BASIC, {"salary_component": car, "amount": 100000,
                                 "depends_on_payment_days": 0}],
                        statutory_rows())
    task = structure("DEMO Task Worker", [BASIC], [])

    emp = employee("TEST 01 Normal Monthly")
    assign(emp, monthly, 80000)
    payslip(emp, monthly, "2026-08-01", "2026-08-31")
    note("TEST 01", "Salary Slip Aug",
         "NSSF 540 + 4,260, SHIF 2,200, Housing Levy 1,200, PAYE on 80,000")

    emp = employee("TEST 02 Low Pay Monthly")
    assign(emp, monthly, 8000)
    payslip(emp, monthly, "2026-08-01", "2026-08-31")
    note("TEST 02", "Salary Slip Aug",
         "SHIF is 300, the monthly minimum, not 2.75% of 8,000 (220)")

    emp = employee("TEST 03 Weekly Low Pay")
    assign(emp, weekly, 2000)
    payslip(emp, weekly, "2026-08-03", "2026-08-09", freq="Weekly")
    note("TEST 03", "Salary Slip 03-09 Aug",
         "SHIF is 55 (2.75%), NOT 300 - the minimum is monthly only")

    emp = employee("TEST 04 Non Cash Benefit")
    assign(emp, benefit, 40000)
    payslip(emp, benefit, "2026-08-01", "2026-08-31")
    note("TEST 04", "Salary Slip Aug, 1/3 Rule section",
         "Wage Base 40,000 not 140,000 - the car benefit is taxed but is not cash")

    emp = employee("TEST 05 Secondary Employment",
                   custom_is_secondary_employment=1)
    assign(emp, monthly, 60000)
    frappe.clear_cache()
    payslip(emp, monthly, "2026-08-01", "2026-08-31")
    note("TEST 05", "Salary Slip Aug, Personal Relief fields",
         "Relief used is 0 and PAYE equals Tax Charged - no relief at a 2nd employer")

    emp = employee("TEST 06 Statutory Opt Outs", custom_opt_out_of_nssf=1,
                   custom_opt_out_of_shif=1, custom_opt_out_of_housing_levy=1)
    assign(emp, monthly, 60000)
    frappe.clear_cache()
    payslip(emp, monthly, "2026-08-01", "2026-08-31")
    note("TEST 06", "Salary Slip Aug",
         "No NSSF, SHIF or Housing Levy rows. PAYE is HIGHER than TEST 01 "
         "pro rata, because nothing was deducted so nothing relieved")

    emp = employee("TEST 07 Task Worker")
    assign(emp, task, 30000)
    payslip(emp, task, "2026-08-01", "2026-08-31")
    note("TEST 07", "Salary Slip Aug",
         "No deductions at all. Net = gross 30,000. The structure lists none, "
         "so this employee is not subject to them")


def relief_case():
    monthly = "DEMO Monthly"
    settings = frappe.get_doc("Company Payroll Settings", COMPANY)
    settings.personal_relief_method = "Carry Forward (Annual Cap)"
    settings.save(ignore_permissions=True)
    frappe.clear_cache()

    emp = employee("TEST 08 Relief Carry Forward")
    assign(emp, monthly, 20000)
    payslip(emp, monthly, "2026-08-01", "2026-08-31")
    payslip(emp, monthly, "2026-09-01", "2026-09-30")
    note("TEST 08", "Salary Slips Aug then Sep",
         "Aug: relief used < 2,400 and the rest Carried Forward. "
         "Sep: Brought Forward equals Aug's carried figure")

    settings.reload()
    settings.personal_relief_method = "Flat Monthly"
    settings.save(ignore_permissions=True)
    frappe.clear_cache()


def one_third_case(advance):
    struct = structure("DEMO One Third", [BASIC],
                       statutory_rows() + [{"salary_component": advance,
                                            "amount": 25000,
                                            "depends_on_payment_days": 0}])
    emp = employee("TEST 09 One Third Rule")
    assign(emp, struct, 30000)
    payslip(emp, struct, "2026-08-01", "2026-08-31")
    payslip(emp, struct, "2026-09-01", "2026-09-30")
    note("TEST 09", "Salary Slips Aug and Sep, plus Deferred Deduction list",
         "Advance trimmed so net pay is a third of 30,000. A Deferred Deduction "
         "is raised in Aug and recovered in Sep - see Brought Forward Deductions")

    # A higher wage than TEST 09 on purpose. The final slip takes the arrear on
    # top of the instalment, and HRMS refuses to submit a payslip whose net pay
    # is below zero - so a leaver whose arrears exceed their final dues cannot be
    # processed through payroll at all. 60,000 keeps this one submittable.
    leaver = employee("TEST 10 Leaver Final Slip")
    assign(leaver, struct, 60000)
    payslip(leaver, struct, "2026-08-01", "2026-08-31")
    frappe.db.set_value("Employee", leaver,
                        {"relieving_date": "2026-09-30", "status": "Left"})
    frappe.clear_cache()
    payslip(leaver, struct, "2026-09-01", "2026-09-30")
    note("TEST 10", "Salary Slip Sep",
         "1/3 Rule Not Applied is ticked. The advance plus the August arrear "
         "come out in full - a final slip has no later payslip to recover from")


def terminal_accounts():
	"""A settlement posts its own journal, so it needs somewhere to post to.

	Without these it refuses to submit - "Could not resolve the following
	accounts" - which is the right behaviour, but it means a settlement cannot
	be seeded without naming them.
	"""
	def pick(root_type, *patterns):
		for pattern in patterns:
			name = frappe.db.get_value("Account", {
				"company": COMPANY, "is_group": 0, "root_type": root_type,
				"name": ("like", pattern)}, "name")
			if name:
				return name
		return frappe.db.get_value("Account", {
			"company": COMPANY, "is_group": 0, "root_type": root_type}, "name")

	return {
		"salary_expense_account": pick("Expense", "%Salary%", "%Wages%"),
		"payroll_payable_account": pick("Liability", "%Payroll Payable%", "%Payable%"),
		"paye_account": pick("Liability", "%PAYE%", "%Tax%", "%Dut%"),
	}


def set_days_worked(doc, days):
    """Days worked has to be set on the second save, not the first.

    The first save fetches the dues, and part of that is counting attendance
    for the final month - which overwrites whatever was passed in. These demo
    employees have no attendance records, so it counted zero and every figure
    derived from a day's pay came out nil. Setting it afterwards is also what a
    payroll officer does in the UI: open the settlement, correct Days Worked in
    Final Month, save again.
    """
    doc.days_worked_in_final_month = days
    doc.save(ignore_permissions=True)


def terminal_dues_cases():
    struct = "DEMO Monthly"
    settings = frappe.get_doc("Company Payroll Settings", COMPANY)

    for label, mode in [("TEST 11 Terminal Full Statutory", "All Statutory"),
                        ("TEST 12 Terminal PAYE Only", "PAYE Only")]:
        settings.reload()
        settings.terminal_dues_statutory_deductions = mode
        settings.save(ignore_permissions=True)
        frappe.clear_cache()

        emp = employee(label)
        assign(emp, struct, 80000)
        payslip(emp, struct, "2026-07-01", "2026-07-31")
        frappe.db.set_value("Employee", emp,
                            {"relieving_date": "2026-08-31", "status": "Left",
                             "resignation_letter_date": "2026-08-01"})
        frappe.clear_cache()

        doc = frappe.get_doc({
            "doctype": "Terminal Dues Settlement", "employee": emp,
            "company": COMPANY, "relieving_date": "2026-08-31",
            "payroll_period_start": "2026-08-01",
            "days_worked_in_final_month": 20,
            "notice_direction": "Payable to Employee",
            "notice_days_served": 10,
            **terminal_accounts()})
        doc.insert(ignore_permissions=True)
        set_days_worked(doc, 20)
        doc.submit()
        note(label.split(" Terminal")[0],
             f"Terminal Dues Settlement ({mode})",
             "All Statutory shows NSSF, SHIF and Housing Levy rows and a LOWER "
             "PAYE. PAYE Only shows tax alone and a higher PAYE"
             if mode == "All Statutory" else
             "Only PAYE in the deductions. Compare its PAYE with TEST 11")

    # no payslip at all - the basic_pay fallback
    emp = employee("TEST 13 No Payslip Basic Pay")
    frappe.db.set_value("Employee", emp, {
        "basic_pay": 52000, "relieving_date": "2026-08-31", "status": "Left"})
    frappe.clear_cache()
    doc = frappe.get_doc({
        "doctype": "Terminal Dues Settlement", "employee": emp,
        "company": COMPANY, "relieving_date": "2026-08-31",
        "payroll_period_start": "2026-08-01",
        "days_worked_in_final_month": 15,
        "notice_direction": "",
        **terminal_accounts()})
    doc.insert(ignore_permissions=True)
    set_days_worked(doc, 15)
    doc.submit()
    note("TEST 13", "Terminal Dues Settlement",
         "This employee has NO payslip. Days Worked Pay is 15 x (52,000/26) "
         "= 30,000, read from Basic Pay on the Employee record")


def leave_provision_case():
    struct = "DEMO Monthly"
    for label, base, days in [("TEST 14 Leave Balance Big", 52000, 12),
                              ("TEST 15 Leave Balance Small", 26000, 5)]:
        emp = employee(label)
        assign(emp, struct, base)
        payslip(emp, struct, "2026-07-01", "2026-07-31")
        alloc = frappe.get_doc({
            "doctype": "Leave Allocation", "employee": emp,
            "leave_type": "Annual Leave", "from_date": "2026-07-01",
            "to_date": "2027-06-30", "new_leaves_allocated": days,
            "company": COMPANY})
        alloc.insert(ignore_permissions=True)
        alloc.submit()

    doc = frappe.get_doc({
        "doctype": "Leave Provision", "company": COMPANY,
        "from_date": "2026-07-01", "to_date": "2026-07-31",
        "group_by": "Nothing"})
    doc.insert(ignore_permissions=True)
    doc.submit()
    note("TEST 14/15", f"Leave Provision {doc.name}",
         "12 days at 52,000/26 = 24,000 plus 5 days at 26,000/26 = 5,000. "
         "Movement equals the whole liability on the first one. "
         "Check the Journal Entry it created")


# ----------------------------------------------------------------------

def run():
    advance, car = configure()
    frappe.db.commit()

    # Each stage commits on its own, and a stage that breaks does not take the
    # others down with it. The first version rolled everything back when one
    # case failed, which left nothing seeded and nothing to look at.
    failures = []
    for label, fn in [("statutory", lambda: statutory_cases(car)),
                      ("personal relief", relief_case),
                      ("1/3 rule", lambda: one_third_case(advance)),
                      ("terminal dues", terminal_dues_cases),
                      ("leave provision", leave_provision_case)]:
        try:
            fn()
            frappe.db.commit()
        except Exception as exc:
            frappe.db.rollback()
            message = frappe.utils.strip_html(str(exc)).strip().replace("\n", " ")
            failures.append((label, f"{type(exc).__name__}: {message[:200]}"))

    print("\n" + "=" * 88)
    print("  WHAT TO CHECK")
    print("=" * 88)
    for who, what, expect in NOTES:
        print(f"\n  {who}  ->  {what}")
        for line in _wrap(expect, 78):
            print(f"      {line}")

    print("\n" + "=" * 88)
    print("  Reports worth opening: Company Register, National Social Security Fund,")
    print("  Affordable Housing Levy, Social Health Insurance Fund, Leave Liability")
    print("\n  To remove all of this:")
    print("    bench --site <site> execute upande_payroll.tests.seed_ui_demo.wipe")
    print("=" * 88)

    if failures:
        print("\n  STAGES THAT DID NOT SEED")
        for label, message in failures:
            print(f"    {label}: {message}")
        print("=" * 88)


def _wrap(text, width):
    words, line, out = text.split(), "", []
    for word in words:
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


def wipe():
    """Remove everything this seeder made."""
    employees = frappe.get_all("Employee",
                              filters={"employee_name": ("like", "TEST %")},
                              pluck="name")
    order = [
        ("Journal Entry", None),
        ("Leave Provision", None),
        ("Deferred Deduction", {"employee": ("in", employees)} if employees else None),
        ("Terminal Dues Settlement", {"employee": ("in", employees)} if employees else None),
        ("Salary Slip", {"employee": ("in", employees)} if employees else None),
        ("Leave Allocation", {"employee": ("in", employees)} if employees else None),
        ("Salary Structure Assignment", {"employee": ("in", employees)} if employees else None),
        ("Salary Structure", {"name": ("like", "DEMO %")}),
    ]

    removed = 0
    for doctype, filters in order:
        if filters is None and doctype != "Journal Entry" and doctype != "Leave Provision":
            continue
        names = frappe.get_all(doctype, filters=filters or {}, pluck="name")
        for name in names:
            try:
                doc = frappe.get_doc(doctype, name)
                if doc.docstatus == 1:
                    doc.flags.ignore_permissions = True
                    doc.flags.ignore_links = True
                    doc.cancel()
                frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
                removed += 1
            except Exception as exc:
                print(f"    ! {doctype} {name}: {str(exc)[:70]}")
        frappe.db.commit()

    for name in employees:
        try:
            frappe.delete_doc("Employee", name, force=True, ignore_permissions=True)
            removed += 1
        except Exception as exc:
            print(f"    ! Employee {name}: {str(exc)[:70]}")

    frappe.db.commit()
    print(f"  removed {removed} documents and {len(employees)} employees")
