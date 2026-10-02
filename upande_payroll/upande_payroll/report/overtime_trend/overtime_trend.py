# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

"""Overtime hours and cost, grouped the way the question is actually being
asked - by month to see whether it is creeping up, by employee or department
to see who is carrying it, by Overtime Type to see which rate is driving cost.

Figures come off Overtime Details, the child table submitted Overtime Slips
carry their hours and cost on, not off the slip header - a slip can mix
Overtime Types in one period, and the header total would blur them.
"""

import frappe
from frappe import _
from frappe.utils import flt, formatdate, getdate

GROUP_LABELS = {
	"Month": _("Period"),
	"Employee": _("Employee"),
	"Department": _("Department"),
	"Overtime Type": _("Overtime Type"),
}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company"):
		frappe.throw(_("Please select a Company."))

	group_by = filters.get("group_by") or "Month"
	details = get_details(filters)
	rows = group_rows(details, group_by)
	return get_columns(group_by), rows


def get_details(filters):
	conditions = ["slip.docstatus = 1", "slip.company = %(company)s"]
	values = {"company": filters.company}

	if filters.get("from_date"):
		conditions.append("slip.posting_date >= %(from_date)s")
		values["from_date"] = filters.from_date
	if filters.get("to_date"):
		conditions.append("slip.posting_date <= %(to_date)s")
		values["to_date"] = filters.to_date
	if filters.get("employee"):
		conditions.append("slip.employee = %(employee)s")
		values["employee"] = filters.employee
	if filters.get("department"):
		conditions.append("slip.department = %(department)s")
		values["department"] = filters.department
	if filters.get("overtime_type"):
		conditions.append("det.overtime_type = %(overtime_type)s")
		values["overtime_type"] = filters.overtime_type

	return frappe.db.sql(
		f"""
		SELECT
			slip.employee, slip.employee_name, slip.department, slip.posting_date,
			det.overtime_type, det.overtime_duration, det.custom_amount, slip.name AS slip_name
		FROM `tabOvertime Slip` slip
		INNER JOIN `tabOvertime Details` det ON det.parent = slip.name
		WHERE {' AND '.join(conditions)}
		""",
		values,
		as_dict=True,
	)


def group_rows(details, group_by):
	buckets = {}
	for row in details:
		key, label = _group_key(row, group_by)
		bucket = buckets.setdefault(key, {
			"group": label,
			"hours": 0.0,
			"cost": 0.0,
			"slips": set(),
			"employees": set(),
			"_sort": key,
		})
		bucket["hours"] += flt(row.overtime_duration)
		bucket["cost"] += flt(row.custom_amount)
		bucket["slips"].add(row.slip_name)
		bucket["employees"].add(row.employee)

	rows = sorted(buckets.values(), key=lambda b: b["_sort"])
	for row in rows:
		row["hours"] = round(row["hours"], 2)
		row["cost"] = round(row["cost"], 2)
		row["overtime_slips"] = len(row.pop("slips"))
		row["employees"] = len(row.pop("employees"))
		row.pop("_sort")
	return rows


def _group_key(row, group_by):
	if group_by == "Employee":
		return row.employee, f"{row.employee_name} ({row.employee})"
	if group_by == "Department":
		dept = row.department or _("No Department")
		return dept, dept
	if group_by == "Overtime Type":
		ot_type = row.overtime_type or _("Unspecified")
		return ot_type, ot_type
	# Month, the default - sortable key is the first of the month so chart and
	# grid both read chronologically rather than alphabetically.
	period_start = getdate(row.posting_date).replace(day=1)
	return period_start, formatdate(period_start, "MMM yyyy")


def get_columns(group_by):
	return [
		{"label": GROUP_LABELS.get(group_by, _("Group")), "fieldname": "group",
		 "fieldtype": "Data", "width": 180},
		{"label": _("Overtime Hours"), "fieldname": "hours", "fieldtype": "Float", "width": 120},
		{"label": _("Overtime Cost"), "fieldname": "cost", "fieldtype": "Currency", "width": 130},
		{"label": _("Overtime Slips"), "fieldname": "overtime_slips", "fieldtype": "Int", "width": 110},
		{"label": _("Employees"), "fieldname": "employees", "fieldtype": "Int", "width": 100},
	]
