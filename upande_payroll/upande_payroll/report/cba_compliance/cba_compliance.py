# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

"""Every employee against their Job Category's Entry Minimum, right now.

Independent of the Application Log - that report is history, what a run of
apply_cba_to_employees actually did. This is a live snapshot: whoever is below
their category's floor today, whether or not any CBA has ever touched them.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company"):
		frappe.throw(_("Please select a Company."))

	cba_name = get_current_cba(filters.company)
	if not cba_name:
		frappe.msgprint(
			_("No CBA is currently in force for {0}.").format(filters.company),
			alert=True, indicator="orange",
		)
		return get_columns(), [], None, None, []

	minimums = get_minimums(cba_name)
	rows = get_rows(filters, minimums)
	return get_columns(), rows, None, None, get_summary(rows)


def get_current_cba(company):
	"""The same agreement get_cba_minimum() would use - submitted, and its
	Effective Start Date has arrived."""
	return frappe.db.get_value(
		"CBA",
		{"docstatus": 1, "company": company, "effective_start_date": ("<=", getdate())},
		"name",
		order_by="effective_start_date desc, creation desc",
	)


def get_minimums(cba_name):
	"""{job_category: entry minimum}, falling back to Current Basic Pay for a
	pay table row saved before Entry Minimum existed - same fallback
	get_cba_minimum() itself uses."""
	rows = frappe.get_all(
		"CBA Pay Table",
		filters={"parent": cba_name, "parenttype": "CBA"},
		fields=["job_category", "entry_minimum", "current_basic_pay"],
	)
	return {
		row.job_category: flt(row.entry_minimum) or flt(row.current_basic_pay)
		for row in rows
		if row.job_category
	}


def get_rows(filters, minimums):
	conditions = {"company": filters.company, "job_category": ["is", "set"]}
	if filters.get("job_category"):
		conditions["job_category"] = filters.job_category
	if filters.get("status"):
		conditions["status"] = filters.status

	employees = frappe.get_all(
		"Employee",
		filters=conditions,
		fields=["name", "employee_name", "company", "job_category", "status", "basic_pay"],
		order_by="job_category, employee_name",
	)

	only_below = frappe.utils.cint(filters.get("only_below_minimum"))
	rows = []
	for emp in employees:
		minimum = minimums.get(emp.job_category)

		if minimum is None:
			# A real category on the employee, but this agreement names no
			# rate for it - worth surfacing on its own, not silently skipped,
			# since it usually means the pay table is missing a row.
			if only_below:
				continue
			rows.append({**emp, "entry_minimum": None, "shortfall": 0.0, "compliance": _("No CBA Rate")})
			continue

		shortfall = round(max(0.0, flt(minimum) - flt(emp.basic_pay)), 2)
		if shortfall > 0:
			compliance = _("Below Minimum")
		elif flt(emp.basic_pay) == flt(minimum):
			compliance = _("At Minimum")
		else:
			compliance = _("Above Minimum")

		if only_below and shortfall <= 0:
			continue

		rows.append({**emp, "entry_minimum": minimum, "shortfall": shortfall, "compliance": compliance})

	return rows


def get_summary(rows):
	below = [r for r in rows if r["compliance"] == _("Below Minimum")]
	return [
		{"label": _("Employees Checked"), "value": len(rows), "datatype": "Int"},
		{
			"label": _("Below Minimum"), "value": len(below), "datatype": "Int",
			"indicator": "Red" if below else "Green",
		},
		{
			"label": _("Total Shortfall"),
			"value": sum(flt(r["shortfall"]) for r in below),
			"datatype": "Currency",
			"indicator": "Red",
		},
	]


def get_columns():
	return [
		{"label": _("Employee"), "fieldname": "name", "fieldtype": "Link",
		 "options": "Employee", "width": 130},
		{"label": _("Employee Name"), "fieldname": "employee_name",
		 "fieldtype": "Data", "width": 180},
		{"label": _("Job Category"), "fieldname": "job_category",
		 "fieldtype": "Data", "width": 140},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 90},
		{"label": _("Basic Pay"), "fieldname": "basic_pay",
		 "fieldtype": "Currency", "width": 120},
		{"label": _("Entry Minimum"), "fieldname": "entry_minimum",
		 "fieldtype": "Currency", "width": 130},
		{"label": _("Shortfall"), "fieldname": "shortfall",
		 "fieldtype": "Currency", "width": 110},
		{"label": _("Compliance"), "fieldname": "compliance",
		 "fieldtype": "Data", "width": 130},
	]
