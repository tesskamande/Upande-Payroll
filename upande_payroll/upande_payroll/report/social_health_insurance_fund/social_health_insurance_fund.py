# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

from frappe import _

from upande_payroll.statutory_reports import build

SPEC = {
	"title": _("Social Health Insurance Fund"),
	"employee_keys": ("shif",),
	# Column order and naming follow the SHA portal's own upload template
	# (Payroll No, Firstname, Lastname, Identity Type, ID No, KRA PIN, SHA
	# No, Contribution Amount, Phone) so this can be filed straight from the
	# report; Gross/Basic Salary trail after as this app's own extra context,
	# not part of what SHA itself asks for.
	"columns": [
		("employee_number", _("Payroll No"), "Data", 120),
		("other_name", _("Firstname"), "Data", 180),
		("last_name", _("Lastname"), "Data", 150),
		("identity_type", _("Identity Type"), "Data", 120),
		("national_id", _("ID No"), "Data", 130),
		("kra_pin", _("KRA PIN"), "Data", 150),
		("sha_no", _("SHA No"), "Data", 150),
		("member_contribution", _("Contribution Amount"), "Currency", 170),
		("phone", _("Phone"), "Data", 130),
		("gross_salary", _("Gross Salary"), "Currency", 180),
		("basic_salary", _("Basic Salary"), "Currency", 150),
	],
}


def execute(filters=None):
	return build(filters, SPEC)
