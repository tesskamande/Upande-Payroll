# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

"""Payroll cost over time, by the dimension the question is actually about -
month to see whether the bill is rising, Job Category to see which band is
driving it. Both at once by default, since "which category's cost rose this
month" is the usual follow-up to "did the cost rise."

Job Category is read off the Employee as of now, not as of the payslip's
period - a promotion moves the employee's whole history to their new category
on this report, which is what a reviewer comparing categories wants; dating it
to the slip would need Salary Structure Assignment history this report has no
reason to carry.
"""

import frappe
from frappe import _
from frappe.utils import flt, formatdate, getdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company"):
		frappe.throw(_("Please select a Company."))

	slips = get_slips(filters)
	if not slips:
		return get_columns(), []

	categories = get_job_categories([s.employee for s in slips])
	if filters.get("job_category"):
		slips = [s for s in slips if categories.get(s.employee) == filters.job_category]
	rows = group_rows(slips, categories)
	return get_columns(), rows, None, None, get_summary(rows)


def get_slips(filters):
	conditions = {"docstatus": 1, "company": filters.company}
	if filters.get("from_date"):
		conditions["start_date"] = (">=", filters.from_date)
	if filters.get("to_date"):
		conditions["end_date"] = ("<=", filters.to_date)

	return frappe.get_all(
		"Salary Slip",
		filters=conditions,
		fields=["name", "employee", "end_date", "gross_pay", "total_deduction", "net_pay"],
	)


def get_job_categories(employees):
	rows = frappe.get_all(
		"Employee", filters={"name": ("in", list(set(employees)))}, fields=["name", "job_category"]
	)
	return {r.name: r.job_category or _("No Category") for r in rows}


def group_rows(slips, categories):
	buckets = {}
	for slip in slips:
		category = categories.get(slip.employee, _("No Category"))
		period_start = getdate(slip.end_date).replace(day=1)
		key = (period_start, category)

		bucket = buckets.setdefault(key, {
			"period": formatdate(period_start, "MMM yyyy"),
			"job_category": category,
			"gross_pay": 0.0,
			"total_deduction": 0.0,
			"net_pay": 0.0,
			"employees": set(),
			"_sort": key,
		})
		bucket["gross_pay"] += flt(slip.gross_pay)
		bucket["total_deduction"] += flt(slip.total_deduction)
		bucket["net_pay"] += flt(slip.net_pay)
		bucket["employees"].add(slip.employee)

	rows = sorted(buckets.values(), key=lambda b: b["_sort"])
	for row in rows:
		row["gross_pay"] = round(row["gross_pay"], 2)
		row["total_deduction"] = round(row["total_deduction"], 2)
		row["net_pay"] = round(row["net_pay"], 2)
		row["employees"] = len(row.pop("employees"))
		row.pop("_sort")
	return rows


def get_summary(rows):
	return [
		{"label": _("Total Gross Pay"), "value": sum(flt(r["gross_pay"]) for r in rows), "datatype": "Currency"},
		{"label": _("Total Net Pay"), "value": sum(flt(r["net_pay"]) for r in rows), "datatype": "Currency"},
	]


def get_columns():
	return [
		{"label": _("Period"), "fieldname": "period", "fieldtype": "Data", "width": 110},
		{"label": _("Job Category"), "fieldname": "job_category", "fieldtype": "Data", "width": 150},
		{"label": _("Employees"), "fieldname": "employees", "fieldtype": "Int", "width": 100},
		{"label": _("Gross Pay"), "fieldname": "gross_pay", "fieldtype": "Currency", "width": 120},
		{"label": _("Total Deduction"), "fieldname": "total_deduction", "fieldtype": "Currency", "width": 130},
		{"label": _("Net Pay"), "fieldname": "net_pay", "fieldtype": "Currency", "width": 120},
	]
