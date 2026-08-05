# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

from frappe import _

from upande_payroll.statutory_reports import build

SPEC = {
	"title": _("National Social Security Fund"),
	"employee_keys": ("nssf_tier1_employee", "nssf_tier2_employee"),
	"columns": [
		("employee_number", _("Payroll No"), "Data", 130),
		("last_name", _("Surname"), "Data", 150),
		("other_name", _("Other Names"), "Data", 200),
		("national_id", _("National ID"), "Data", 140),
		("kra_pin", _("KRA No"), "Data", 140),
		("nssf_no", _("NSSF No"), "Data", 140),
		("gross_pay", _("Gross Pay"), "Currency", 170),
		("total_nssf", _("Total NSSF"), "Currency", 140),
		("voluntary", _("Voluntary"), "Check", 90),
	],
}


def execute(filters=None):
	return build(filters, SPEC)
