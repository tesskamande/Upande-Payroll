# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

"""Who a CBA actually raised, from what, to what.

Applying an agreement writes pay with ``update_modified=False``, which leaves no
version history on the Employee - deliberately, since a bulk raise would bury
the record in noise. The CBA Application Log is written instead, and this reads
it back: the run, the people, and what it cost per month and per year.
"""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	rows = get_rows(filters)
	return get_columns(), rows, None, None, get_summary(rows)


def get_conditions(filters):
	conditions = []
	if filters.get("cba"):
		conditions.append("log.cba = %(cba)s")
	if filters.get("company"):
		conditions.append("log.company = %(company)s")
	if filters.get("job_category"):
		conditions.append("log.job_category = %(job_category)s")
	if filters.get("employee"):
		conditions.append("log.employee = %(employee)s")
	if filters.get("from_date"):
		conditions.append("log.applied_on >= %(from_date)s")
	if filters.get("to_date"):
		# The whole of the closing day, not midnight on it - applied_on is a
		# timestamp, and a run at 14:00 would otherwise fall outside its own date.
		conditions.append("log.applied_on < DATE_ADD(%(to_date)s, INTERVAL 1 DAY)")
	return ("where " + " and ".join(conditions)) if conditions else ""


def get_rows(filters):
	return frappe.db.sql(
		"""
		select
			log.cba,
			log.applied_on,
			log.employee,
			log.employee_name,
			log.job_category,
			log.previous_basic_pay,
			log.increase_amount,
			log.new_basic_pay,
			log.applied_by
		from `tabCBA Application Log` log
		{conditions}
		order by log.applied_on desc, log.job_category, log.employee_name
		""".format(conditions=get_conditions(filters)),
		filters,
		as_dict=True,
	)


def get_summary(rows):
	monthly = sum(flt(row.increase_amount) for row in rows)
	# Employees rather than log rows: someone covered by two agreements in the
	# period appears twice, and counting rows would overstate the headcount.
	headcount = len({row.employee for row in rows})
	return [
		{"label": _("Employees Raised"), "value": headcount, "datatype": "Int"},
		{
			"label": _("Monthly Increase"),
			"value": monthly,
			"datatype": "Currency",
			"indicator": "Blue",
		},
		{
			"label": _("Annual Increase"),
			"value": monthly * 12,
			"datatype": "Currency",
			"indicator": "Orange",
		},
	]


def get_columns():
	return [
		{"label": _("CBA"), "fieldname": "cba", "fieldtype": "Link",
		 "options": "CBA", "width": 210},
		{"label": _("Applied On"), "fieldname": "applied_on",
		 "fieldtype": "Datetime", "width": 160},
		{"label": _("Employee"), "fieldname": "employee", "fieldtype": "Link",
		 "options": "Employee", "width": 130},
		{"label": _("Employee Name"), "fieldname": "employee_name",
		 "fieldtype": "Data", "width": 180},
		{"label": _("Job Category"), "fieldname": "job_category",
		 "fieldtype": "Data", "width": 140},
		{"label": _("Previous Basic Pay"), "fieldname": "previous_basic_pay",
		 "fieldtype": "Currency", "width": 150},
		{"label": _("Increase"), "fieldname": "increase_amount",
		 "fieldtype": "Currency", "width": 130},
		{"label": _("New Basic Pay"), "fieldname": "new_basic_pay",
		 "fieldtype": "Currency", "width": 150},
		{"label": _("Applied By"), "fieldname": "applied_by", "fieldtype": "Link",
		 "options": "User", "width": 160},
	]
