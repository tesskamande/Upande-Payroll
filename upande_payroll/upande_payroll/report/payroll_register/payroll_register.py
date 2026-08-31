# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt

DOCSTATUS = {"Draft": 0, "Submitted": 1, "Cancelled": 2}

# Prefixes keep the four kinds of column apart in one flat row. Two components
# can share a name across earnings and deductions - a company that carries both
# "Life Insurance Premium" as a benefit and as a deduction - and without these
# one would silently overwrite the other.
EARNING = "e_"
DEDUCTION = "d_"
EMPLOYER = "r_"
LOAN = "l_"
MEMO = "m_"


def execute(filters=None):
	filters = frappe._dict(filters or {})

	if filters.from_date and filters.to_date and filters.from_date > filters.to_date:
		frappe.throw(_("From Date cannot be after To Date"))

	slips = _get_slips(filters)
	if not slips:
		return _columns([], [], [], [], [], filters), []

	details = _slip_details([s.name for s in slips])
	loans = _loan_rows([s.name for s in slips])

	earnings, deductions, employer, memo = _component_names(details)
	products = sorted({row.loan_product for row in loans if row.loan_product})

	return (
		_columns(earnings, deductions, employer, memo, products, filters),
		_rows(slips, details, loans, filters),
	)


# ----------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------

def _get_slips(filters):
	conditions = {"docstatus": DOCSTATUS.get(filters.docstatus, 1)}
	if filters.company:
		conditions["company"] = filters.company
	if filters.from_date:
		conditions["start_date"] = (">=", filters.from_date)
	if filters.to_date:
		conditions["end_date"] = ("<=", filters.to_date)
	for fieldname in ("employee", "department", "designation", "branch", "payroll_entry"):
		if filters.get(fieldname):
			conditions[fieldname] = filters.get(fieldname)

	return frappe.get_all(
		"Salary Slip",
		filters=conditions,
		fields=[
			"name", "employee", "employee_name", "branch", "department", "designation",
			"company", "start_date", "end_date", "status",
			"total_working_days", "payment_days", "leave_without_pay", "absent_days",
			"gross_pay", "total_deduction", "total_loan_repayment",
			"custom_total_actual_repayment", "custom_total_deferred_deductions",
			"net_pay", "rounded_total",
		],
		order_by="employee_name, start_date",
	)


def _slip_details(slip_names):
	"""Every component row on these payslips, with the flags that decide how it
	is treated. Read in one query rather than opening each payslip: a register
	is run over a whole month and loading the documents made it the slowest
	report on the site."""
	return frappe.get_all(
		"Salary Detail",
		filters={"parent": ("in", slip_names), "parenttype": "Salary Slip"},
		fields=[
			"parent", "parentfield", "salary_component", "amount",
			"do_not_include_in_total", "statistical_component",
		],
	)


def _loan_rows(slip_names):
	"""What each loan actually took this period.

	total_payment is the figure to read: the two thirds rule writes back to it
	what it allowed, so it is what the employee really lost - not the scheduled
	instalment. The scheduled amount and what could not be taken are carried
	alongside it for the columns that show the shortfall.
	"""
	return frappe.get_all(
		"Salary Slip Loan",
		filters={"parent": ("in", slip_names), "parenttype": "Salary Slip"},
		fields=[
			"parent", "loan", "loan_product", "total_payment",
			"principal_amount", "interest_amount",
			"custom_scheduled_payment", "custom_deferred_amount", "custom_arrears_deferred",
		],
	)


def _component_names(details):
	"""Which components to give a column, split three ways.

	A statistical component is a working figure, never money, so it is left out
	entirely. An employer contribution is money but not the employee's, so it
	gets its own section rather than sitting among their deductions where it
	would look like something they paid.
	"""
	earnings, deductions, employer, memo = set(), set(), set(), set()

	for row in details:
		if row.statistical_component or not flt(row.amount):
			continue
		bucket = _bucket_for(row)
		{"earning": earnings, "deduction": deductions,
		 "employer": employer, "memo": memo}[bucket].add(row.salary_component)

	return sorted(earnings), sorted(deductions), sorted(employer), sorted(memo)


def _bucket_for(row, employer_side=None):
	"""Which section a component row belongs in.

	A row the payslip keeps out of its own totals is not money the employee was
	paid or lost - it is a marker like Taxable Income, or a Gross Pay figure the
	structure carries for a formula to read. Those go to the memo columns after
	Net Pay rather than sitting among the deductions, where they read as
	something that came off the pay.

	The exception is an employer contribution, which is real money and is also
	kept out of the totals - correctly, because it is the company's cost, not
	the employee's. It gets its own section.
	"""
	if employer_side is None:
		employer_side = _employer_components()
	if row.salary_component in employer_side:
		return "employer"
	if row.do_not_include_in_total:
		return "memo"
	return "earning" if row.parentfield == "earnings" else "deduction"


def _employer_components():
	return set(frappe.get_all(
		"Salary Component",
		filters={"custom_is_employer_contribution": 1},
		pluck="name",
	))


# ----------------------------------------------------------------------
# Columns
# ----------------------------------------------------------------------

def _columns(earnings, deductions, employer, memo, products, filters):
	columns = [
		_col(_("Salary Slip"), "name", "Link", 180, options="Salary Slip"),
		_col(_("Employee"), "employee", "Link", 110, options="Employee"),
		_col(_("Employee Name"), "employee_name", "Data", 180),
		_col(_("Status"), "status", "Data", 90),
		_col(_("Department"), "department", "Link", 130, options="Department"),
		_col(_("Designation"), "designation", "Link", 130, options="Designation"),
		_col(_("Branch"), "branch", "Link", 120, options="Branch"),
		_col(_("From"), "start_date", "Date", 95),
		_col(_("To"), "end_date", "Date", 95),
		_col(_("Payment Days"), "payment_days", "Float", 95),
		_col(_("Absent Days"), "absent_days", "Float", 90),
		_col(_("LWP"), "leave_without_pay", "Float", 70),
	]

	for name in earnings:
		columns.append(_col(name, EARNING + frappe.scrub(name), "Currency", 130))
	columns.append(_col(_("Gross Pay"), "gross_pay", "Currency", 130))

	for name in deductions:
		columns.append(_col(name, DEDUCTION + frappe.scrub(name), "Currency", 130))

	for product in products:
		columns.append(_col(product, LOAN + frappe.scrub(product), "Currency", 130))

	columns.extend([
		_col(_("Total Loan Repayment"), "loan_repayment", "Currency", 140),
		_col(_("Total Deductions"), "total_deductions", "Currency", 140),
		_col(_("Net Pay"), "net_pay", "Currency", 130),
	])

	if filters.show_deferred:
		columns.extend([
			_col(_("Loan Instalment Due"), "loan_scheduled", "Currency", 140),
			# What the money that WAS taken actually settled. The two thirds rule
			# puts interest before principal, so a capped repayment can service a
			# month's interest and repay nothing at all - which is the difference
			# between a loan that is being repaid and one that is standing still.
			_col(_("Interest Recovered"), "loan_interest", "Currency", 140),
			_col(_("Principal Recovered"), "loan_principal", "Currency", 145),
			_col(_("Not Collected"), "total_deferred", "Currency", 130),
		])

	for name in memo:
		columns.append(_col(name, MEMO + frappe.scrub(name), "Currency", 140))

	# Last, and only when asked for: the employer's own cost is a different
	# question from what the employee was paid, and it doubles the width of a
	# register that is usually being read for the payslip figures.
	if filters.show_employer_cost:
		for name in employer:
			columns.append(_col(name, EMPLOYER + frappe.scrub(name), "Currency", 140))
		columns.append(_col(_("Total Employer Cost"), "employer_cost", "Currency", 150))
		columns.append(_col(_("Cost to Company"), "cost_to_company", "Currency", 150))

	return columns


def _col(label, fieldname, fieldtype, width, options=None):
	column = {"label": label, "fieldname": fieldname, "fieldtype": fieldtype, "width": width}
	if options:
		column["options"] = options
	return column


# ----------------------------------------------------------------------
# Rows
# ----------------------------------------------------------------------

def _rows(slips, details, loans, filters):
	employer_side = _employer_components()
	prefixes = {"earning": EARNING, "deduction": DEDUCTION,
				"employer": EMPLOYER, "memo": MEMO}

	by_slip = {}
	for row in details:
		if row.statistical_component or not flt(row.amount):
			continue
		if row.parentfield not in ("earnings", "deductions"):
			continue
		bucket = by_slip.setdefault(row.parent, {})
		key = prefixes[_bucket_for(row, employer_side)] + frappe.scrub(row.salary_component)
		bucket[key] = flt(bucket.get(key, 0.0) + flt(row.amount), 2)

	loans_by_slip = {}
	for row in loans:
		loans_by_slip.setdefault(row.parent, []).append(row)

	rows = []
	for slip in slips:
		row = {
			"name": slip.name,
			"employee": slip.employee,
			"employee_name": slip.employee_name,
			"status": slip.status,
			"department": slip.department,
			"designation": slip.designation,
			"branch": slip.branch,
			"start_date": slip.start_date,
			"end_date": slip.end_date,
			"payment_days": slip.payment_days,
			"absent_days": slip.absent_days,
			"leave_without_pay": slip.leave_without_pay,
			"gross_pay": flt(slip.gross_pay, 2),
			"net_pay": flt(slip.net_pay, 2),
		}
		row.update(by_slip.get(slip.name, {}))

		collected = scheduled = interest = principal = 0.0
		for loan in loans_by_slip.get(slip.name, []):
			taken = flt(loan.total_payment)
			collected += taken
			scheduled += flt(loan.custom_scheduled_payment) or taken
			interest += flt(loan.interest_amount)
			principal += flt(loan.principal_amount)
			if loan.loan_product:
				key = LOAN + frappe.scrub(loan.loan_product)
				row[key] = flt(flt(row.get(key, 0.0)) + taken, 2)

		row["loan_repayment"] = flt(collected, 2)
		# What the employee lost altogether. HRMS keeps loans out of
		# total_deduction - net pay is gross less deductions less loans - so the
		# two have to be added for a register that answers "what came off".
		row["total_deductions"] = flt(flt(slip.total_deduction) + collected, 2)

		if filters.show_deferred:
			row["loan_scheduled"] = flt(scheduled, 2)
			row["loan_interest"] = flt(interest, 2)
			row["loan_principal"] = flt(principal, 2)
			row["total_deferred"] = flt(slip.custom_total_deferred_deductions, 2)

		if filters.show_employer_cost:
			cost = sum(
				value for key, value in row.items()
				if isinstance(key, str) and key.startswith(EMPLOYER)
			)
			row["employer_cost"] = flt(cost, 2)
			row["cost_to_company"] = flt(flt(slip.gross_pay) + cost, 2)

		rows.append(row)

	return rows
