# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

import calendar

import frappe
from frappe import _
from frappe.utils import flt, getdate

DOCSTATUS = {"Draft": 0, "Submitted": 1, "Cancelled": 2}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = get_columns()

	if not (filters.employee and filters.fiscal_year and filters.company):
		return columns, []

	year = frappe.db.get_value(
		"Fiscal Year", filters.fiscal_year,
		["year_start_date", "year_end_date"], as_dict=True,
	)
	if not year:
		frappe.throw(_("Fiscal Year {0} does not exist").format(filters.fiscal_year))

	slips = frappe.db.sql(
		"""
		SELECT name, gross_pay, start_date, end_date,
			custom_tax_charged, custom_personal_relief_utilized
		FROM `tabSalary Slip`
		WHERE docstatus = %(docstatus_value)s
			AND employee = %(employee)s
			AND company = %(company)s
			AND end_date >= %(year_start)s
			AND end_date <= %(year_end)s
		ORDER BY start_date ASC
		""",
		{
			"docstatus_value": DOCSTATUS.get(filters.docstatus, 1),
			"employee": filters.employee,
			"company": filters.company,
			"year_start": year.year_start_date,
			"year_end": year.year_end_date,
		},
		as_dict=True,
	)
	if not slips:
		return columns, []

	relief = flt(frappe.db.get_single_value(
		"Kenya Payroll Settings", "monthly_personal_relief"
	))

	rows = [_month(slip, relief) for slip in slips]
	rows.append(_year_total(rows))
	return columns, rows


# ----------------------------------------------------------------------

def _month(slip, monthly_relief):
	"""One line of the card, using the P9A tag on each component.

	Nothing here knows a component by name. A company that calls its basic pay
	"Basic Wage" only has to tag it Basic Salary and the card fills in.
	"""
	tagged = frappe.db.sql(
		"""
		SELECT sc.p9a_tax_deduction_card_type AS tag, IFNULL(sd.amount, 0) AS amount
		FROM `tabSalary Detail` sd
		INNER JOIN `tabSalary Component` sc ON sc.name = sd.salary_component
		WHERE sd.parent = %(slip)s AND sd.parenttype = 'Salary Slip'
			AND IFNULL(sc.p9a_tax_deduction_card_type, '') != ''
		""",
		{"slip": slip.name},
		as_dict=True,
	)

	p9a = {}
	for row in tagged:
		p9a[row.tag] = p9a.get(row.tag, 0.0) + flt(row.amount)

	basic = p9a.get("Basic Salary", 0.0)
	gross = flt(slip.gross_pay)

	# E1 is 30% of pensionable pay, E2 the actual scheme contribution, E3 any
	# other. Only the lowest of the three is allowable, which is the cap KRA
	# applies rather than the sum.
	e1 = basic * 0.30
	e2 = p9a.get("E2 Defined Contribution Retirement Scheme", 0.0)
	e3 = p9a.get("E3 Defined Contribution Retirement Scheme", 0.0)
	claimed = [v for v in (e1, e2, e3) if v > 0]
	owner_interest = p9a.get("Owner Occupied Interest", 0.0)
	allowable = (min(claimed) if claimed else 0.0) + owner_interest

	chargeable = p9a.get("Chargeable Pay") or (gross - allowable)

	# Relief actually applied on the payslip beats the standard monthly figure:
	# a mid-year joiner or a month with too little tax to absorb it gets less.
	utilized = slip.custom_personal_relief_utilized
	personal_relief = (
		flt(utilized) if utilized is not None
		else p9a.get("Personal Relief", monthly_relief)
	)

	return {
		"month": calendar.month_name[getdate(slip.end_date).month],
		"basic_salary": basic,
		"benefits_non_cash": p9a.get("Benefits NonCash", 0.0),
		"value_of_quarters": p9a.get("Value of Quarters", 0.0),
		"total_gross_pay": gross,
		"e1_defined_contribution": e1,
		"e2_defined_contribution": e2,
		"e3_defined_contribution": e3,
		"owner_occupied_interest": owner_interest,
		"retirement_and_owner_interest": allowable,
		"chargeable_pay": chargeable,
		"tax_charged": flt(slip.custom_tax_charged),
		"personal_relief": personal_relief,
		"insurance_relief": p9a.get("Insurance Relief", 0.0),
		"paye_tax": p9a.get("PAYE Tax", 0.0),
		"housing_levy": p9a.get("Housing Levy", 0.0),
		"shif": p9a.get("SHIF", 0.0),
	}


def _year_total(rows):
	total = {"month": _("Total")}
	for column in get_columns()[1:]:
		field = column["fieldname"]
		total[field] = sum(flt(row.get(field)) for row in rows)
	return total


def get_columns():
	return [
		{"fieldname": "month", "label": _("Month"), "fieldtype": "Data", "width": 110},
		{"fieldname": "basic_salary", "label": _("Basic Salary | A"),
		 "fieldtype": "Currency", "width": 130},
		{"fieldname": "benefits_non_cash", "label": _("Benefits NonCash | B"),
		 "fieldtype": "Currency", "width": 150},
		{"fieldname": "value_of_quarters", "label": _("Value of Quarters | C"),
		 "fieldtype": "Currency", "width": 150},
		{"fieldname": "total_gross_pay", "label": _("Total Gross Pay | D"),
		 "fieldtype": "Currency", "width": 150},
		{"fieldname": "e1_defined_contribution", "label": _("E1 (30% of A)"),
		 "fieldtype": "Currency", "width": 120},
		{"fieldname": "e2_defined_contribution", "label": _("E2 (NSSF)"),
		 "fieldtype": "Currency", "width": 110},
		{"fieldname": "e3_defined_contribution", "label": _("E3 (Other)"),
		 "fieldtype": "Currency", "width": 110},
		{"fieldname": "owner_occupied_interest", "label": _("Owner Occupied Interest | F"),
		 "fieldtype": "Currency", "width": 170},
		{"fieldname": "retirement_and_owner_interest", "label": _("Retirement + Owner | G"),
		 "fieldtype": "Currency", "width": 160},
		{"fieldname": "chargeable_pay", "label": _("Chargeable Pay | H"),
		 "fieldtype": "Currency", "width": 150},
		{"fieldname": "tax_charged", "label": _("Tax Charged | I"),
		 "fieldtype": "Currency", "width": 130},
		{"fieldname": "personal_relief", "label": _("Personal Relief | K"),
		 "fieldtype": "Currency", "width": 140},
		{"fieldname": "insurance_relief", "label": _("Insurance Relief"),
		 "fieldtype": "Currency", "width": 130},
		{"fieldname": "paye_tax", "label": _("PAYE Tax | L"),
		 "fieldtype": "Currency", "width": 120},
		{"fieldname": "housing_levy", "label": _("Housing Levy | N"),
		 "fieldtype": "Currency", "width": 130},
		{"fieldname": "shif", "label": _("SHIF | J"), "fieldtype": "Currency", "width": 110},
	]
