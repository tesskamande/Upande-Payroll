# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

"""Every salary review raised, and what it changed.

The audit trail Salary Review exists for: one row per review, the basic pay
it moved from and to.
"""

import frappe
from frappe import _
from frappe.utils import flt

DOCSTATUS = {"Draft": 0, "Submitted": 1, "Cancelled": 2}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	rows = get_rows(filters)
	return get_columns(), rows, None, None, get_summary(rows)


def get_conditions(filters):
	conditions = ["sr.docstatus = %(docstatus_value)s"]
	filters["docstatus_value"] = DOCSTATUS.get(filters.get("docstatus"), 1)

	if filters.get("employee"):
		conditions.append("sr.employee = %(employee)s")
	if filters.get("company"):
		conditions.append("sr.company = %(company)s")
	if filters.get("review_type"):
		conditions.append("sr.review_type = %(review_type)s")
	if filters.get("from_date"):
		conditions.append("sr.effective_date >= %(from_date)s")
	if filters.get("to_date"):
		conditions.append("sr.effective_date <= %(to_date)s")
	return "where " + " and ".join(conditions)


def get_rows(filters):
	return frappe.db.sql(
		"""
		select
			sr.name,
			sr.employee,
			sr.employee_name,
			sr.company,
			sr.review_type,
			sr.effective_date,
			sr.current_basic_pay,
			sr.increment_percent,
			sr.increment_amount,
			sr.proposed_basic_pay,
			sr.auto_update_basic_pay,
			sr.reason
		from `tabSalary Review` sr
		{conditions}
		order by sr.effective_date desc, sr.employee_name
		""".format(conditions=get_conditions(filters)),
		filters,
		as_dict=True,
	)


def get_summary(rows):
	total_change = sum(flt(row.increment_amount) for row in rows)
	headcount = len({row.employee for row in rows})
	return [
		{"label": _("Employees Reviewed"), "value": headcount, "datatype": "Int"},
		{
			"label": _("Total Monthly Change"),
			"value": total_change,
			"datatype": "Currency",
			"indicator": "Blue" if total_change >= 0 else "Red",
		},
		{
			"label": _("Annualised"),
			"value": total_change * 12,
			"datatype": "Currency",
			"indicator": "Orange",
		},
	]


def get_columns():
	return [
		{"label": _("Salary Review"), "fieldname": "name", "fieldtype": "Link",
		 "options": "Salary Review", "width": 150},
		{"label": _("Employee"), "fieldname": "employee", "fieldtype": "Link",
		 "options": "Employee", "width": 130},
		{"label": _("Employee Name"), "fieldname": "employee_name",
		 "fieldtype": "Data", "width": 160},
		{"label": _("Review Type"), "fieldname": "review_type",
		 "fieldtype": "Data", "width": 130},
		{"label": _("Effective Date"), "fieldname": "effective_date",
		 "fieldtype": "Date", "width": 110},
		{"label": _("Previous Basic Pay"), "fieldname": "current_basic_pay",
		 "fieldtype": "Currency", "width": 140},
		{"label": _("Increment %"), "fieldname": "increment_percent",
		 "fieldtype": "Percent", "width": 100},
		{"label": _("Increment Amount"), "fieldname": "increment_amount",
		 "fieldtype": "Currency", "width": 140},
		{"label": _("New Basic Pay"), "fieldname": "proposed_basic_pay",
		 "fieldtype": "Currency", "width": 140},
		{"label": _("Reason"), "fieldname": "reason", "fieldtype": "Data", "width": 200},
	]
