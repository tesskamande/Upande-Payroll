# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt

DOCSTATUS = {"Draft": 0, "Submitted": 1, "Cancelled": 2}

# The P10 columns that come straight from a tagged component. Anything worked
# out from other figures is handled in _derive below instead.
TAGGED = [
	("Basic Salary", "basic_salary"),
	("Housing Allowance", "housing_allowance"),
	("Transport Allowance", "transport_allowance"),
	("Leave Pay", "leave_pay"),
	("Overtime", "overtime"),
	("Directors Fee", "directors_fee"),
	("Lump Sum Payment", "lump_sum_payment"),
	("Other Allowance", "other_allowance"),
	("Value of Car Benefit", "value_of_car_benefit"),
	("Other Non Cash Benefits", "other_non_cash_benefits"),
	("Pension Contribution", "pension_contribution"),
	("NSSF Contribution", "nssf_contribution"),
	("Mortgage Interest", "mortgage_interest"),
	("Affordable Housing Levy", "affordable_housing_levy"),
	("SHIF", "shif"),
	("Amount of Benefit", "amount_of_benefit"),
	("Taxable Pay", "taxable_pay"),
	("Amount of Insurance", "amount_of_insurance"),
	("PAYE Tax", "paye_tax"),
	("Self Assessed PAYE Tax", "self_assessed_paye_tax"),
]


def execute(filters=None):
	filters = frappe._dict(filters or {})

	if filters.from_date and filters.to_date and filters.from_date > filters.to_date:
		frappe.throw(_("From Date cannot be after To Date"))

	slips = _get_slips(filters)
	if not slips:
		return get_columns(), []

	employees = _accumulate(slips, filters)
	_derive(employees)
	return get_columns(), list(employees.values())


# ----------------------------------------------------------------------

def _get_slips(filters):
	conditions = ["ss.docstatus = %(docstatus_value)s"]
	params = dict(filters)
	params["docstatus_value"] = DOCSTATUS.get(filters.docstatus, 1)

	for field, clause in (
		("company", "ss.company = %(company)s"),
		("from_date", "ss.start_date >= %(from_date)s"),
		("to_date", "ss.end_date <= %(to_date)s"),
		("employee", "ss.employee = %(employee)s"),
		("grade", "e.grade = %(grade)s"),
	):
		if filters.get(field):
			conditions.append(clause)

	# The KRA PIN is read from whatever the site calls it rather than being
	# created by this app, so a site holding it under another name still fills
	# the column and one holding it nowhere simply leaves it blank.
	pin_field = _pin_field()
	pin_select = "e.{0} AS tax_id".format(pin_field) if pin_field else "NULL AS tax_id"

	return frappe.db.sql(
		"""
		SELECT ss.name, ss.employee, ss.employee_name, ss.gross_pay,
			ss.custom_tax_charged, ss.custom_personal_relief_utilized, {pin}
		FROM `tabSalary Slip` ss
		INNER JOIN `tabEmployee` e ON e.name = ss.employee
		WHERE {conditions}
		ORDER BY ss.employee ASC
		""".format(pin=pin_select, conditions=" AND ".join(conditions)),
		params,
		as_dict=True,
	)


def _pin_field():
	meta = frappe.get_meta("Employee")
	for fieldname in ("tax_id", "custom_kra_pin", "kra_pin", "custom_tax_id"):
		if meta.has_field(fieldname):
			return fieldname
	return None


def _accumulate(slips, filters):
	"""One line per employee, adding up every payslip in the range."""
	tagged = frappe.db.sql(
		"""
		SELECT sd.parent, sc.p10a_tax_deduction_card_type AS tag,
			IFNULL(sd.amount, 0) AS amount
		FROM `tabSalary Detail` sd
		INNER JOIN `tabSalary Component` sc ON sc.name = sd.salary_component
		WHERE sd.parent IN %(slips)s AND sd.parenttype = 'Salary Slip'
			AND IFNULL(sc.p10a_tax_deduction_card_type, '') != ''
		""",
		{"slips": [s.name for s in slips]},
		as_dict=True,
	)

	by_slip = {}
	for row in tagged:
		by_slip.setdefault(row.parent, {})
		by_slip[row.parent][row.tag] = by_slip[row.parent].get(row.tag, 0.0) + flt(row.amount)

	settings = _company_settings(filters.company)
	absence = settings.get("absence_components", set())

	employees = {}
	for slip in slips:
		line = employees.get(slip.employee)
		if line is None:
			line = {field: 0.0 for _tag, field in TAGGED}
			line.update({
				"tax_id": slip.tax_id,
				"employee_name": slip.employee_name,
				"residence_status": "Resident",
				"total_gross_pay": 0.0,
				"tax_payable": 0.0,
				"monthly_personal_relief": 0.0,
				"_absence": 0.0,
			})
			employees[slip.employee] = line

		line["total_gross_pay"] += flt(slip.gross_pay)
		line["tax_payable"] += flt(slip.custom_tax_charged)

		# What the payslip actually relieved, rather than the standard figure -
		# a mid-year joiner or a low-tax month gets less than the full amount.
		utilized = slip.custom_personal_relief_utilized
		line["monthly_personal_relief"] += (
			flt(utilized) if utilized is not None else settings["monthly_relief"]
		)

		amounts = by_slip.get(slip.name, {})
		for tag, field in TAGGED:
			line[field] += amounts.get(tag, 0.0)

		# Absence is time not worked, so it comes off pay rather than sitting in
		# the deductions. Which components count is the company's own mapping.
		line["_absence"] += _absence_on(slip.name, absence)

	for line in employees.values():
		line["_retirement_cap"] = settings["retirement_cap"]

	return employees


def _absence_on(slip, absence_components):
	if not absence_components:
		return 0.0
	total = frappe.db.sql(
		"""
		SELECT IFNULL(SUM(amount), 0) FROM `tabSalary Detail`
		WHERE parent = %(slip)s AND parenttype = 'Salary Slip'
			AND salary_component IN %(components)s
		""",
		{"slip": slip, "components": list(absence_components)},
	)
	return flt(total[0][0]) if total else 0.0


def _company_settings(company):
	monthly_relief = flt(frappe.db.get_single_value(
		"Kenya Payroll Settings", "monthly_personal_relief"))
	retirement_cap = flt(frappe.db.get_single_value(
		"Kenya Payroll Settings", "retirement_relief_cap"))

	absence = set()
	if company and frappe.db.exists("Company Payroll Settings", company):
		settings = frappe.get_cached_doc("Company Payroll Settings", company)
		absence = {
			row.salary_component
			for row in (settings.statutory_income_component_mapping or [])
			if row.category == "Absence / Unpaid Deduction"
		}

	return {
		"monthly_relief": monthly_relief,
		"retirement_cap": retirement_cap,
		"absence_components": absence,
	}


def _derive(employees):
	"""The columns the P10 works out rather than reads off a component."""
	for line in employees.values():
		absence = line.pop("_absence", 0.0)
		cap = line.pop("_retirement_cap", 0.0)

		line["total_cash_pay"] = line["total_gross_pay"] - absence
		line["total_gross_pay"] = line["total_cash_pay"]

		line["total_non_cash_pay"] = (
			line["value_of_car_benefit"] + line["other_non_cash_benefits"]
		)
		line["benefit_status"] = (
			"Benefit Given" if line["total_non_cash_pay"] > 0 else "Benefit Not Given"
		)

		line["30_percent_of_cash_pay"] = line["total_cash_pay"] * 0.30
		line["actual_contribution"] = (
			line["nssf_contribution"] + line["pension_contribution"]
		)

		# Retirement relief is the lowest of what was paid, 30% of cash pay, and
		# the monthly cap from Kenya Payroll Settings - so a rate change is one
		# edit there rather than a number buried in this report.
		limits = [line["actual_contribution"], line["30_percent_of_cash_pay"]]
		if cap:
			limits.append(cap)
		line["permissible_limit"] = min(limits)


def get_columns():
	def money(fieldname, label, width=150):
		return {"fieldname": fieldname, "label": _(label),
				"fieldtype": "Currency", "width": width}

	return [
		{"fieldname": "tax_id", "label": _("PIN of Employee"),
		 "fieldtype": "Data", "width": 150},
		{"fieldname": "employee_name", "label": _("Employee Name"),
		 "fieldtype": "Data", "width": 200},
		{"fieldname": "residence_status", "label": _("Residence Status"),
		 "fieldtype": "Data", "width": 130},
		money("basic_salary", "Basic Salary"),
		money("housing_allowance", "Housing Allowance"),
		money("transport_allowance", "Transport Allowance"),
		money("leave_pay", "Leave Pay"),
		money("overtime", "Overtime"),
		money("directors_fee", "Director's Fee"),
		money("lump_sum_payment", "Lump Sum Payment"),
		money("other_allowance", "Other Allowance"),
		money("total_cash_pay", "Total Cash Pay"),
		{"fieldname": "benefit_status", "label": _("Benefit Status"),
		 "fieldtype": "Data", "width": 150},
		money("value_of_car_benefit", "Value of Car Benefit", 170),
		money("other_non_cash_benefits", "Other Non Cash Benefits", 180),
		money("total_non_cash_pay", "Total Non Cash Pay", 160),
		money("total_gross_pay", "Total Gross Pay"),
		money("30_percent_of_cash_pay", "30 Percent of Cash Pay", 170),
		money("pension_contribution", "Pension Contribution", 170),
		money("nssf_contribution", "NSSF Contribution", 160),
		money("actual_contribution", "Actual Contribution", 160),
		money("permissible_limit", "Permissible Limit", 150),
		money("mortgage_interest", "Mortgage Interest", 150),
		money("affordable_housing_levy", "Affordable Housing Levy", 180),
		money("shif", "SHIF", 120),
		money("amount_of_benefit", "Amount of Benefit", 150),
		money("taxable_pay", "Taxable Pay"),
		money("tax_payable", "Tax Payable"),
		money("monthly_personal_relief", "Monthly Personal Relief", 180),
		money("amount_of_insurance", "Amount of Insurance", 170),
		money("paye_tax", "PAYE Tax", 130),
		money("self_assessed_paye_tax", "Self Assessed PAYE Tax", 180),
	]
