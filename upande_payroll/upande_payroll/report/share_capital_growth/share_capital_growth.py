# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

"""Share capital over time - deposits against withdrawals, by month and by
Shares Type, with the running balance that answers "how big is the pool now."

Figures come off submitted Share Transaction rows. Shares itself only ever
carries a current total_shares_to_date per employee, with no date on the
movement - the transaction is the only place a deposit or withdrawal is dated,
so it is the only thing a trend can be built from.
"""

import frappe
from frappe import _
from frappe.utils import flt, formatdate, getdate


def execute(filters=None):
	filters = frappe._dict(filters or {})

	transactions = get_transactions(filters)
	rows = group_rows(transactions)
	return get_columns(), rows, None, None, get_summary(transactions)


def get_transactions(filters):
	conditions = {"docstatus": 1}
	# Share Transaction carries no Company of its own - narrowed through the
	# employee instead, the same way it is narrowed by Shares Type.
	if filters.get("company"):
		employees = frappe.get_all("Employee", filters={"company": filters.company}, pluck="name")
		conditions["employee"] = ("in", employees or [""])
	if filters.get("from_date") and filters.get("to_date"):
		conditions["transaction_date"] = ("between", [filters.from_date, filters.to_date])
	elif filters.get("from_date"):
		conditions["transaction_date"] = (">=", filters.from_date)
	elif filters.get("to_date"):
		conditions["transaction_date"] = ("<=", filters.to_date)
	if filters.get("shares_type"):
		conditions["shares_type"] = filters.shares_type
	if filters.get("employee"):
		conditions["employee"] = filters.employee

	return frappe.get_all(
		"Share Transaction",
		filters=conditions,
		fields=["employee", "shares_type", "transaction_type", "amount", "transaction_date"],
		order_by="transaction_date",
	)


def group_rows(transactions):
	buckets = {}
	for row in transactions:
		period_start = getdate(row.transaction_date).replace(day=1)
		shares_type = row.shares_type or _("Unspecified")
		key = (period_start, shares_type)

		bucket = buckets.setdefault(key, {
			"period": formatdate(period_start, "MMM yyyy"),
			"shares_type": shares_type,
			"deposits": 0.0,
			"withdrawals": 0.0,
			"_sort": key,
		})
		if row.transaction_type == "Withdrawal":
			bucket["withdrawals"] += flt(row.amount)
		else:
			bucket["deposits"] += flt(row.amount)

	rows = sorted(buckets.values(), key=lambda b: b["_sort"])

	running = {}
	for row in rows:
		row["deposits"] = round(row["deposits"], 2)
		row["withdrawals"] = round(row["withdrawals"], 2)
		row["net"] = round(row["deposits"] - row["withdrawals"], 2)
		running.setdefault(row["shares_type"], 0.0)
		running[row["shares_type"]] += row["net"]
		row["running_balance"] = round(running[row["shares_type"]], 2)
		row.pop("_sort")
	return rows


def get_summary(transactions):
	deposits = sum(flt(t.amount) for t in transactions if t.transaction_type != "Withdrawal")
	withdrawals = sum(flt(t.amount) for t in transactions if t.transaction_type == "Withdrawal")
	return [
		{"label": _("Total Deposits"), "value": round(deposits, 2), "datatype": "Currency"},
		{"label": _("Total Withdrawals"), "value": round(withdrawals, 2), "datatype": "Currency"},
		{"label": _("Net Growth"), "value": round(deposits - withdrawals, 2), "datatype": "Currency"},
	]


def get_columns():
	return [
		{"label": _("Period"), "fieldname": "period", "fieldtype": "Data", "width": 110},
		{"label": _("Shares Type"), "fieldname": "shares_type", "fieldtype": "Data", "width": 150},
		{"label": _("Deposits"), "fieldname": "deposits", "fieldtype": "Currency", "width": 120},
		{"label": _("Withdrawals"), "fieldname": "withdrawals", "fieldtype": "Currency", "width": 120},
		{"label": _("Net"), "fieldname": "net", "fieldtype": "Currency", "width": 110},
		{"label": _("Running Balance"), "fieldname": "running_balance", "fieldtype": "Currency", "width": 130},
	]
