# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

from frappe import _

from upande_payroll.statutory_reports import build

SPEC = {
	"title": _("HELB Report"),
	"component_filter": True,
	# HELB is a deduction on most sites and a Loan Product on the ones that
	# track the balance owed. Both are summed, so a changeover reports in full
	# rather than losing whichever half has already moved.
	"loan_filter": True,
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
