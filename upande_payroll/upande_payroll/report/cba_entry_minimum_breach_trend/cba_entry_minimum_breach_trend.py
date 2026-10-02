# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

"""CBA Compliance, trended - how many employees were below their category's
Entry Minimum at the end of each month, not just today.

CBA Compliance is a live snapshot: today's basic pay against whichever CBA is
in force today. This reconstructs the same check for each month in the range,
from two histories instead of two live values:

  Pay       the latest Salary Structure Assignment's base as of that month end
            - the rate actually in effect then, not today's.
  Minimum   the latest CBA's Entry Minimum for the category as of that month
            end - the rate that agreement set then, not whichever is current.

Job Category itself has no history on the Employee - a promotion moves an
employee's whole trend to their new category, the same reading CBA Compliance
and Payroll Cost Trend both use.
"""

import frappe
from frappe import _
from frappe.utils import add_months, flt, formatdate, getdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company"):
		frappe.throw(_("Please select a Company."))

	employees = get_employees(filters)
	if not employees:
		return get_columns(), []

	ssas_by_employee = get_ssas([e.name for e in employees])
	cba_timeline = get_cba_timeline(filters.company)
	if not cba_timeline:
		frappe.msgprint(
			_("No submitted CBA found for {0}.").format(filters.company),
			alert=True, indicator="orange",
		)
		return get_columns(), []

	months = get_months(filters)
	rows = []
	for month_end in months:
		minimums = _minimums_as_of(cba_timeline, month_end)
		if minimums is None:
			continue

		below = 0
		shortfall = 0.0
		checked = 0
		for emp in employees:
			base = _latest_base(ssas_by_employee.get(emp.name, []), month_end)
			if base is None:
				continue
			minimum = minimums.get(emp.job_category)
			if minimum is None:
				continue
			checked += 1
			gap = flt(minimum) - flt(base)
			if gap > 0:
				below += 1
				shortfall += gap

		rows.append({
			"period": formatdate(month_end, "MMM yyyy"),
			"employees_checked": checked,
			"below_minimum": below,
			"total_shortfall": round(shortfall, 2),
		})

	return get_columns(), rows


def get_employees(filters):
	conditions = {"company": filters.company, "job_category": ("is", "set")}
	if filters.get("job_category"):
		conditions["job_category"] = filters.job_category
	return frappe.get_all("Employee", filters=conditions, fields=["name", "job_category"])


def get_ssas(employees):
	rows = frappe.get_all(
		"Salary Structure Assignment",
		filters={"employee": ("in", employees), "docstatus": 1},
		fields=["employee", "from_date", "base"],
		order_by="from_date asc",
	)
	by_employee = {}
	for row in rows:
		by_employee.setdefault(row.employee, []).append(row)
	return by_employee


def _latest_base(ssas, as_of):
	base = None
	for ssa in ssas:
		if getdate(ssa.from_date) <= as_of:
			base = flt(ssa.base)
		else:
			break
	return base


def get_cba_timeline(company):
	"""[(effective_start_date, {job_category: entry_minimum})], oldest first."""
	cbas = frappe.get_all(
		"CBA",
		filters={"docstatus": 1, "company": company},
		fields=["name", "effective_start_date"],
		order_by="effective_start_date asc",
	)
	timeline = []
	for cba in cbas:
		pay_rows = frappe.get_all(
			"CBA Pay Table",
			filters={"parent": cba.name, "parenttype": "CBA"},
			fields=["job_category", "entry_minimum", "current_basic_pay"],
		)
		minimums = {
			r.job_category: flt(r.entry_minimum) or flt(r.current_basic_pay)
			for r in pay_rows if r.job_category
		}
		timeline.append((getdate(cba.effective_start_date), minimums))
	return timeline


def _minimums_as_of(timeline, as_of):
	applicable = None
	for effective_date, minimums in timeline:
		if effective_date <= as_of:
			applicable = minimums
		else:
			break
	return applicable


def get_months(filters):
	"""The last day of every month from From Date to To Date, the two
	defaulting to the trailing twelve if either is left blank."""
	from datetime import timedelta

	from_date = getdate(filters.get("from_date")) if filters.get("from_date") else add_months(getdate(), -11)
	to_date = getdate(filters.get("to_date")) if filters.get("to_date") else getdate()

	months = []
	cursor = from_date.replace(day=1)
	while cursor <= to_date:
		next_month = add_months(cursor, 1)
		month_end = next_month - timedelta(days=1)
		months.append(min(month_end, to_date))
		cursor = next_month
	return months


def get_columns():
	return [
		{"label": _("Period"), "fieldname": "period", "fieldtype": "Data", "width": 110},
		{"label": _("Employees Checked"), "fieldname": "employees_checked", "fieldtype": "Int", "width": 130},
		{"label": _("Below Minimum"), "fieldname": "below_minimum", "fieldtype": "Int", "width": 120},
		{"label": _("Total Shortfall"), "fieldname": "total_shortfall", "fieldtype": "Currency", "width": 130},
	]
