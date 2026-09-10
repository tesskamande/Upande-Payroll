# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

"""One line per employee to be paid, carrying what a bank needs to move money.

The codes are not held on the Employee. A bank code belongs to the Bank and a
branch code to the Bank Branch, because a branch code is a property of a branch
and not of the people who bank there; storing it per employee means the same
code typed once per employee and drifting. So the figures come from the salary
slip and the routing is joined on behind them.

Nothing is filtered out for being incomplete. An employee with no account number
is exactly what the person running payroll needs to see, and dropping them would
make the report agree with itself while the bank file silently paid fewer people
than the register. The gaps are marked instead.
"""

import frappe
from frappe import _
from frappe.utils import flt, formatdate, getdate

DOCSTATUS = {"Draft": 0, "Submitted": 1, "Cancelled": 2}

# What a line needs before it can be sent, by how the employee is paid. A bank
# transfer needs the codes that route it; a mobile payment needs a number and
# no codes at all, and asking it for a Bank Code was reporting the wrong fault.
ROUTING_BY_MODE = {
	"M-Pesa": {"mpesa_number": "M-Pesa Number"},
	"Bank": {
		"bank_code": "Bank Code",
		"branch_code": "Branch Code",
		"account_number": "Account Number",
	},
}

# Salary Mode is blank on most employee records, so a blank is read as a bank
# transfer - which is what it has always been treated as here.
DEFAULT_MODE = "Bank"

# Neither is a remittance. They are shown, because they are on the payroll and
# somebody reconciling wants to see them, and held back with the reason.
NOT_REMITTED = ("Cash", "Cheque")


def execute(filters=None):
	filters = frappe._dict(filters or {})

	if filters.from_date and filters.to_date and filters.from_date > filters.to_date:
		frappe.throw(_("From Date cannot be after To Date"))

	slips = _get_slips(filters)
	if not slips:
		return _columns(), []

	routing = _routing([s.employee for s in slips])
	rows = _rows(slips, routing, filters)

	return _columns(), rows


def _get_slips(filters):
	conditions = {"docstatus": DOCSTATUS.get(filters.docstatus, 1)}
	if filters.company:
		conditions["company"] = filters.company
	if filters.from_date:
		conditions["start_date"] = (">=", filters.from_date)
	if filters.to_date:
		conditions["end_date"] = ("<=", filters.to_date)
	for fieldname in ("employee", "department", "payroll_entry"):
		if filters.get(fieldname):
			conditions[fieldname] = filters.get(fieldname)

	# Farm, bank and salary mode all live on the Employee, so each becomes a
	# list of employees rather than a condition on the slip. They narrow an
	# employee already chosen rather than replacing it, so a contradictory pair
	# of filters returns nothing instead of quietly ignoring one of them.
	employee_filters = {}
	if filters.get("farm"):
		employee_filters["custom_farm"] = filters.get("farm")
	if filters.get("bank"):
		employee_filters["bank_name"] = filters.get("bank")
	if filters.get("salary_mode"):
		mode = filters.get("salary_mode")
		# Salary Mode is blank on an employee nobody has set it for, and blank is
		# read as a bank transfer everywhere else here, so filtering to Bank has
		# to include them or the file quietly loses everyone unconfigured.
		employee_filters["salary_mode"] = (
			("in", [mode, "", None]) if mode == DEFAULT_MODE else mode
		)

	if employee_filters:
		matching = frappe.get_all("Employee", filters=employee_filters, pluck="name")
		if filters.get("employee"):
			matching = [e for e in matching if e == filters.get("employee")]
		# An empty list would be ignored by get_all, which would widen the
		# report instead of narrowing it, so no match has to exclude everything.
		conditions["employee"] = ("in", matching or [""])

	return frappe.get_all(
		"Salary Slip",
		filters=conditions,
		fields=[
			"name", "employee", "employee_name", "company",
			"start_date", "end_date", "status",
			"net_pay", "rounded_total",
		],
		order_by="employee_name, start_date",
	)


def _routing(employees):
	"""Bank, branch and the codes behind them, for the employees on the run.

	Two joins rather than fields on the Employee: the bank code is read from the
	Bank and the branch code from the Bank Branch, so correcting either one
	corrects every employee at once.
	"""
	if not employees:
		return {}

	rows = frappe.get_all(
		"Employee",
		filters={"name": ("in", list(set(employees)))},
		fields=[
			"name", "employee_number", "national_id", "salary_mode",
			"bank_name", "bank_ac_no", "custom_bank_branch", "custom_mpesa_number",
		],
	)

	banks = dict(
		frappe.get_all("Bank", fields=["name", "custom_bank_code"], as_list=True)
	)
	branches = {
		b.name: b
		for b in frappe.get_all(
			"Bank Branch", fields=["name", "branch_name", "branch_code"]
		)
	}

	out = {}
	for row in rows:
		branch = branches.get(row.custom_bank_branch) or frappe._dict()
		out[row.name] = frappe._dict(
			payroll_number=row.employee_number,
			national_id=row.national_id,
			salary_mode=row.salary_mode,
			bank_name=row.bank_name,
			bank_code=banks.get(row.bank_name),
			branch_name=branch.get("branch_name"),
			branch_code=branch.get("branch_code"),
			account_number=row.bank_ac_no,
			mpesa_number=row.custom_mpesa_number,
		)
	return out


def _remarks(slip, filters):
	"""What the bank prints against the entry on the employee's statement.

	A typed remark wins, so a company with its own wording keeps it. Otherwise
	the period the slip covers, which is the one thing every bank narration
	here has in common and the only thing derivable without guessing.
	"""
	if filters.get("remarks"):
		return filters.get("remarks")
	if not slip.end_date:
		return None
	return _("Salary {0}").format(formatdate(getdate(slip.end_date), "MMM yyyy"))


def _blockers(row, amount, status):
	"""Why this line cannot be sent, in the reader's words.

	Kept separate from the figures because every one of these is a real state a
	payroll ends up in - a starter whose account has not come through, a task
	worker who earned nothing this period, a slip whose deductions came to more
	than the pay - and a file built without checking would ask the bank to move
	a negative amount or open five transfers worth nothing.
	"""
	reasons = []
	mode = row.get("salary_mode") or DEFAULT_MODE

	if mode in NOT_REMITTED:
		reasons.append(_("Paid by {0}").format(_(mode)))
	else:
		for fieldname, label in ROUTING_BY_MODE.get(mode, ROUTING_BY_MODE[DEFAULT_MODE]).items():
			if not row.get(fieldname):
				reasons.append(_(label))

	if amount < 0:
		reasons.append(_("Negative Pay"))
	elif not amount:
		reasons.append(_("Nothing To Pay"))
	if status == "Withheld":
		reasons.append(_("Withheld"))
	return reasons


def _rows(slips, routing, filters):
	rows = []

	for slip in slips:
		route = routing.get(slip.employee) or frappe._dict()
		# What the bank is actually asked to move. Rounding is a company
		# setting, so read the rounded figure when payroll produced one and
		# fall back to net pay when rounding is switched off.
		amount = flt(slip.rounded_total) or flt(slip.net_pay)

		row = frappe._dict(
			bank_code=route.get("bank_code"),
			branch_code=route.get("branch_code"),
			bank_name=route.get("bank_name"),
			branch_name=route.get("branch_name"),
			account_number=route.get("account_number"),
			mpesa_number=route.get("mpesa_number"),
			payroll_number=route.get("payroll_number"),
			national_id=route.get("national_id"),
			salary_mode=route.get("salary_mode") or DEFAULT_MODE,
			# The docname, so the payroll number and the name can both open the
			# employee. It gets no column: a Link column would try to route to an
			# Employee named after the payroll number.
			employee=slip.employee,
			employee_name=slip.employee_name,
			amount=amount,
			remarks=_remarks(slip, filters),
			# No column of their own. They carry the reason a line is marked,
			# and the slip it came from, for anyone reading the export.
			salary_slip=slip.name,
			status=slip.status,
		)
		# Kept on the row without a column of its own: the formatter marks the
		# amount with it, so a line that cannot be sent is still visible.
		row.cannot_pay = ", ".join(_blockers(row, amount, slip.status))
		rows.append(row)

	return rows


def _columns():
	"""The ten columns asked for, in that order, each told how to sit.

	Alignment is set on every one of them rather than left to the table. The
	codes are Data fields holding digits, and frappe-datatable right-aligns any
	cell whose value merely looks numeric - so an ID of 32000008 would drift
	right while a payroll number of PN1004 stayed left, in neighbouring columns,
	which is what made the report read as ragged. Identifiers are text and sit
	left; only money sits right.
	"""
	def col(label, fieldname, width, align="left", fieldtype="Data", **kw):
		return dict(
			label=_(label), fieldname=fieldname, fieldtype=fieldtype,
			width=width, align=align, **kw
		)

	# Who is being paid, then where the money goes, then how much. The three
	# identifiers lead because that is what a payroll office reads down to find
	# a person; the routing follows in bank order, name before code, so a
	# reader checking a line against a bank circular runs left to right.
	return [
		col("Payroll Number", "payroll_number", 120),
		col("Employee Name", "employee_name", 200),
		col("ID Number", "national_id", 120),
		col("Salary Mode", "salary_mode", 100),
		col("Bank Name", "bank_name", 130, fieldtype="Link", options="Bank"),
		col("Bank Code", "bank_code", 90),
		col("Branch Name", "branch_name", 150),
		col("Branch Code", "branch_code", 100),
		col("Account Number", "account_number", 150),
		col("M-Pesa Number", "mpesa_number", 130),
		col("Amount", "amount", 130, align="right", fieldtype="Currency"),
		col("Remarks", "remarks", 170),
	]
