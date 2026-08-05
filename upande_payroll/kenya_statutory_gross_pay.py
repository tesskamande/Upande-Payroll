import frappe
from frappe.utils import flt


def get_income_breakdown(salary_slip, settings, is_secondary=False):
	"""Return the two figures Kenyan statutory deductions actually need -
	which are NOT the same as ERPNext's gross_pay:

	  statutory_cash_base - NSSF/SHIF/Housing Levy base.

	  taxable_income - PAYE base, BEFORE the NSSF/SHIF/Housing Levy employee
	      deductions AND before the Pension Contribution relief (those are
	      combined with NSSF and capped together at Kenya Payroll Settings'
	      Retirement Relief Cap downstream, so they can't be known yet here)
	      - see kenya_statutory_calculator.py.

	  pension_contribution - returned separately (not yet subtracted from
	      taxable_income) so the caller can cap it together with NSSF against
	      the combined KES 30,000/month retirement contributions relief limit,
	      rather than each being deducted in full independently.

	The starting cash figure is summed from ordinary cash earnings, classified
	via Statutory Income Component Mapping (unmapped components default to
	"Ordinary Cash Earning", excluding Non-Cash Benefit/Partially Exempt
	Benefit). The same mapping table then applies on top: Absence/Unpaid
	Deduction reduces the cash base; Non-Cash Benefit, Pension Contribution,
	Mortgage Interest Relief and Post-Retirement Medical Fund adjust taxable
	income.
	"""
	mapping = {
		row.salary_component: row
		for row in (settings.statutory_income_component_mapping or [])
	}
	kenya_settings = frappe.get_cached_doc("Kenya Payroll Settings")

	cash_earnings = _sum_ordinary_cash_earnings(salary_slip, mapping)
	adj = _mapped_amounts(salary_slip, mapping, kenya_settings)

	# Secondary employment: reliefs are claimable from the main employer only
	# (Income Tax Act s.30). Disallowed at a secondary employer are Personal
	# Relief, Owner-Occupied Mortgage Interest Relief and Pension Relief.
	#
	# Note on pension: the contribution is still DEDUCTED from the employee in
	# full - that row comes from the company's own Salary Structure and nothing
	# here touches it. What a secondary employer may not give is the pension
	# RELIEF, so the amount is zeroed only where it would reduce taxable income.
	#
	# Post-Retirement Medical Fund is deliberately NOT suppressed. The Tax Laws
	# (Amendment) Act 2024 turned it - along with the Housing Levy and SHIF -
	# from a relief into an allowable deduction from taxable income, and
	# deductions of that kind survive into secondary employment just as the
	# Housing Levy does.
	mortgage = 0.0 if is_secondary else adj.mortgage_interest_relief
	prmf = adj.post_retirement_medical_fund
	pension = 0.0 if is_secondary else adj.pension_contribution

	statutory_cash_base = max(cash_earnings - adj.absence_deductions, 0.0)
	taxable_income = max(
		statutory_cash_base + adj.non_cash_benefit_taxable - mortgage - prmf,
		0.0,
	)

	return frappe._dict({
		"statutory_cash_base": flt(statutory_cash_base, 2),
		"taxable_income": flt(taxable_income, 2),
		"pension_contribution": flt(pension, 2),
		"insurance_premium": flt(adj.insurance_premium, 2),
	})


def _sum_ordinary_cash_earnings(salary_slip, mapping):
	total = 0.0
	for row in salary_slip.earnings or []:
		rule = mapping.get(row.salary_component)
		category = rule.category if rule else "Ordinary Cash Earning"
		if category not in ("Non-Cash Benefit", "Partially Exempt Benefit"):
			total += flt(row.amount)
	return total


def _mapped_amounts(salary_slip, mapping, kenya_settings):
	non_cash_benefit_taxable = 0.0
	absence_deductions = 0.0
	pension_contribution = 0.0
	mortgage_interest_relief = 0.0
	post_retirement_medical_fund = 0.0
	insurance_premium = 0.0

	for row in salary_slip.earnings or []:
		rule = mapping.get(row.salary_component)
		if not rule:
			continue
		amount = flt(row.amount)
		if rule.category == "Non-Cash Benefit":
			if rule.non_cash_benefit_is_exempt:
				continue
			non_cash_benefit_taxable += amount
		elif rule.category == "Partially Exempt Benefit":
			threshold = flt(rule.exemption_threshold)
			if rule.exemption_threshold_period == "Daily":
				threshold *= flt(salary_slip.payment_days) or 1
			non_cash_benefit_taxable += max(amount - threshold, 0.0)

	for row in salary_slip.deductions or []:
		rule = mapping.get(row.salary_component)
		if not rule:
			continue
		amount = flt(row.amount)
		if rule.category == "Absence / Unpaid Deduction":
			absence_deductions += amount
		elif rule.category == "Pension Contribution":
			pension_contribution += amount
		elif rule.category == "Mortgage Interest Relief":
			cap = flt(kenya_settings.mortgage_interest_relief_cap)
			mortgage_interest_relief += min(amount, cap) if cap else amount
		elif rule.category == "Post-Retirement Medical Fund":
			cap = flt(kenya_settings.post_retirement_medical_fund_cap)
			post_retirement_medical_fund += min(amount, cap) if cap else amount
		elif rule.category == "Insurance Premium":
			# Premiums on qualifying life, education and health policies
			# (Income Tax Act s.31). Several policies may be tagged and they
			# accumulate. Not SHIF: the Tax Laws (Amendment) Act 2024 made that
			# an allowable deduction from taxable income instead, which is how
			# it is already handled, so relieving it here would double-count.
			insurance_premium += amount

	return frappe._dict({
		"non_cash_benefit_taxable": non_cash_benefit_taxable,
		"absence_deductions": absence_deductions,
		"pension_contribution": pension_contribution,
		"mortgage_interest_relief": mortgage_interest_relief,
		"post_retirement_medical_fund": post_retirement_medical_fund,
		"insurance_premium": insurance_premium,
	})
