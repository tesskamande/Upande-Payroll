# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

from frappe import _

from upande_payroll.statutory_reports import build

SPEC = {
	"title": _("Affordable Housing Levy"),
	"employee_keys": ("housing_levy_employee",),
	"employer_keys": ("housing_levy_employer",),
	"columns": [
		("employee_number", _("Payroll No"), "Data", 120),
		("national_id", _("ID Number"), "Data", 150),
		("employee_name", _("Employee Name"), "Data", 250),
		("kra_pin", _("KRA PIN"), "Data", 150),
		("gross_salary", _("Gross Salary"), "Currency", 180),
		("basic_salary", _("Basic Salary"), "Currency", 150),
		("member_contribution", _("Member Contribution"), "Currency", 170),
		("employer_contribution", _("Employer Contribution"), "Currency", 170),
		("total_contribution", _("Total Contribution"), "Currency", 160),
	],
}


def execute(filters=None):
	return build(filters, SPEC)
