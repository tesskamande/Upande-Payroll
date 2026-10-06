# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

"""How leveraged the workforce is - the payroll-side view of lending, not the
lending-side view.

The lending reports already show a loan's own health: its balance, its
schedule, whether it is overdue. None of them ask what a loan costs the
employee against their pay, or how much of the workforce carries one at all -
that is what this answers, per employee, from what payroll actually deducted
for loan repayment in the period chosen.

Debt-service ratio here is loan repayment collected through payroll divided by
net pay before that repayment - rows with no Salary Slip Loan amount in the
period are left out rather than shown at 0%, since "not carrying a loan this
period" is not the same claim as "carrying one for free."
"""

import frappe
from frappe import _
from frappe.utils import flt

OPEN_STATUSES = ("Disbursed", "Active", "Loan Closure Requested")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company"):
		frappe.throw(_("Please select a Company."))

	rows = get_rows(filters)
	only_over = flt(filters.get("only_ratio_above") or 0)
	if only_over:
		rows = [r for r in rows if r["debt_service_ratio"] >= only_over]

	return get_columns(), rows, None, None, get_summary(filters, rows)


def get_rows(filters):
	conditions = {"docstatus": 1, "company": filters.company}
	if filters.get("from_date"):
		conditions["start_date"] = (">=", filters.from_date)
	if filters.get("to_date"):
		conditions["end_date"] = ("<=", filters.to_date)

	slips = frappe.get_all(
		"Salary Slip", filters=conditions,
		fields=["name", "employee", "employee_name", "net_pay"],
	)
	if not slips:
		return []

	slip_names = [s.name for s in slips]
	loan_rows = frappe.db.sql(
		"""SELECT parent, SUM(total_payment) AS repayment FROM `tabSalary Slip Loan`
		WHERE parent IN %(slips)s GROUP BY parent""",
		{"slips": slip_names},
		as_dict=True,
	)
	repayment_by_slip = {r.parent: flt(r.repayment) for r in loan_rows}

	rows = []
	for slip in slips:
		repayment = repayment_by_slip.get(slip.name, 0.0)
		if repayment <= 0:
			continue
		gross_before_repayment = flt(slip.net_pay) + repayment
		ratio = round((repayment / gross_before_repayment * 100), 1) if gross_before_repayment else 0.0
		rows.append({
			"employee": slip.employee,
			"employee_name": slip.employee_name,
			"salary_slip": slip.name,
			"net_pay": flt(slip.net_pay),
			"loan_repayment": round(repayment, 2),
			"debt_service_ratio": ratio,
		})

	rows.sort(key=lambda r: -r["debt_service_ratio"])
	return rows


def get_summary(filters, rows):
	active = frappe.db.count("Employee", {"company": filters.company, "status": "Active"})
	# Salary Slip Loan (what the rows above are built from) is HRMS core and
	# always present; Loan itself is not - a payroll-only client without the
	# lending app has nothing here to count, not an error to raise.
	if "lending" in frappe.get_installed_apps():
		borrowers = frappe.db.sql(
			"""SELECT COUNT(DISTINCT applicant) FROM `tabLoan`
			WHERE company=%(company)s AND docstatus=1 AND status IN %(statuses)s AND applicant_type='Employee'""",
			{"company": filters.company, "statuses": OPEN_STATUSES},
		)[0][0] or 0
	else:
		borrowers = 0

	pct_with_loan = round((borrowers / active * 100), 1) if active else 0.0
	avg_ratio = round(sum(r["debt_service_ratio"] for r in rows) / len(rows), 1) if rows else 0.0

	return [
		{"label": _("Active Employees"), "value": active, "datatype": "Int"},
		{"label": _("Borrowers (Open Loans)"), "value": borrowers, "datatype": "Int"},
		{"label": _("% of Workforce With a Loan"), "value": pct_with_loan, "datatype": "Percent"},
		{"label": _("Average Debt-Service Ratio"), "value": avg_ratio, "datatype": "Percent"},
	]


def get_columns():
	return [
		{"label": _("Employee"), "fieldname": "employee", "fieldtype": "Link",
		 "options": "Employee", "width": 130},
		{"label": _("Employee Name"), "fieldname": "employee_name", "fieldtype": "Data", "width": 160},
		{"label": _("Salary Slip"), "fieldname": "salary_slip", "fieldtype": "Link",
		 "options": "Salary Slip", "width": 140},
		{"label": _("Net Pay"), "fieldname": "net_pay", "fieldtype": "Currency", "width": 110},
		{"label": _("Loan Repayment"), "fieldname": "loan_repayment", "fieldtype": "Currency", "width": 130},
		{"label": _("Debt-Service Ratio"), "fieldname": "debt_service_ratio", "fieldtype": "Percent", "width": 140},
	]
