# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt

BREAKDOWN_FIELDS = {"Department": "grouping", "Employee": "employee"}


def execute(filters=None):
	filters = frappe._dict(filters or {})

	provisions = _provisions(filters)
	if not provisions:
		return _columns(filters), []

	if filters.breakdown in BREAKDOWN_FIELDS:
		return _columns(filters), _by_breakdown(provisions, filters)
	return _columns(filters), _by_period(provisions)


# ----------------------------------------------------------------------

def _provisions(filters):
	conditions = ["docstatus = 1"]
	params = dict(filters)

	for field, clause in (
		("company", "company = %(company)s"),
		("from_date", "to_date >= %(from_date)s"),
		("to_date", "to_date <= %(to_date)s"),
	):
		if filters.get(field):
			conditions.append(clause)

	return frappe.db.sql(
		"""
		SELECT name, from_date, to_date, total_liability, previous_liability,
			movement, journal_entry
		FROM `tabLeave Provision`
		WHERE {conditions}
		ORDER BY to_date ASC, creation ASC
		""".format(conditions=" AND ".join(conditions)),
		params,
		as_dict=True,
	)


def _by_period(provisions):
	"""One line per payroll period: what was owed, and how it moved."""
	rows = []
	for provision in provisions:
		people = frappe.db.count("Leave Provision Detail",
								 {"parent": provision.name,
								  "parenttype": "Leave Provision"})
		days = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(leave_balance), 0) FROM `tabLeave Provision Detail`
			WHERE parent = %s AND parenttype = 'Leave Provision'
			""", provision.name)[0][0]

		rows.append({
			"period": f"{provision.from_date} to {provision.to_date}",
			"leave_provision": provision.name,
			"employees": people,
			"days": flt(days, 2),
			"opening": flt(provision.previous_liability, 2),
			"movement": flt(provision.movement, 2),
			"liability": flt(provision.total_liability, 2),
			"journal_entry": provision.journal_entry,
		})
	return rows


def _by_breakdown(provisions, filters):
	"""Split the latest period by department or by person.

	Only the latest, because a liability is a standing figure - adding several
	periods together would report the same debt more than once.
	"""
	latest = provisions[-1]
	field = BREAKDOWN_FIELDS[filters.breakdown]

	lines = frappe.get_all(
		"Leave Provision Detail",
		filters={"parent": latest.name, "parenttype": "Leave Provision"},
		fields=["employee", "employee_name", "grouping", "leave_type",
				"leave_balance", "daily_rate", "liability"],
		order_by="grouping, employee_name",
	)

	if filters.breakdown == "Employee":
		return [{
			"period": f"{latest.from_date} to {latest.to_date}",
			"leave_provision": latest.name,
			"grouping": line.grouping,
			"employee": line.employee,
			"employee_name": line.employee_name,
			"days": flt(line.leave_balance, 2),
			"daily_rate": flt(line.daily_rate, 2),
			"liability": flt(line.liability, 2),
		} for line in lines]

	totals = {}
	for line in lines:
		key = line.get(field) or _("Unassigned")
		totals.setdefault(key, {"days": 0.0, "liability": 0.0, "employees": set()})
		totals[key]["days"] += flt(line.leave_balance)
		totals[key]["liability"] += flt(line.liability)
		totals[key]["employees"].add(line.employee)

	return [{
		"period": f"{latest.from_date} to {latest.to_date}",
		"leave_provision": latest.name,
		"grouping": key,
		"employees": len(data["employees"]),
		"days": flt(data["days"], 2),
		"liability": flt(data["liability"], 2),
	} for key, data in sorted(totals.items())]


# ----------------------------------------------------------------------

def _columns(filters):
	period = {"fieldname": "period", "label": _("Payroll Period"),
			  "fieldtype": "Data", "width": 200}
	provision = {"fieldname": "leave_provision", "label": _("Provision"),
				 "fieldtype": "Link", "options": "Leave Provision", "width": 140}
	liability = {"fieldname": "liability", "label": _("Liability"),
				 "fieldtype": "Currency", "options": "currency", "width": 150}
	days = {"fieldname": "days", "label": _("Leave Days"),
			"fieldtype": "Float", "precision": "2", "width": 110}

	if filters.breakdown == "Employee":
		return [
			period, provision,
			{"fieldname": "grouping", "label": _("Group"), "fieldtype": "Data",
			 "width": 150},
			{"fieldname": "employee", "label": _("Employee"), "fieldtype": "Link",
			 "options": "Employee", "width": 120},
			{"fieldname": "employee_name", "label": _("Name"), "fieldtype": "Data",
			 "width": 200},
			days,
			{"fieldname": "daily_rate", "label": _("Daily Rate"),
			 "fieldtype": "Currency", "options": "currency", "width": 130},
			liability,
		]

	if filters.breakdown == "Department":
		return [
			period, provision,
			{"fieldname": "grouping", "label": _("Group"), "fieldtype": "Data",
			 "width": 200},
			{"fieldname": "employees", "label": _("Employees"), "fieldtype": "Int",
			 "width": 100},
			days, liability,
		]

	return [
		period, provision,
		{"fieldname": "employees", "label": _("Employees"), "fieldtype": "Int",
		 "width": 100},
		days,
		{"fieldname": "opening", "label": _("Opening"), "fieldtype": "Currency",
		 "options": "currency", "width": 140},
		{"fieldname": "movement", "label": _("Movement"), "fieldtype": "Currency",
		 "options": "currency", "width": 140},
		liability,
		{"fieldname": "journal_entry", "label": _("Journal Entry"),
		 "fieldtype": "Link", "options": "Journal Entry", "width": 150},
	]
