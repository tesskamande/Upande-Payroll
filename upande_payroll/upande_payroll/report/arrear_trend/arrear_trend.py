# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

"""Arrears paid, by month and by why - the thing Arrear's own Reason field
exists to answer. A rising total most months is routine catch-up; a rising
total against the same reason every month is a process that keeps missing
something.

Submitted Arrear only - a draft is not yet money paid, and this is a trend of
what was.
"""

import frappe
from frappe import _
from frappe.utils import flt, formatdate, getdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company"):
		frappe.throw(_("Please select a Company."))

	arrears = get_arrears(filters)
	rows = group_rows(arrears)
	return get_columns(), rows, None, None, get_summary(rows)


def get_arrears(filters):
	conditions = {"docstatus": 1, "company": filters.company}
	if filters.get("from_date") and filters.get("to_date"):
		conditions["payroll_date"] = ("between", [filters.from_date, filters.to_date])
	elif filters.get("from_date"):
		conditions["payroll_date"] = (">=", filters.from_date)
	elif filters.get("to_date"):
		conditions["payroll_date"] = ("<=", filters.to_date)
	if filters.get("salary_component"):
		conditions["salary_component"] = filters.salary_component

	return frappe.get_all(
		"Arrear",
		filters=conditions,
		fields=["employee", "salary_component", "amount", "reason", "payroll_date"],
	)


def group_rows(arrears):
	buckets = {}
	for row in arrears:
		period_start = getdate(row.payroll_date).replace(day=1)
		reason = (row.reason or "").strip() or _("No Reason Given")
		key = (period_start, reason)

		bucket = buckets.setdefault(key, {
			"period": formatdate(period_start, "MMM yyyy"),
			"reason": reason,
			"amount": 0.0,
			"employees": set(),
			"_sort": key,
		})
		bucket["amount"] += flt(row.amount)
		bucket["employees"].add(row.employee)

	rows = sorted(buckets.values(), key=lambda b: b["_sort"])
	for row in rows:
		row["amount"] = round(row["amount"], 2)
		row["employees"] = len(row.pop("employees"))
		row.pop("_sort")
	return rows


def get_summary(rows):
	return [
		{"label": _("Total Arrears Paid"), "value": sum(flt(r["amount"]) for r in rows), "datatype": "Currency"},
	]


def get_columns():
	return [
		{"label": _("Period"), "fieldname": "period", "fieldtype": "Data", "width": 110},
		{"label": _("Reason"), "fieldname": "reason", "fieldtype": "Data", "width": 220},
		{"label": _("Employees"), "fieldname": "employees", "fieldtype": "Int", "width": 100},
		{"label": _("Amount"), "fieldname": "amount", "fieldtype": "Currency", "width": 120},
	]
