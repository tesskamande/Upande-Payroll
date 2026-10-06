# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

"""Every open loan, bucketed by how overdue its oldest unpaid Loan Demand is.

A loan with no overdue demand is Current, whatever its balance. One is overdue
from the date of its oldest Loan Demand still carrying an outstanding_amount,
not from today backwards - so a demand issued in January and never paid ages
the same way whether it is checked in February or in July.

Open means Disbursed, Active, or Loan Closure Requested - a loan still
carrying a balance the borrower is expected to be repaying. Closed, Written
Off and Settled loans are done, by definition not at risk.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

OPEN_STATUSES = ("Disbursed", "Active", "Loan Closure Requested")

BUCKETS = [
	(0, 0, _("Current")),
	(1, 30, _("1-30 Days")),
	(31, 60, _("31-60 Days")),
	(61, 90, _("61-90 Days")),
	(91, None, _("90+ Days")),
]


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company"):
		frappe.throw(_("Please select a Company."))

	# A payroll-only client (no lending app at all) has no Loan doctype for
	# this report to read - nothing at risk to show, not an error page.
	if "lending" not in frappe.get_installed_apps():
		return get_columns(), []

	as_on = getdate(filters.get("as_on_date") or frappe.utils.today())
	loans = get_loans(filters)
	if not loans:
		return get_columns(), []

	oldest_overdue = get_oldest_overdue_demand(loans, as_on)
	rows = build_rows(loans, oldest_overdue, as_on)

	bucket_filter = filters.get("bucket")
	if bucket_filter:
		rows = [r for r in rows if r["bucket"] == bucket_filter]

	return get_columns(), rows, None, None, get_summary(rows)


def get_loans(filters):
	conditions = {"docstatus": 1, "company": filters.company, "status": ("in", OPEN_STATUSES)}
	if filters.get("loan_product"):
		conditions["loan_product"] = filters.loan_product
	if filters.get("applicant"):
		conditions["applicant"] = filters.applicant

	return frappe.get_all(
		"Loan",
		filters=conditions,
		fields=[
			"name", "applicant_type", "applicant", "loan_product",
			"disbursed_amount", "total_amount_paid", "status",
		],
		order_by="name",
	)


def get_oldest_overdue_demand(loans, as_on):
	"""{loan: oldest unpaid demand_date}, for demands due on or before as_on."""
	loan_names = [l.name for l in loans]
	demands = frappe.get_all(
		"Loan Demand",
		filters={
			"loan": ("in", loan_names),
			"docstatus": 1,
			"outstanding_amount": (">", 0),
			"demand_date": ("<=", as_on),
		},
		fields=["loan", "demand_date", "outstanding_amount"],
	)

	oldest = {}
	outstanding_total = {}
	for d in demands:
		date = getdate(d.demand_date)
		if d.loan not in oldest or date < oldest[d.loan]:
			oldest[d.loan] = date
		outstanding_total[d.loan] = outstanding_total.get(d.loan, 0.0) + flt(d.outstanding_amount)
	return oldest, outstanding_total


def build_rows(loans, overdue_data, as_on):
	oldest, overdue_outstanding = overdue_data
	rows = []
	for loan in loans:
		overdue_since = oldest.get(loan.name)
		days_overdue = (as_on - overdue_since).days if overdue_since else 0
		bucket = _bucket_for(days_overdue)
		outstanding = round(flt(loan.disbursed_amount) - flt(loan.total_amount_paid), 2)

		rows.append({
			"loan": loan.name,
			"applicant": loan.applicant,
			"applicant_type": loan.applicant_type,
			"loan_product": loan.loan_product,
			"status": loan.status,
			"outstanding_amount": outstanding,
			"overdue_amount": round(overdue_outstanding.get(loan.name, 0.0), 2),
			"days_overdue": days_overdue,
			"bucket": bucket,
		})
	return rows


def _bucket_for(days_overdue):
	for low, high, label in BUCKETS:
		if high is None or days_overdue <= high:
			if days_overdue >= low:
				return label
	return BUCKETS[-1][2]


def get_summary(rows):
	by_bucket = {}
	for row in rows:
		by_bucket.setdefault(row["bucket"], 0.0)
		by_bucket[row["bucket"]] += flt(row["outstanding_amount"])

	total = sum(by_bucket.values())
	at_risk = total - by_bucket.get(_("Current"), 0.0)

	return [
		{"label": _("Open Loans"), "value": len(rows), "datatype": "Int"},
		{"label": _("Total Outstanding"), "value": round(total, 2), "datatype": "Currency"},
		{
			"label": _("At Risk (Overdue)"), "value": round(at_risk, 2), "datatype": "Currency",
			"indicator": "Red" if at_risk else "Green",
		},
	]


def get_columns():
	return [
		{"label": _("Loan"), "fieldname": "loan", "fieldtype": "Link", "options": "Loan", "width": 140},
		{"label": _("Applicant"), "fieldname": "applicant", "fieldtype": "Dynamic Link",
		 "options": "applicant_type", "width": 130},
		{"label": _("Loan Product"), "fieldname": "loan_product", "fieldtype": "Link",
		 "options": "Loan Product", "width": 150},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
		{"label": _("Outstanding"), "fieldname": "outstanding_amount", "fieldtype": "Currency", "width": 120},
		{"label": _("Overdue Amount"), "fieldname": "overdue_amount", "fieldtype": "Currency", "width": 130},
		{"label": _("Days Overdue"), "fieldname": "days_overdue", "fieldtype": "Int", "width": 100},
		{"label": _("Bucket"), "fieldname": "bucket", "fieldtype": "Data", "width": 110},
	]
