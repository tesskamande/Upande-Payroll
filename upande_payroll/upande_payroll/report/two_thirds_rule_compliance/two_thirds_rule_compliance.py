# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

"""An audit view of the one-third rule (Employment Act s.19(3)), not a second
calculation of it.

The rule is already enforced, on every Salary Slip's validate, by
upande_payroll.deduction_cap - it holds total deductions to two thirds of
wages, deferring whatever is reducible and recording what it could not fix.
This report reads what that enforcement already decided and stored on the
slip, rather than recomputing the cap with a different idea of what counts
towards it (an earlier version of this report did exactly that, and got the
definition wrong - see custom_exempt_from_two_thirds_rule, since removed).

Two different things show up here:

  Deferred       the enforcement cut a reducible deduction to fit the cap.
                 Ordinary and expected - the deferred amount becomes a
                 Deferred Deduction and is recovered once a later period has
                 room.
  Unreducible    even after cutting everything that could be cut, the
                 remaining (protected) deductions alone still breach the cap.
                 Nothing further could be done about it mechanically - this is
                 the figure worth a human looking at.
"""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company"):
		frappe.throw(_("Please select a Company."))

	message = None
	if not frappe.db.get_value("Company Payroll Settings", filters.company, "enable_one_third_rule"):
		message = _(
			"The 1/3 Rule is not enabled on {0}'s Company Payroll Settings. Every "
			"slip below shows as uncapped, because the enforcement in "
			"deduction_cap.py does not run for this company."
		).format(frappe.bold(filters.company))
		frappe.msgprint(message, alert=True, indicator="orange")

	rows = get_rows(filters)

	only_breaches = frappe.utils.cint(filters.get("only_breaches"))
	if only_breaches:
		rows = [r for r in rows if r["unreducible_excess"] > 0 or r["has_pending_deductions"]]

	return get_columns(), rows, message, None, get_summary(filters, rows)


def get_rows(filters):
	conditions = {"docstatus": 1, "company": filters.company}
	if filters.get("from_date"):
		conditions["start_date"] = (">=", filters.from_date)
	if filters.get("to_date"):
		conditions["end_date"] = ("<=", filters.to_date)
	if filters.get("employee"):
		conditions["employee"] = filters.employee

	slips = frappe.get_all(
		"Salary Slip",
		filters=conditions,
		fields=[
			"name", "employee", "employee_name", "end_date", "gross_pay", "total_deduction",
			"custom_deduction_cap_applied", "custom_unreducible_excess",
			"custom_total_deferred_deductions", "custom_has_pending_deductions",
			"custom_one_third_rule_skipped",
		],
		order_by="end_date, employee_name",
	)

	rows = []
	for slip in slips:
		rows.append({
			"employee": slip.employee,
			"employee_name": slip.employee_name,
			"salary_slip": slip.name,
			"end_date": slip.end_date,
			"gross_pay": flt(slip.gross_pay),
			"total_deduction": flt(slip.total_deduction),
			"cap_applied": slip.custom_deduction_cap_applied,
			"deferred_this_period": flt(slip.custom_total_deferred_deductions),
			"unreducible_excess": flt(slip.custom_unreducible_excess),
			"has_pending_deductions": slip.custom_has_pending_deductions,
			"rule_skipped": slip.custom_one_third_rule_skipped,
		})
	return rows


def get_summary(filters, rows):
	breaches = [r for r in rows if r["unreducible_excess"] > 0]
	# balance_remaining > 0, not status = 'Pending' - a Partially Recovered
	# debt still owes a balance, the same as deferred_deduction.get_outstanding()
	# already checks; filtering on status alone understated what is still owed.
	outstanding = flt(frappe.db.sql(
		"""SELECT SUM(balance_remaining) FROM `tabDeferred Deduction`
		WHERE company=%(company)s AND docstatus=1 AND balance_remaining > 0""",
		{"company": filters.company},
	)[0][0] or 0)

	return [
		{"label": _("Payslips Checked"), "value": len(rows), "datatype": "Int"},
		{
			"label": _("Unreducible Breaches"), "value": len(breaches), "datatype": "Int",
			"indicator": "Red" if breaches else "Green",
		},
		{
			"label": _("Outstanding Deferred Balance (Now)"), "value": outstanding,
			"datatype": "Currency", "indicator": "Orange" if outstanding else "Green",
		},
	]


def get_columns():
	return [
		{"label": _("Employee"), "fieldname": "employee", "fieldtype": "Link",
		 "options": "Employee", "width": 130},
		{"label": _("Employee Name"), "fieldname": "employee_name", "fieldtype": "Data", "width": 160},
		{"label": _("Salary Slip"), "fieldname": "salary_slip", "fieldtype": "Link",
		 "options": "Salary Slip", "width": 140},
		{"label": _("Period End"), "fieldname": "end_date", "fieldtype": "Date", "width": 100},
		{"label": _("Gross Pay"), "fieldname": "gross_pay", "fieldtype": "Currency", "width": 110},
		{"label": _("Total Deduction"), "fieldname": "total_deduction", "fieldtype": "Currency", "width": 120},
		{"label": _("Cap Applied"), "fieldname": "cap_applied", "fieldtype": "Check", "width": 90},
		{"label": _("Deferred This Period"), "fieldname": "deferred_this_period",
		 "fieldtype": "Currency", "width": 140},
		{"label": _("Unreducible Excess"), "fieldname": "unreducible_excess",
		 "fieldtype": "Currency", "width": 140},
		{"label": _("Still Pending"), "fieldname": "has_pending_deductions", "fieldtype": "Check", "width": 90},
		{"label": _("Final Slip (Rule Skipped)"), "fieldname": "rule_skipped", "fieldtype": "Check", "width": 140},
	]
