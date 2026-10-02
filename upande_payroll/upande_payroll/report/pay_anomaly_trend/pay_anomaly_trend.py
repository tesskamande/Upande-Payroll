# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

"""How many lines Bank Remittance would have left out of the file, trended
monthly, by why.

Not a second definition of "cannot be paid" - this reuses Bank Remittance's
own _routing() and _blockers() so a line is flagged here for exactly the same
reason it would be flagged on the file that actually goes to the bank, nothing
reinvented. What is new here is the trend: the remittance report only ever
shows the current run, so "how many were held back last month, and why" has
had no answer until now.
"""

import frappe
from frappe import _
from frappe.utils import flt, formatdate, getdate

from upande_payroll.upande_payroll.report.bank_remittance.bank_remittance import (
	_blockers,
	_routing,
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company"):
		frappe.throw(_("Please select a Company."))

	slips = get_slips(filters)
	if not slips:
		return get_columns(), []

	routing = _routing([s.employee for s in slips])
	rows = group_rows(slips, routing)
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
		fields=["name", "employee", "end_date", "status", "net_pay", "rounded_total"],
	)


def group_rows(slips, routing):
	buckets = {}
	for slip in slips:
		route = routing.get(slip.employee) or frappe._dict()
		amount = flt(slip.rounded_total) or flt(slip.net_pay)
		reasons = _blockers(route, amount, slip.status)
		if not reasons:
			continue

		period_start = getdate(slip.end_date).replace(day=1)
		for reason in reasons:
			key = (period_start, reason)
			bucket = buckets.setdefault(key, {
				"period": formatdate(period_start, "MMM yyyy"),
				"reason": reason,
				"count": 0,
				"amount": 0.0,
				"_sort": key,
			})
			bucket["count"] += 1
			bucket["amount"] += flt(amount)

	rows = sorted(buckets.values(), key=lambda b: b["_sort"])
	for row in rows:
		row["amount"] = round(row["amount"], 2)
		row.pop("_sort")
	return rows


def get_summary(rows):
	return [
		{"label": _("Flagged Lines"), "value": sum(r["count"] for r in rows), "datatype": "Int"},
	]


def get_columns():
	return [
		{"label": _("Period"), "fieldname": "period", "fieldtype": "Data", "width": 110},
		{"label": _("Reason"), "fieldname": "reason", "fieldtype": "Data", "width": 200},
		{"label": _("Lines"), "fieldname": "count", "fieldtype": "Int", "width": 90},
		{"label": _("Amount"), "fieldname": "amount", "fieldtype": "Currency", "width": 120},
	]
