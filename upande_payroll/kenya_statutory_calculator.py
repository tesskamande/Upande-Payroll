import frappe
from frappe.utils import flt, getdate

from upande_payroll.kenya_statutory_gross_pay import get_income_breakdown


def apply_regional_deductions(doc):
	"""Kenya statutory deductions for Salary Slip.

	Wired via hooks.py's ``regional_overrides`` against HRMS's own
	``apply_regional_deductions`` extension point - not a doc_event, since
	this is exactly the case that extension point exists for.

	Three levels of gating, narrowest last:
	  1. Kenya Payroll Settings.enabled - national kill-switch.
	  2. Company Payroll Settings.enable_taxable_income_calculation - the
	     per-company opt-in every other feature in this app also uses.
	  3. The employee's own Salary Structure - a statutory component is only
	     computed if that structure actually lists it as a deduction row. This
	     is what lets task workers (whose structure omits NSSF/SHIF/AHL/PAYE)
	     run through the same company payroll without being deducted, while
	     regular staff on a structure that does list them get the full set.
	"""
	settings = frappe.get_cached_doc("Company Payroll Settings", doc.company)
	if not settings.enable_taxable_income_calculation:
		doc.custom_personal_relief_method = ""
		_remove_managed_components(doc, settings)
		return
	doc.custom_personal_relief_method = settings.personal_relief_method

	kenya_settings = frappe.get_cached_doc("Kenya Payroll Settings")
	if not kenya_settings.enabled:
		_remove_managed_components(doc, settings)
		return

	# Read the structure itself, not the slip's rows: a placeholder row with
	# amount 0 is dropped by core before this hook runs when the component has
	# remove_if_zero_valued set (salary_slip.py's update_component_row), so
	# slip-row presence is not a reliable signal. The structure is.
	structure = _get_structure_deduction_components(doc)
	allowed = structure.allowed
	by_formula = structure.by_formula

	emp = frappe.db.get_value(
		"Employee", doc.employee,
		["custom_is_secondary_employment", "custom_opt_out_of_nssf",
		 "custom_opt_out_of_shif", "custom_opt_out_of_housing_levy"],
		as_dict=True,
	) or frappe._dict()
	is_secondary = bool(emp.custom_is_secondary_employment)

	breakdown = get_income_breakdown(doc, settings, is_secondary=is_secondary)
	cash_base = breakdown.statutory_cash_base

	# Employee-side deductions first, employer-side contributions last (for
	# readability - employer contributions don't reduce the employee's own
	# take-home, so grouping them at the bottom keeps what actually affects
	# net pay together, up top).
	nssf = compute_nssf(cash_base, kenya_settings)
	shif = compute_shif(cash_base, kenya_settings, doc.payroll_frequency)
	ahl = compute_housing_levy(cash_base, kenya_settings)

	# Per-employee opt-outs. Needed because a secondary employee usually sits on
	# the same Salary Structure as everyone else, so structure-level gating
	# cannot single them out. NSSF Tier I/II caps and the SHIF entitlement are
	# per-employee rather than per-employer, so deducting at a second employer
	# over-contributes. Zeroing here rather than at the point of writing the row
	# matters: the amounts feed the relief arithmetic below, and a contribution
	# that was never deducted must not reduce taxable income either.
	if emp.custom_opt_out_of_nssf:
		nssf = frappe._dict({k: 0.0 for k in nssf})
	if emp.custom_opt_out_of_shif:
		shif = 0.0
	if emp.custom_opt_out_of_housing_levy:
		ahl = frappe._dict({"employee": 0.0, "employer": 0.0})

	comp = get_statutory_components()

	# Keep what each one actually came to. Where the structure computes a
	# component itself these differ from the figures above, and it is the amount
	# the employee really loses that may reduce their taxable income.
	paid_nssf_t1 = _set_amount(doc, comp.nssf_tier1_employee, nssf.tier1_employee,
							   allowed, by_formula)
	paid_nssf_t2 = _set_amount(doc, comp.nssf_tier2_employee, nssf.tier2_employee,
							   allowed, by_formula)
	paid_shif = _set_amount(doc, comp.shif, shif, allowed, by_formula)
	paid_ahl = _set_amount(doc, comp.housing_levy_employee, ahl.employee,
						   allowed, by_formula)

	# PAYE. SHIF/Housing Levy reduce taxable income in full - that rule is
	# universal, not a company policy choice, so it doesn't need per-company
	# tagging in Statutory Income Component Mapping. NSSF and Pension
	# Contribution are different: Kenyan law caps their COMBINED relief at
	# Kenya Payroll Settings' Retirement Relief Cap (KES 30,000/month) - the
	# actual NSSF deduction still comes out of the employee's pay in full via
	# its own tier caps above, but only up to this combined cap reduces what
	# gets taxed.
	# Only deductions the employee actually bears may reduce their taxable
	# income - a component skipped because the structure omits it was never
	# deducted, so it cannot also grant tax relief.
	relief_shif = paid_shif
	relief_ahl = paid_ahl
	relief_nssf = paid_nssf_t1 + paid_nssf_t2

	# Retirement contributions relief is itself a relief, so a secondary
	# employer allows none of it (breakdown.pension_contribution is already 0
	# in that case; the NSSF side is zeroed here).
	if is_secondary:
		retirement_relief = 0.0
	else:
		retirement_contributions = relief_nssf + breakdown.pension_contribution
		retirement_cap = flt(kenya_settings.retirement_relief_cap)
		retirement_relief = (
			min(retirement_contributions, retirement_cap) if retirement_cap else retirement_contributions
		)

	# SHIF and Housing Levy deductibility is a rule about those specific levies,
	# not a personal relief, so it survives into secondary employment.
	taxable_income = max(breakdown.taxable_income - relief_shif - relief_ahl - retirement_relief, 0.0)

	if is_secondary:
		gross_paye = compute_secondary_paye(taxable_income, kenya_settings)
		relief_utilized = 0.0
		_clear_relief_fields(doc)
	else:
		relief = compute_personal_relief(doc, settings, kenya_settings, taxable_income)
		gross_paye = relief.gross_paye
		relief_utilized = relief.relief_utilized

	# Insurance relief (Income Tax Act s.31) is a credit against tax, like
	# personal relief - not a deduction from taxable income. It survives into
	# secondary employment: the disallowed reliefs there are Personal, Owner-
	# Occupied Mortgage Interest and Pension.
	insurance_relief = compute_insurance_relief(breakdown.insurance_premium, kenya_settings)

	doc.custom_tax_charged = flt(gross_paye, 2)
	paye = max(gross_paye - relief_utilized - insurance_relief, 0.0)
	_set_amount(doc, comp.paye, flt(paye, 2), allowed, by_formula)

	# Employer contributions last - these never touch the employee's net pay
	# (statistical/do_not_include_in_total), grouped at the bottom of the list.
	_set_amount(doc, comp.nssf_tier1_employer, nssf.tier1_employer, allowed, by_formula)
	_set_amount(doc, comp.nssf_tier2_employer, nssf.tier2_employer, allowed, by_formula)
	_set_amount(doc, comp.housing_levy_employer, ahl.employer, allowed, by_formula)
	_set_amount(doc, comp.nita, flt(kenya_settings.nita_amount), allowed, by_formula)


# ----------------------------------------------------------------------
# NSSF
# ----------------------------------------------------------------------

def compute_nssf(cash_base, kenya_settings):
	"""NSSF Tier I + Tier II per the NSSF Act 2013 phased schedule. Tier II is
	bounded by BOTH the contribution-rate cap and the Upper Earnings Limit
	itself, so a high earner's Tier II never exceeds the true statutory
	ceiling (unlike a naive rate-cap-only implementation)."""
	tier1_rate = flt(kenya_settings.nssf_tier1_rate) / 100
	tier2_rate = flt(kenya_settings.nssf_tier2_rate) / 100
	lel = flt(kenya_settings.nssf_lower_earnings_limit)
	uel = flt(kenya_settings.nssf_upper_earnings_limit)
	tier1_cap = flt(kenya_settings.nssf_tier1_cap)
	tier2_cap = flt(kenya_settings.nssf_tier2_cap)

	tier1_base = min(cash_base, lel) if cash_base > 0 else 0.0
	tier1 = min(tier1_base * tier1_rate, tier1_cap) if tier1_cap else tier1_base * tier1_rate

	tier2_base = max(min(cash_base, uel) - lel, 0.0) if cash_base > 0 else 0.0
	tier2 = min(tier2_base * tier2_rate, tier2_cap) if tier2_cap else tier2_base * tier2_rate

	tier1 = flt(tier1, 2)
	tier2 = flt(tier2, 2)
	return frappe._dict({
		"tier1_employee": tier1, "tier1_employer": tier1,
		"tier2_employee": tier2, "tier2_employer": tier2,
	})


# ----------------------------------------------------------------------
# SHIF - frequency sensitive: the KES 300 monthly minimum only applies when
# the payroll actually runs monthly. Applying it per-period on a weekly or
# fortnightly run would floor each of that month's several payslips at 300,
# vastly overcharging sub-monthly-paid employees relative to monthly ones.
# ----------------------------------------------------------------------

def compute_shif(cash_base, kenya_settings, payroll_frequency):
	rate = flt(kenya_settings.shif_rate) / 100
	amount = cash_base * rate
	if payroll_frequency == "Monthly":
		amount = max(amount, flt(kenya_settings.shif_minimum))
	return flt(amount, 2)


# ----------------------------------------------------------------------
# Affordable Housing Levy - no upper limit, same base both sides (per
# explicit instruction: employer AHL uses the same Statutory Cash Base as
# employee AHL, not a broader emoluments base).
# ----------------------------------------------------------------------

def compute_housing_levy(cash_base, kenya_settings):
	employee = cash_base * (flt(kenya_settings.ahl_employee_rate) / 100)
	employer = cash_base * (flt(kenya_settings.ahl_employer_rate) / 100)
	return frappe._dict({"employee": flt(employee, 2), "employer": flt(employer, 2)})


# ----------------------------------------------------------------------
# PAYE
# ----------------------------------------------------------------------

def compute_gross_paye(taxable_income, kenya_settings):
	"""Progressive monthly PAYE from Kenya Payroll Settings' own bands - not
	core's Income Tax Slab, which is annual (kept for Gratuity's spreading
	calculation) and unsuited to a monthly-cadence Salary Slip.

	Bands are stored the way KRA publishes them (e.g. Band 2 "From Amount" =
	24,001, matching "24,001 - 32,333"), not as shared continuous thresholds.
	Every band except the first is therefore an inclusive start - the true
	continuous threshold for the width calculation is one shilling below it,
	so we subtract 1 before comparing/subtracting. The first band keeps its
	from_amount of 0 as-is, since "up to 24,000" has no such offset."""
	tax = 0.0
	for idx, band in enumerate(kenya_settings.paye_bands or []):
		lower = flt(band.from_amount) if idx == 0 else flt(band.from_amount) - 1
		upper = flt(band.to_amount)
		rate = flt(band.rate) / 100
		if taxable_income <= lower:
			break
		ceiling = upper if upper > 0 else taxable_income
		taxable_in_band = min(taxable_income, ceiling) - lower
		if taxable_in_band > 0:
			tax += taxable_in_band * rate
	return flt(tax, 2)


def get_top_paye_rate(kenya_settings):
	"""Highest configured PAYE band rate, read from Kenya Payroll Settings
	rather than hardcoded.

	The convention for secondary employment is "withhold at the top band" - the
	commonly quoted figure was 30% before the Finance Act 2023 and 35% after,
	because the top band itself moved. Reading it from config means it tracks
	future band changes instead of going stale."""
	bands = kenya_settings.paye_bands or []
	if not bands:
		return 0.0
	top = max(bands, key=lambda b: flt(b.from_amount))
	return flt(top.rate)


def compute_secondary_paye(taxable_income, kenya_settings):
	"""PAYE for secondary employment: the top band applied flat, with no
	progressive banding and no reliefs.

	Basis: Income Tax Act s.30 restricts personal relief to one employer, and
	KRA's Employer's Guide confirms relief comes from the main employment only.
	The flat top-band rate itself is established payroll practice rather than a
	provision we can cite - it deliberately over-withholds, because a secondary
	employer cannot see the employee's combined income. The employee reconciles
	at year end across both P9 forms."""
	return flt(taxable_income * get_top_paye_rate(kenya_settings) / 100, 2)


def _clear_relief_fields(salary_slip):
	"""Secondary employment gets no personal relief, so the carry-forward
	ledger must read as zero rather than showing a stale chain."""
	salary_slip.custom_personal_relief_brought_forward = 0
	salary_slip.custom_personal_relief_available_this_month = 0
	salary_slip.custom_personal_relief_utilized = 0
	salary_slip.custom_personal_relief_carried_forward = 0
	salary_slip.custom_annual_personal_relief = 0


def compute_insurance_relief(premium, kenya_settings):
	"""15% of qualifying premiums, capped per month - both read from Kenya
	Payroll Settings rather than hardcoded, so a Finance Act change to either
	is a settings edit.

	Income Tax Act s.31: relief on premiums paid for life, education or health
	policies, for self, spouse or child."""
	premium = flt(premium)
	if premium <= 0:
		return 0.0
	relief = premium * flt(kenya_settings.insurance_relief_rate) / 100
	cap = flt(kenya_settings.monthly_insurance_relief_cap)
	if cap:
		relief = min(relief, cap)
	return flt(relief, 2)


def compute_personal_relief(salary_slip, settings, kenya_settings, taxable_income):
	"""Returns {gross_paye, relief_utilized}. Flat Monthly: relief is simply
	capped at whatever's owed that month. Carry Forward: unused relief from
	months where tax owed was less than the relief rolls into next month,
	chained via the previous Salary Slip - same mechanism Karen Roses already
	uses (Personal Relief Update Server Script on Kaitet_2), rebuilt to read
	Kenya Payroll Settings instead of hardcoding the relief amount and PAYE
	bands, and fixed to use all 5 bands instead of a fixed cap. This method is
	a per-company choice (Company Payroll Settings.personal_relief_method),
	not a national default - some companies carry forward, some don't."""
	monthly_relief = flt(kenya_settings.monthly_personal_relief)
	gross_paye = compute_gross_paye(taxable_income, kenya_settings)

	if settings.personal_relief_method != "Carry Forward (Annual Cap)":
		relief_utilized = min(gross_paye, monthly_relief)

		# Record what was relieved even though nothing is being carried. The
		# figure is just as true here as under carry forward, and the P9 and
		# P10 read it for their relief columns - leaving it unset made every
		# month on a Flat Monthly company report no relief at all.
		salary_slip.custom_personal_relief_brought_forward = 0
		salary_slip.custom_personal_relief_available_this_month = monthly_relief
		salary_slip.custom_personal_relief_utilized = relief_utilized
		salary_slip.custom_personal_relief_carried_forward = 0
		salary_slip.custom_annual_personal_relief = 0

		return frappe._dict({"gross_paye": gross_paye, "relief_utilized": relief_utilized})

	return _compute_relief_carry_forward(salary_slip, kenya_settings, gross_paye, monthly_relief)


def _compute_relief_carry_forward(salary_slip, kenya_settings, gross_paye, monthly_relief):
	end_date = getdate(salary_slip.end_date)
	current_month = end_date.month
	date_of_joining = frappe.db.get_value("Employee", salary_slip.employee, "date_of_joining")
	doj = getdate(date_of_joining) if date_of_joining else None
	joined_this_year = bool(doj and doj.year == end_date.year)

	# Brought-forward relief: chained from the previous submitted/draft slip's
	# carried-forward value. January (or no prior slip) falls back to a
	# manual seed field, for cases needing a starting balance.
	if current_month == 1 and not joined_this_year:
		relief_bf = flt(salary_slip.get("custom_personal_relief_brought_forward"))
	else:
		prev = frappe.db.sql(
			"""
			SELECT custom_personal_relief_carried_forward
			FROM `tabSalary Slip`
			WHERE employee = %s AND docstatus IN (0, 1) AND end_date < %s
			ORDER BY end_date DESC LIMIT 1
			""",
			(salary_slip.employee, salary_slip.end_date),
			as_dict=True,
		)
		relief_bf = flt(prev[0].custom_personal_relief_carried_forward) if prev else 0.0

	relief_available = relief_bf + monthly_relief
	relief_utilized = min(gross_paye, relief_available)
	relief_carried_forward = relief_available - relief_utilized

	if joined_this_year:
		eligible_months = 12 - doj.month + 1
		annual_cap = eligible_months * monthly_relief
		months_elapsed = max(current_month - doj.month, 0)
	else:
		annual_cap = monthly_relief * 12
		months_elapsed = current_month - 1

	annual_cap_remaining = max(annual_cap - (monthly_relief * months_elapsed), 0.0)

	salary_slip.custom_personal_relief_brought_forward = relief_bf
	salary_slip.custom_personal_relief_available_this_month = relief_available
	salary_slip.custom_personal_relief_utilized = relief_utilized
	salary_slip.custom_personal_relief_carried_forward = relief_carried_forward
	salary_slip.custom_annual_personal_relief = annual_cap_remaining

	return frappe._dict({"gross_paye": gross_paye, "relief_utilized": relief_utilized})


# ----------------------------------------------------------------------
# Generic component application - mirrors the create-if-missing /
# statistical-if-employer-only pattern, but reads is_employer_contribution
# from the Salary Component master (config-driven) instead of a hardcoded
# per-component Python flag.
# ----------------------------------------------------------------------

STATUTORY_COMPONENTS = frappe._dict({
	"nssf_tier1_employee": "Employee NSSF Tier 1",
	"nssf_tier2_employee": "Employee NSSF Tier 2",
	"shif": "Social Health Insurance Fund",
	"housing_levy_employee": "Housing Levy",
	"paye": "Pay As You Earn",
	"nssf_tier1_employer": "Employer NSSF Tier 1",
	"nssf_tier2_employer": "Employer NSSF Tier 2",
	"housing_levy_employer": "Employer Housing Levy",
	"nita": "NITA",
})


def get_statutory_components(settings=None):
	"""The Salary Components this calculator writes to.

	These ship with the app as fixtures, so every site has the same set and
	there is nothing to configure or misconfigure. A company adopting the app
	adapts to these names rather than the app chasing each company's own.
	Naming convention: an "Employee"/"Employer" prefix only where both sides of
	a contribution exist - SHIF has no employer match, so it carries none."""
	return STATUTORY_COMPONENTS


def _remove_managed_components(salary_slip, settings=None):
	"""Clean up stale rows from a prior calculation if statutory deduction
	calculation gets disabled for this company (or Kenya Payroll Settings
	itself) after having previously been on."""
	managed = set(STATUTORY_COMPONENTS.values())
	salary_slip.deductions = [
		r for r in (salary_slip.deductions or [])
		if r.salary_component not in managed
	]


def _get_structure_deduction_components(salary_slip):
	"""What this slip's Salary Structure says about the statutory components.

	``allowed`` - components listed as deduction rows. Only these get computed;
	a structure that omits them (e.g. a task-worker structure) yields no
	statutory deductions for that employee, even though the company as a whole
	has statutory calculation enabled.

	``by_formula`` - components the structure works out for itself. A company
	whose Housing Levy sits on a different figure from its NSSF can write that
	as an ordinary Salary Structure formula, and this calculator then leaves the
	row alone rather than overwriting it. The formula is the whole answer: none
	of the Kenya Payroll Settings rates, tier caps or minimums are applied on
	top of it.

	Read from the Salary Structure rather than the slip, because core drops
	zero-amount placeholder rows before this hook runs when the component has
	remove_if_zero_valued set."""
	empty = frappe._dict({"allowed": set(), "by_formula": set()})
	if not salary_slip.salary_structure:
		return empty

	rows = frappe.get_all(
		"Salary Detail",
		filters={
			"parent": salary_slip.salary_structure,
			"parenttype": "Salary Structure",
			"parentfield": "deductions",
		},
		fields=["salary_component", "amount_based_on_formula", "formula"],
	)
	return frappe._dict({
		"allowed": {r.salary_component for r in rows},
		"by_formula": {
			r.salary_component for r in rows
			if r.amount_based_on_formula and (r.formula or "").strip()
		},
	})


def _set_amount(salary_slip, component_name, amount, allowed=None, by_formula=None):
	"""Write a statutory amount onto the slip, and return what it ended up being.

	The return matters: whatever is actually deducted is what may reduce taxable
	income, and that is not always what this calculator worked out. A component
	the structure computes for itself keeps its own figure, and the caller has
	to relieve against that rather than against ours.
	"""
	# No component configured for this figure on this company - nothing to write.
	if not component_name:
		return 0.0

	amount = flt(amount)
	existing = next(
		(row for row in (salary_slip.deductions or []) if row.salary_component == component_name),
		None,
	)

	# Not listed on the employee's Salary Structure -> this employee is not
	# subject to this statutory deduction at all.
	if allowed is not None and component_name not in allowed:
		if existing:
			salary_slip.deductions.remove(existing)
		return 0.0

	# The structure works this one out itself. Core has already evaluated the
	# formula by the time this hook runs, so leave the row exactly as it is.
	if by_formula and component_name in by_formula:
		return flt(existing.amount) if existing else 0.0

	if amount <= 0:
		if existing:
			salary_slip.deductions.remove(existing)
		return 0.0

	component_doc = frappe.db.get_value(
		"Salary Component", component_name,
		["salary_component_abbr", "custom_is_employer_contribution"], as_dict=True,
	)
	if not component_doc:
		frappe.log_error(
			title="Kenya Statutory Calculator: Missing Salary Component",
			message=f"Salary Component '{component_name}' not found. "
			f"Create it before enabling statutory deduction calculation.",
		)
		return 0.0

	# do_not_include_in_total alone keeps employer contributions out of net pay
	# (salary_slip.py's get_component_totals skips on that flag only). The
	# statistical flag is deliberately NOT set: on the Salary Component master
	# it hides the Accounts child table, leaving the component's GL accounts
	# unconfigurable, and a statistical component never becomes a slip row at
	# all - so the payroll Journal Entry would have nothing to post from.
	if existing:
		existing.amount = amount
		existing.do_not_include_in_total = 1 if component_doc.custom_is_employer_contribution else 0
		return amount

	row = {
		"salary_component": component_name,
		"abbr": component_doc.salary_component_abbr,
		"amount": amount,
	}
	if component_doc.custom_is_employer_contribution:
		row["do_not_include_in_total"] = 1
	salary_slip.append("deductions", row)
	return amount
