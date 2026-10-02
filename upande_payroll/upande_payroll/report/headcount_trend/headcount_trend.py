# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

"""Who joined, who left, and the net of it, month by month.

A joiner is counted in the month of date_of_joining, a leaver in the month of
relieving_date - both dated events on the Employee, not read off payroll. An
employee with no relieving_date is never counted as a leaver, whatever their
status; Left with no date recorded has nothing to trend them by.
"""

import frappe
from frappe import _
from frappe.utils import formatdate, getdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company"):
		frappe.throw(_("Please select a Company."))

	joiners, leavers = get_events(filters)
	rows = build_rows(joiners, leavers, filters)
	return get_columns(), rows


def get_events(filters):
	conditions = {"company": filters.company}
	if filters.get("job_category"):
		conditions["job_category"] = filters.job_category

	joiners = frappe.get_all(
		"Employee",
		filters={**conditions, "date_of_joining": ("is", "set")},
		fields=["name", "date_of_joining"],
	)
	leavers = frappe.get_all(
		"Employee",
		filters={**conditions, "relieving_date": ("is", "set")},
		fields=["name", "relieving_date"],
	)
	return joiners, leavers


def build_rows(joiners, leavers, filters):
	from_date = getdate(filters.get("from_date")) if filters.get("from_date") else None
	to_date = getdate(filters.get("to_date")) if filters.get("to_date") else None

	by_month = {}

	def in_range(date):
		if from_date and date < from_date:
			return False
		if to_date and date > to_date:
			return False
		return True

	for row in joiners:
		date = getdate(row.date_of_joining)
		if not in_range(date):
			continue
		key = date.replace(day=1)
		by_month.setdefault(key, {"joiners": 0, "leavers": 0})
		by_month[key]["joiners"] += 1

	for row in leavers:
		date = getdate(row.relieving_date)
		if not in_range(date):
			continue
		key = date.replace(day=1)
		by_month.setdefault(key, {"joiners": 0, "leavers": 0})
		by_month[key]["leavers"] += 1

	rows = []
	running_net = 0
	for key in sorted(by_month):
		counts = by_month[key]
		running_net += counts["joiners"] - counts["leavers"]
		rows.append({
			"period": formatdate(key, "MMM yyyy"),
			"joiners": counts["joiners"],
			"leavers": counts["leavers"],
			"net_change": counts["joiners"] - counts["leavers"],
			"running_headcount_change": running_net,
		})
	return rows


def get_columns():
	return [
		{"label": _("Period"), "fieldname": "period", "fieldtype": "Data", "width": 110},
		{"label": _("Joiners"), "fieldname": "joiners", "fieldtype": "Int", "width": 90},
		{"label": _("Leavers"), "fieldname": "leavers", "fieldtype": "Int", "width": 90},
		{"label": _("Net Change"), "fieldname": "net_change", "fieldtype": "Int", "width": 100},
		{"label": _("Running Change"), "fieldname": "running_headcount_change", "fieldtype": "Int", "width": 120},
	]
