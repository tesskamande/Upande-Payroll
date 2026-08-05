# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

from frappe import _

from upande_payroll.statutory_reports import build

SPEC = {
	"title": _("HELB Report"),
	"component_filter": True,
	"columns": [
		("employee_number", _("Payroll No"), "Data", 120),
		("full_name", _("Employee"), "Data", 250),
		("national_id", _("ID Number"), "Data", 150),
		("kra_pin", _("KRA PIN"), "Data", 150),
		("amount", _("HELB Deduction"), "Currency", 150),
	],
}


def execute(filters=None):
	return build(filters, SPEC)
