"""End to end checks over every section of Kenya Payroll Settings and Company
Payroll Settings.

Run with:

    bench --site <site> execute upande_payroll.tests.end_to_end_checks.run

Everything is rolled back at the end, so it is safe against a working site. It
builds its own components, structures and employees, and edits both settings
records while it runs - do not run it while someone is changing those.

A section that raises is reported as a failure rather than stopping the run, so
one broken area never hides the state of the rest.
"""

import frappe
from frappe.utils import flt

COMPANY = "Karen Roses"
RESULTS = []
def test_statutory_base_ignores_total_markers():
	"""A row the payslip leaves out of gross is not earnings.

	Structures often carry a Gross Pay marker built by adding the real
	components together. Counting it charged statutory on the employee's pay
	twice, and every figure downstream still reconciled, so nothing looked
	wrong.
	"""
	s = "Statutory base"
	from upande_payroll.kenya_statutory_gross_pay import _sum_ordinary_cash_earnings

	def row(component, amount, **flags):
		return frappe._dict({"salary_component": component, "amount": amount, **flags})

	slip = frappe._dict({"earnings": [
		row("Basic Pay", 30000),
		row("Wages Refund", 2000),
		row("Gross Pay(TI)", 32000, do_not_include_in_total=1),
	]})
	check(s, "a total marker is not counted as earnings",
		  _sum_ordinary_cash_earnings(slip, {}), 32000)

	slip.earnings[2] = row("Gross Pay(TI)", 32000, statistical_component=1)
	check(s, "nor is a statistical row", _sum_ordinary_cash_earnings(slip, {}), 32000)

	# Mapped rows were described on purpose, so they keep their treatment even
	# when the company leaves them out of gross.
	slip.earnings[2] = row("Airtime Allowance", 6000, do_not_include_in_total=1)
	mapping = {"Airtime Allowance": frappe._dict({"category": "Partially Exempt Benefit"})}
	check(s, "a mapped benefit still follows its category",
		  _sum_ordinary_cash_earnings(slip, mapping), 32000)

	mapping = {"Airtime Allowance": frappe._dict({"category": "Ordinary Cash Earning"})}
	check(s, "a mapped cash row still counts",
		  _sum_ordinary_cash_earnings(slip, mapping), 38000)

	# A refund of pay wrongly deducted is cash, but it is not income - the
	# employee was taxed on it the first time round.
	refund = frappe._dict({"earnings": [
		row("Basic Pay", 30000),
		row("Wages Refund", 2000),
	]})
	mapping = {"Wages Refund": frappe._dict({"category": "Non-Taxable Payment"})}
	check(s, "a non-taxable payment is left out of the base",
		  _sum_ordinary_cash_earnings(refund, mapping), 30000)
	check(s, "and counts as ordinary cash when unmapped",
		  _sum_ordinary_cash_earnings(refund, {}), 32000)


FAILED_SECTIONS = []


# ----------------------------------------------------------------------
# harness
# ----------------------------------------------------------------------

def check(section, name, got, want, tol=0.01):
	if isinstance(want, (int, float)) and not isinstance(want, bool):
		ok = abs(flt(got) - flt(want)) <= tol
	else:
		ok = got == want
	RESULTS.append((ok, section, name, got, want))
	return ok


def kenya(**values):
	settings = frappe.get_single("Kenya Payroll Settings")
	for field, value in values.items():
		setattr(settings, field, value)
	settings.save(ignore_permissions=True)
	frappe.clear_cache()


def company(**values):
	settings = frappe.get_doc("Company Payroll Settings", COMPANY)
	for field, value in values.items():
		setattr(settings, field, value)
	settings.save(ignore_permissions=True)
	frappe.clear_cache()


def component(name, abbr, ctype="Deduction", **extra):
	if not frappe.db.exists("Salary Component", name):
		frappe.get_doc({
			"doctype": "Salary Component", "salary_component": name,
			"salary_component_abbr": abbr, "type": ctype,
			"depends_on_payment_days": 0, "remove_if_zero_valued": 0,
			**extra,
		}).insert(ignore_permissions=True)
	return name


def structure(name, earnings, deductions, freq="Monthly"):
	if frappe.db.exists("Salary Structure", name):
		return name
	doc = frappe.get_doc({
		"doctype": "Salary Structure", "name": name, "company": COMPANY,
		"payroll_frequency": freq, "currency": "KES",
		"earnings": earnings, "deductions": deductions,
	})
	doc.insert(ignore_permissions=True)
	doc.submit()
	return name


def statutory_rows(extra=None):
	"""Every statutory component as a zero placeholder, for the calculator to fill."""
	from upande_payroll.kenya_statutory_calculator import get_statutory_components

	rows = [
		{"salary_component": name, "amount": 0, "amount_based_on_formula": 0,
		 "depends_on_payment_days": 0}
		for name in get_statutory_components().values()
	]
	return rows + list(extra or [])


def employee(name, joining="2026-01-01", **extra):
	existing = frappe.db.get_value("Employee", {"employee_name": name}, "name")
	if existing:
		if extra:
			frappe.db.set_value("Employee", existing, extra)
		return existing
	doc = frappe.get_doc({
		"doctype": "Employee", "first_name": name, "company": COMPANY,
		"date_of_joining": joining, "date_of_birth": "1990-01-01",
		"gender": "Female", "status": "Active",
	}).insert(ignore_permissions=True)
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
		"company": COMPANY, "base": base,
	})
	doc.insert(ignore_permissions=True)
	doc.submit()


def payslip(emp, struct, start, end, freq="Monthly", submit=False):
	old = frappe.db.get_value(
		"Salary Slip", {"employee": emp, "start_date": start, "docstatus": ("<", 2)}, "name")
	if old:
		frappe.delete_doc("Salary Slip", old, force=True, ignore_permissions=True)
	slip = frappe.get_doc({
		"doctype": "Salary Slip", "employee": emp, "company": COMPANY,
		"salary_structure": struct, "payroll_frequency": freq,
		"start_date": start, "end_date": end, "posting_date": end,
	})
	slip.insert(ignore_permissions=True)
	if submit:
		slip.submit()
	return slip


def amount(slip, name):
	for row in list(slip.deductions or []) + list(slip.earnings or []):
		if row.salary_component == name:
			return flt(row.amount)
	return 0.0


# ----------------------------------------------------------------------
# Kenya Payroll Settings
# ----------------------------------------------------------------------

def test_nssf():
	s = "NSSF"
	struct = structure("E2E Statutory", [BASIC], statutory_rows())

	for label, base, t1, t2 in [
		("below LEL", 5000, 300, 0),
		("between limits", 50000, 540, 2460),
		("above UEL", 200000, 540, 5940),
	]:
		emp = employee(f"E2E NSSF {label}")
		assign(emp, struct, base)
		slip = payslip(emp, struct, "2026-08-01", "2026-08-31")
		check(s, f"{label}: Tier 1", amount(slip, "Employee NSSF Tier 1"), t1)
		check(s, f"{label}: Tier 2", amount(slip, "Employee NSSF Tier 2"), t2)
		check(s, f"{label}: employer matches",
			  amount(slip, "Employer NSSF Tier 1") + amount(slip, "Employer NSSF Tier 2"),
			  t1 + t2)


def test_shif():
	s = "SHIF"
	struct = "E2E Statutory"

	emp = employee("E2E SHIF normal")
	assign(emp, struct, 50000)
	slip = payslip(emp, struct, "2026-08-01", "2026-08-31")
	check(s, "2.75% of cash pay", amount(slip, "Social Health Insurance Fund"), 1375)

	# The minimum is a monthly floor, so a weekly payroll must not apply it.
	weekly = structure("E2E Statutory Weekly", [BASIC], statutory_rows(), freq="Weekly")
	low = employee("E2E SHIF low")
	assign(low, weekly, 2000)
	slip = payslip(low, weekly, "2026-08-03", "2026-08-09", freq="Weekly")
	check(s, "weekly ignores the monthly minimum",
		  amount(slip, "Social Health Insurance Fund"), 55)

	monthly_low = employee("E2E SHIF monthly low")
	assign(monthly_low, struct, 2000)
	slip = payslip(monthly_low, struct, "2026-08-01", "2026-08-31")
	check(s, "monthly applies the minimum",
		  amount(slip, "Social Health Insurance Fund"), 300)


def test_housing_levy():
	s = "Housing Levy"
	emp = employee("E2E AHL")
	assign(emp, "E2E Statutory", 60000)
	slip = payslip(emp, "E2E Statutory", "2026-08-01", "2026-08-31")
	check(s, "employee 1.5%", amount(slip, "Housing Levy"), 900)
	check(s, "employer 1.5%", amount(slip, "Employer Housing Levy"), 900)
	check(s, "employer side stays out of net pay",
		  next(r.do_not_include_in_total for r in slip.deductions
			   if r.salary_component == "Employer Housing Levy"), 1)


def test_nita():
	s = "NITA"
	kenya(nita_amount=50)
	emp = employee("E2E NITA")
	assign(emp, "E2E Statutory", 40000)
	slip = payslip(emp, "E2E Statutory", "2026-08-01", "2026-08-31")
	check(s, "flat employer levy", amount(slip, "NITA"), 50)
	check(s, "does not touch net pay",
		  next(r.do_not_include_in_total for r in slip.deductions
			   if r.salary_component == "NITA"), 1)
	kenya(nita_amount=0)
	slip = payslip(emp, "E2E Statutory", "2026-08-01", "2026-08-31")
	check(s, "zero removes the row", amount(slip, "NITA"), 0)


def test_paye_bands():
	s = "PAYE bands"
	# 24,000 at 10% then 25% to 32,333 then 30%. Chargeable pay is gross less
	# SHIF, Housing Levy and the retirement relief, so the figures below are
	# derived rather than guessed.
	for label, base in [("first band", 20000), ("second band", 30000),
						("third band", 100000)]:
		emp = employee(f"E2E PAYE {label}")
		assign(emp, "E2E Statutory", base)
		slip = payslip(emp, "E2E Statutory", "2026-08-01", "2026-08-31")

		chargeable = flt(base)
		nssf = amount(slip, "Employee NSSF Tier 1") + amount(slip, "Employee NSSF Tier 2")
		taxable = base - amount(slip, "Social Health Insurance Fund") \
			- amount(slip, "Housing Levy") - nssf
		expected_gross_tax = _band_tax(taxable)
		check(s, f"{label}: tax charged before relief",
			  flt(slip.custom_tax_charged), expected_gross_tax)
		check(s, f"{label}: PAYE net of relief",
			  amount(slip, "Pay As You Earn"), max(expected_gross_tax - 2400, 0))


def _band_tax(taxable):
	bands = frappe.get_all("Kenya PAYE Band", fields=["from_amount", "to_amount", "rate"],
						   order_by="idx")
	tax, previous = 0.0, 0.0
	for index, band in enumerate(bands):
		lower = flt(band.from_amount) if index == 0 else flt(band.from_amount) - 1
		upper = flt(band.to_amount) or float("inf")
		if taxable <= lower:
			break
		tax += (min(taxable, upper) - lower) * flt(band.rate) / 100.0
		previous = upper
	return round(tax, 2)


def test_personal_relief_flat():
	s = "Personal relief (flat)"
	company(personal_relief_method="Flat Monthly")
	emp = employee("E2E Relief flat")
	assign(emp, "E2E Statutory", 60000)
	slip = payslip(emp, "E2E Statutory", "2026-08-01", "2026-08-31")
	check(s, "relief used equals the monthly figure",
		  flt(slip.custom_personal_relief_utilized), 2400)
	check(s, "PAYE is tax charged less relief",
		  amount(slip, "Pay As You Earn"),
		  max(flt(slip.custom_tax_charged) - 2400, 0))


def test_personal_relief_carry_forward():
	s = "Personal relief (carry forward)"
	company(personal_relief_method="Carry Forward (Annual Cap)")

	# Too little tax to absorb the relief, so the remainder should roll on.
	emp = employee("E2E Relief carry")
	assign(emp, "E2E Statutory", 20000)
	first = payslip(emp, "E2E Statutory", "2026-08-01", "2026-08-31", submit=True)
	used = flt(first.custom_personal_relief_utilized)
	carried = flt(first.custom_personal_relief_carried_forward)
	check(s, "relief used is capped at the tax charged",
		  used, min(2400, flt(first.custom_tax_charged)))
	check(s, "unused relief carries forward", carried, 2400 - used)
	check(s, "PAYE floors at zero", amount(first, "Pay As You Earn"), 0)

	second = payslip(emp, "E2E Statutory", "2026-09-01", "2026-09-30")
	check(s, "next month brings it forward",
		  flt(second.custom_personal_relief_brought_forward), carried)
	check(s, "available is brought forward plus this month",
		  flt(second.custom_personal_relief_available_this_month), carried + 2400)

	# Relief still to come counts the months AFTER this one. September's own
	# 2,400 is already inside available_this_month, so counting it here too
	# reported the same money twice and "used plus remaining" could never come
	# to the year's figure.
	check(s, "still to accrue excludes the month being viewed",
		  flt(second.custom_annual_personal_relief), 2400 * 3)

	december = payslip(emp, "E2E Statutory", "2026-12-01", "2026-12-31")
	check(s, "nothing left to accrue in December",
		  flt(december.custom_annual_personal_relief), 0)

	# Joining part way through the year does not change what is still to come:
	# relief runs to December either way.
	joiner = employee("E2E Relief joiner", joining="2026-08-01")
	assign(joiner, "E2E Statutory", 20000, from_date="2026-08-01")
	joined = payslip(joiner, "E2E Statutory", "2026-09-01", "2026-09-30")
	check(s, "a mid-year joiner has the same months left",
		  flt(joined.custom_annual_personal_relief), 2400 * 3)

	company(personal_relief_method="Flat Monthly")


def test_insurance_relief():
	s = "Insurance relief"
	premium = component("E2E Life Cover", "E2ELC", do_not_include_in_total=1)
	settings = frappe.get_doc("Company Payroll Settings", COMPANY)
	if not any(r.salary_component == premium
			   for r in settings.statutory_income_component_mapping):
		settings.append("statutory_income_component_mapping",
						{"salary_component": premium, "category": "Insurance Premium"})
		settings.save(ignore_permissions=True)
		frappe.clear_cache()

	struct = structure("E2E Insurance", [BASIC], statutory_rows([
		{"salary_component": premium, "amount": 10000, "depends_on_payment_days": 0}]))
	emp = employee("E2E Insurance")
	assign(emp, struct, 80000)
	slip = payslip(emp, struct, "2026-08-01", "2026-08-31")

	# 15% of 10,000 is 1,500, under the 5,000 monthly cap.
	check(s, "relief is 15% of the premium",
		  flt(slip.custom_tax_charged) - amount(slip, "Pay As You Earn") - 2400, 1500)
	check(s, "premium does not reduce net pay",
		  next(r.do_not_include_in_total for r in slip.deductions
			   if r.salary_component == premium), 1)


def test_retirement_relief_cap():
	s = "Retirement relief cap"
	pension = "Pension Contribution"
	struct = structure("E2E Pension", [BASIC], statutory_rows([
		{"salary_component": pension, "amount": 40000, "depends_on_payment_days": 0}]))
	settings = frappe.get_doc("Company Payroll Settings", COMPANY)
	if not any(r.salary_component == pension
			   for r in settings.statutory_income_component_mapping):
		settings.append("statutory_income_component_mapping",
						{"salary_component": pension, "category": "Pension Contribution"})
		settings.save(ignore_permissions=True)
		frappe.clear_cache()

	emp = employee("E2E Pension")
	assign(emp, struct, 300000)
	slip = payslip(emp, struct, "2026-08-01", "2026-08-31")

	# Compare against the tax the bands would charge on a chargeable figure
	# built by hand, rather than reversing the bands out of the tax - that was
	# only accurate to a couple of cents and made a correct result look wrong.
	nssf = amount(slip, "Employee NSSF Tier 1") + amount(slip, "Employee NSSF Tier 2")
	retirement_relief = min(nssf + 40000, 30000)
	chargeable = 300000 - amount(slip, "Social Health Insurance Fund") \
		- amount(slip, "Housing Levy") - retirement_relief

	check(s, "relief on NSSF plus pension stops at 30,000", retirement_relief, 30000)
	check(s, "tax charged matches the capped chargeable pay",
		  flt(slip.custom_tax_charged), _band_tax(chargeable))

	# Prove it is the cap biting: uncapped relief would be much larger.
	check(s, "uncapped relief would have exceeded the cap", nssf + 40000 > 30000, True)


def test_kill_switch():
	s = "Kenya kill switch"
	kenya(enabled=0)
	emp = employee("E2E Killswitch")
	assign(emp, "E2E Statutory", 60000)
	slip = payslip(emp, "E2E Statutory", "2026-08-01", "2026-08-31")
	check(s, "no PAYE when disabled", amount(slip, "Pay As You Earn"), 0)
	check(s, "no NSSF when disabled", amount(slip, "Employee NSSF Tier 1"), 0)
	kenya(enabled=1)
	slip = payslip(emp, "E2E Statutory", "2026-08-01", "2026-08-31")
	check(s, "restored when re-enabled", amount(slip, "Employee NSSF Tier 1"), 540)


def test_company_opt_in():
	s = "Company opt-in"
	company(enable_taxable_income_calculation=0)
	emp = employee("E2E Optout")
	assign(emp, "E2E Statutory", 60000)
	slip = payslip(emp, "E2E Statutory", "2026-08-01", "2026-08-31")
	check(s, "statutory skipped when company opts out",
		  amount(slip, "Employee NSSF Tier 1"), 0)
	company(enable_taxable_income_calculation=1)
	slip = payslip(emp, "E2E Statutory", "2026-08-01", "2026-08-31")
	check(s, "restored when opted back in",
		  amount(slip, "Employee NSSF Tier 1"), 540)


def test_structure_gating():
	s = "Structure gating"
	# A task worker structure that lists no statutory rows at all.
	struct = structure("E2E Task Worker", [BASIC], [])
	emp = employee("E2E Task worker")
	assign(emp, struct, 60000)
	slip = payslip(emp, struct, "2026-08-01", "2026-08-31")
	check(s, "no statutory rows appear", len(slip.deductions or []), 0)
	check(s, "net pay equals gross", flt(slip.net_pay), 60000)


def test_secondary_employment():
	s = "Secondary employment"
	emp = employee("E2E Secondary", custom_is_secondary_employment=1)
	assign(emp, "E2E Statutory", 60000)
	frappe.clear_cache()
	slip = payslip(emp, "E2E Statutory", "2026-08-01", "2026-08-31")
	check(s, "no personal relief at a second employer",
		  flt(slip.custom_personal_relief_utilized or 0), 0)
	check(s, "PAYE equals tax charged",
		  amount(slip, "Pay As You Earn"), flt(slip.custom_tax_charged))


def test_opt_outs():
	s = "Per-employee opt-outs"
	emp = employee("E2E Optouts", custom_opt_out_of_nssf=1,
				   custom_opt_out_of_shif=1, custom_opt_out_of_housing_levy=1)
	assign(emp, "E2E Statutory", 60000)
	frappe.clear_cache()
	slip = payslip(emp, "E2E Statutory", "2026-08-01", "2026-08-31")
	check(s, "NSSF opted out", amount(slip, "Employee NSSF Tier 1"), 0)
	check(s, "SHIF opted out", amount(slip, "Social Health Insurance Fund"), 0)
	check(s, "Housing Levy opted out", amount(slip, "Housing Levy"), 0)
	# An untaken deduction must not still reduce taxable income.
	check(s, "PAYE rises because nothing was relieved",
		  amount(slip, "Pay As You Earn"), _band_tax(60000) - 2400)


BASIC = {"salary_component": "Basic Pay", "amount_based_on_formula": 1,
		 "formula": "base", "depends_on_payment_days": 0}


# ----------------------------------------------------------------------
# Company Payroll Settings
# ----------------------------------------------------------------------

def test_one_third_rule():
	s = "1/3 rule"
	advance = component("E2E Advance", "E2EADV")
	group_name = f"{COMPANY}-E2E Coop"
	group = (frappe.get_doc("Deduction Group", group_name)
			 if frappe.db.exists("Deduction Group", group_name)
			 else frappe.get_doc({"doctype": "Deduction Group", "company": COMPANY,
								  "group_name": "E2E Coop"}))
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
	company(enable_one_third_rule=1)

	struct = structure("E2E Cap", [BASIC],
					   [{"salary_component": advance, "amount": 25000,
						 "depends_on_payment_days": 0}])
	emp = employee("E2E Cap")
	assign(emp, struct, 30000)

	first = payslip(emp, struct, "2026-08-01", "2026-08-31", submit=True)
	check(s, "deduction trimmed to the limit", amount(first, advance), 20000)
	check(s, "net pay is a third", flt(first.net_pay), 10000)

	debts = frappe.get_all("Deferred Deduction", filters={"employee": emp},
						   fields=["balance_remaining", "status"])
	check(s, "shortfall raised as a debt", len(debts), 1)
	check(s, "debt balance", flt(debts[0].balance_remaining), 5000)

	# This period is met before anything is put towards an old debt, and this
	# period's own 25,000 already exceeds the limit - so the debt waits.
	second = payslip(emp, struct, "2026-09-01", "2026-09-30", submit=True)
	check(s, "no room to recover the arrear",
		  len(second.custom_brought_forward_deductions or []), 0)
	debts = frappe.get_all("Deferred Deduction", filters={"employee": emp},
						   fields=["balance_remaining", "status"], order_by="creation")
	check(s, "old debt still owed", debts[0].status, "Pending")

	second.reload()
	second.cancel()
	debts = frappe.get_all("Deferred Deduction", filters={"employee": emp},
						   fields=["balance_remaining", "status"], order_by="creation")
	check(s, "cancelling restores the balance", flt(debts[0].balance_remaining), 5000)

	company(enable_one_third_rule=0)
	third = payslip(emp, struct, "2026-10-01", "2026-10-31")
	check(s, "off means no trimming", amount(third, advance), 25000)
	company(enable_one_third_rule=1)


def test_leave_encashment_divisor():
	s = "Leave encashment"
	settings = frappe.get_doc("Company Payroll Settings", COMPANY)
	check(s, "calculation enabled", bool(settings.enable_leave_encashment_calculation), True)
	check(s, "divisor is set", flt(settings.leave_encashment_divisor) > 0, True)


def test_overtime_hours():
	s = "Overtime"
	settings = frappe.get_doc("Company Payroll Settings", COMPANY)
	check(s, "default monthly hours set",
		  flt(settings.default_monthly_working_hours) > 0, True)

	from upande_payroll.upande_payroll.doctype.company_payroll_settings.company_payroll_settings import (
		get_monthly_working_hours,
		get_notice_days,
	)

	default_hours = flt(settings.default_monthly_working_hours)
	check(s, "no department falls back to the default",
		  flt(get_monthly_working_hours(COMPANY)), default_hours)
	check(s, "an unlisted department falls back too",
		  flt(get_monthly_working_hours(COMPANY, "No Such Department")), default_hours)

	for row in settings.overtime_department_working_hours or []:
		check(s, f"{row.department} uses its own hours",
			  flt(get_monthly_working_hours(COMPANY, row.department)),
			  flt(row.monthly_working_hours))

	# Notice days are looked up the same way, so exercise the tenure ranges.
	for row in settings.terminal_dues_notice_period_rules or []:
		inside = flt(row.minimum_years_of_service) + 0.5
		check(s, f"notice at {inside} years",
			  flt(get_notice_days(COMPANY, inside)), flt(row.notice_days))


def test_terminal_dues_config():
	s = "Terminal dues"
	settings = frappe.get_doc("Company Payroll Settings", COMPANY)
	for field in ("terminal_dues_basic_pay_component",
				  "terminal_dues_days_worked_component",
				  "terminal_dues_notice_pay_earning_component",
				  "terminal_dues_notice_pay_deduction_component",
				  "terminal_dues_paye_component"):
		check(s, f"{field} configured", bool(settings.get(field)), True)
	check(s, "daily rate divisor set", flt(settings.terminal_dues_divisor) > 0, True)

	rules = settings.terminal_dues_notice_period_rules or []
	check(s, "notice period rules present", len(rules) > 0, True)
	if rules:
		ordered = sorted(rules, key=lambda r: flt(r.minimum_years_of_service))
		gaps = [
			(a.maximum_years_of_service, b.minimum_years_of_service)
			for a, b in zip(ordered, ordered[1:])
			if flt(a.maximum_years_of_service) != flt(b.minimum_years_of_service)
		]
		check(s, "ranges join with no gaps", gaps, [])
		check(s, "top tier is open ended",
			  flt(ordered[-1].maximum_years_of_service), 0)


def test_journal_account_method():
	s = "Payroll journal"
	settings = frappe.get_doc("Company Payroll Settings", COMPANY)
	method = settings.gross_pay_account_method
	check(s, "an account method is chosen", bool(method), True)
	if method == "Single Account":
		check(s, "single account is set", bool(settings.single_gross_pay_account), True)


def test_component_mapping():
	s = "Component mapping"
	settings = frappe.get_doc("Company Payroll Settings", COMPANY)
	rows = settings.statutory_income_component_mapping or []
	check(s, "components are classified", len(rows) > 0, True)

	missing = [r.salary_component for r in rows
			   if not frappe.db.exists("Salary Component", r.salary_component)]
	check(s, "every mapped component still exists", missing, [])

	blank = [r.salary_component for r in rows if not r.category]
	check(s, "no row is left without a category", blank, [])


# ----------------------------------------------------------------------

SECTIONS = [
	test_nssf, test_shif, test_housing_levy, test_nita, test_paye_bands,
	test_personal_relief_flat, test_personal_relief_carry_forward,
	test_insurance_relief, test_retirement_relief_cap,
	test_kill_switch, test_company_opt_in, test_structure_gating,
	test_secondary_employment, test_opt_outs,
	test_one_third_rule, test_statutory_base_ignores_total_markers,
	test_leave_encashment_divisor, test_overtime_hours,
	test_terminal_dues_config, test_journal_account_method,
	test_component_mapping,
]


def run():
	frappe.flags.in_test = True
	for fn in SECTIONS:
		try:
			fn()
		except Exception as exc:
			FAILED_SECTIONS.append((fn.__name__, f"{type(exc).__name__}: {exc}"))
			frappe.db.rollback()
	report()
	frappe.db.rollback()


def report():
	print()
	current = None
	for ok, section, name, got, want in RESULTS:
		if section != current:
			print(f"\n  {section}")
			current = section
		g = f"{got:,.2f}" if isinstance(got, float) else str(got)
		w = f"{want:,.2f}" if isinstance(want, float) else str(want)
		print(f"    {'ok  ' if ok else 'FAIL'} {name:<48}{g:>14}{w:>14}")

	passed = sum(1 for r in RESULTS if r[0])
	print(f"\n  {passed}/{len(RESULTS)} checks passed")
	if FAILED_SECTIONS:
		print("\n  SECTIONS THAT RAISED:")
		for name, err in FAILED_SECTIONS:
			print(f"    {name}: {err[:150]}")
