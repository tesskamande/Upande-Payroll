import frappe
from frappe import _
from frappe.utils import flt

ABSENCE_CATEGORY = "Absence / Unpaid Deduction"

# Employee field carrying that employee's gross pay account, used when the
# company posts gross pay Per Employee. Ships with the app, so it is a fixed
# name rather than something each company has to point a setting at.
EMPLOYEE_EXPENSE_ACCOUNT_FIELD = "custom_salary_expense_account"


def rewrite_payroll_journal(doc, method=None):
	"""Rebuild the accrual Journal Entry that Payroll Entry creates.

	Hooked on Journal Entry ``before_insert``. Core builds the JE with one line
	per salary component and links it back to the Payroll Entry; this replaces
	the ``accounts`` table with a posting that follows the company's own rules,
	then lets core carry on with its own plumbing (currency, dimensions,
	linking the slips, submission).

	Only the debit side varies between companies. Deductions, employer
	contributions, loans and net pay behave identically everywhere.
	"""
	# The accrual entry only. Core tags the Payroll Payable row of the salary
	# PAYMENT entry with the same reference_type, so the reference alone does
	# not tell the two apart - without this, clicking Make Bank Entry rebuilds
	# the payment as a second accrual: expense and every statutory liability
	# posted twice, Payroll Payable at 2x instead of cleared, and the bank never
	# credited. It balances, so nothing complains.
	#
	# The Server Script this replaced opened with the same check.
	if doc.voucher_type != "Journal Entry":
		return

	payroll_entry = _find_payroll_entry(doc)
	if not payroll_entry:
		return

	pe = frappe.get_doc("Payroll Entry", payroll_entry)
	settings = frappe.get_cached_doc("Company Payroll Settings", pe.company)

	slips = frappe.get_all(
		"Salary Slip",
		filters={"payroll_entry": pe.name, "docstatus": 1},
		fields=["name", "employee", "salary_structure", "gross_pay", "net_pay"],
	)
	if not slips:
		return

	debits, credits = {}, {}
	absence_components = _absence_components(settings)

	absence_reduces_gross = settings.gross_pay_account_method != "Per Salary Component"
	_add_gross_pay(pe, settings, slips, absence_components, debits)
	_add_employer_contributions(pe, debits, credits)
	_add_employee_deductions(
		pe, credits,
		skip=absence_components if absence_reduces_gross else set(),
	)
	_add_loan_repayments(pe, credits)

	if not pe.payroll_payable_account:
		frappe.throw(_("Payroll Payable Account is not set on {0}.").format(pe.name))
	for slip in slips:
		_split(credits, pe.payroll_payable_account, pe,
			   slip.employee, slip.salary_structure, slip.net_pay)

	_apply(doc, pe, settings, debits, credits)


# ----------------------------------------------------------------------
# Trigger
# ----------------------------------------------------------------------

def _split(bucket, account, pe, employee, structure, amount):
	"""Add an amount to the posting, apportioned across the employee's cost centres.

	The bucket is keyed on (account, cost centre) rather than the account alone.
	Aggregating by account was what lost the cost centre: two people on
	different farms sharing one expense account collapsed into a single figure,
	and by the time the rows were written there was no employee left to ask, so
	every line took the Payroll Entry's own cost centre.

	Where an employee's cost is split across several centres the amount is split
	with it, so the halves land where the work was done.
	"""
	amount = flt(amount, 2)
	if not account or not amount:
		return

	shares = _cost_centres(pe, employee, structure)
	remaining = amount
	last = len(shares) - 1
	for index, item in enumerate(shares.items()):
		centre = item[0]
		# The final share takes the rounding, so the split always sums to the
		# amount rather than to a cent either side of it.
		share = remaining if index == last else flt(amount * flt(item[1]) / 100.0, 2)
		remaining = flt(remaining - share, 2)
		if not share:
			continue
		key = (account, centre)
		bucket[key] = flt(bucket.get(key, 0.0) + share, 2)


def _cost_centres(pe, employee, structure):
	"""{cost centre: percentage} for one employee.

	Asked of the Payroll Entry itself rather than worked out here: core already
	resolves the assignment's own split, then the employee, then the department,
	then falls back to the run's cost centre - and caches the answer per
	employee. Reimplementing that would be one more thing to keep in step.
	"""
	try:
		shares = pe.get_payroll_cost_centers_for_employee(employee, structure)
	except Exception:
		shares = None
	return shares or {pe.get("custom_cost_center") or pe.get("cost_center"): 100}


def _find_payroll_entry(doc):
	"""Core tags the payable row with reference_type/reference_name pointing at
	the Payroll Entry (payroll_entry.py, get_accounting_entries_and_payable_amount).
	That is a far steadier signal than matching on the remark text."""
	for row in doc.get("accounts") or []:
		if row.reference_type == "Payroll Entry" and row.reference_name:
			return row.reference_name
	return None


def _absence_components(settings):
	return {
		row.salary_component
		for row in (settings.statutory_income_component_mapping or [])
		if row.category == ABSENCE_CATEGORY
	}


# ----------------------------------------------------------------------
# Debit side - the only part that differs between companies
# ----------------------------------------------------------------------

def _add_gross_pay(pe, settings, slips, absence_components, debits):
	method = settings.gross_pay_account_method or "Per Salary Component"

	if method == "Per Salary Component":
		# Mirrors the payslip: every earning hits its own account, and absence
		# is left to post separately as a credit.
		rows = frappe.db.sql(
			"""
			SELECT ss.employee, ss.salary_structure, sca.account, SUM(sd.amount) AS total
			FROM `tabSalary Slip` ss
			JOIN `tabSalary Detail` sd ON sd.parent = ss.name
			JOIN `tabSalary Component Account` sca
			  ON sca.parent = sd.salary_component AND sca.company = %s
			WHERE ss.payroll_entry = %s AND ss.docstatus = 1
			  AND sd.parentfield = 'earnings'
			  AND IFNULL(sd.do_not_include_in_total, 0) = 0
			GROUP BY ss.employee, ss.salary_structure, sca.account
			""",
			(pe.company, pe.name), as_dict=True,
		)
		for row in rows:
			_split(debits, row.account, pe, row.employee, row.salary_structure, row.total)
		return

	# Per Employee / Single Account: debit what the employee actually cost, so
	# absence is netted off rather than shown as a contra credit.
	for slip in slips:
		if method == "Single Account":
			account = settings.single_gross_pay_account
			if not account:
				frappe.throw(_("Set Single Gross Pay Account in Company Payroll Settings for {0}.")
							 .format(pe.company))
		else:
			account = frappe.db.get_value(
				"Employee", slip.employee, EMPLOYEE_EXPENSE_ACCOUNT_FIELD)
			if not account:
				frappe.throw(_("Employee {0} has no Salary Expense Account set.")
							 .format(slip.employee))

		absence = _absence_total(slip.name, absence_components)
		amount = flt(flt(slip.gross_pay) - absence, 2)
		_split(debits, account, pe, slip.employee, slip.salary_structure, amount)


def _absence_total(slip_name, absence_components):
	if not absence_components:
		return 0.0
	rows = frappe.get_all(
		"Salary Detail",
		filters={
			"parent": slip_name, "parentfield": "deductions",
			"salary_component": ("in", list(absence_components)),
		},
		fields=["amount"],
	)
	return flt(sum(flt(r.amount) for r in rows), 2)


# ----------------------------------------------------------------------
# Credit side - identical for every company
# ----------------------------------------------------------------------

def _add_employer_contributions(pe, debits, credits):
	"""Employer contributions carry two accounts. The component is typed as a
	Deduction, so its standard Account is the liability owed to the fund and is
	credited, exactly as core treats any deduction. The company's own cost is
	debited to the Employer Expense Account alongside it.

	Deriving the split from the account's ``account_type`` instead - as the
	Server Scripts did - is unreliable: most charts leave that field blank, and
	every contribution then silently posts as a credit with no expense at all."""
	rows = frappe.db.sql(
		"""
		SELECT ss.employee, ss.salary_structure, sd.salary_component, sca.account,
		       sca.custom_employer_expense_account AS expense_account,
		       SUM(sd.amount) AS total
		FROM `tabSalary Slip` ss
		JOIN `tabSalary Detail` sd ON sd.parent = ss.name
		JOIN `tabSalary Component` sc ON sc.name = sd.salary_component
		JOIN `tabSalary Component Account` sca
		  ON sca.parent = sd.salary_component AND sca.company = %s
		WHERE ss.payroll_entry = %s AND ss.docstatus = 1
		  AND sd.parentfield = 'deductions'
		  AND sc.custom_is_employer_contribution = 1
		GROUP BY ss.employee, ss.salary_structure, sd.salary_component, sca.account,
		         sca.custom_employer_expense_account
		""",
		(pe.company, pe.name), as_dict=True,
	)
	for row in rows:
		_split(credits, row.account, pe, row.employee, row.salary_structure, row.total)
		_split(debits, row.expense_account, pe, row.employee, row.salary_structure, row.total)


def _add_employee_deductions(pe, credits, skip=None):
	skip = skip or set()
	rows = frappe.db.sql(
		"""
		SELECT ss.employee, ss.salary_structure, sd.salary_component, sca.account,
		       SUM(sd.amount) AS total
		FROM `tabSalary Slip` ss
		JOIN `tabSalary Detail` sd ON sd.parent = ss.name
		JOIN `tabSalary Component` sc ON sc.name = sd.salary_component
		JOIN `tabSalary Component Account` sca
		  ON sca.parent = sd.salary_component AND sca.company = %s
		WHERE ss.payroll_entry = %s AND ss.docstatus = 1
		  AND sd.parentfield = 'deductions'
		  AND IFNULL(sc.custom_is_employer_contribution, 0) = 0
		  AND IFNULL(sc.do_not_include_in_total, 0) = 0
		GROUP BY ss.employee, ss.salary_structure, sd.salary_component, sca.account
		""",
		(pe.company, pe.name), as_dict=True,
	)
	for row in rows:
		if row.salary_component in skip:
			continue
		_split(credits, row.account, pe, row.employee, row.salary_structure, row.total)


def _add_loan_repayments(pe, credits):
	if not frappe.db.has_column("Salary Slip Loan", "loan_account"):
		return
	# total_payment is what the slip actually collected once the two thirds cap
	# has had its say, so it is what the loan account is credited with.
	amount_field = "total_payment"
	rows = frappe.db.sql(
		f"""
		SELECT ss.employee, ss.salary_structure, sl.loan_account,
		       SUM(sl.{amount_field}) AS total
		FROM `tabSalary Slip` ss
		JOIN `tabSalary Slip Loan` sl ON sl.parent = ss.name
		WHERE ss.payroll_entry = %s AND ss.docstatus = 1
		  AND IFNULL(sl.loan_account, '') != '' AND sl.{amount_field} > 0
		GROUP BY ss.employee, ss.salary_structure, sl.loan_account
		""",
		(pe.name,), as_dict=True,
	)
	for row in rows:
		_split(credits, row.loan_account, pe, row.employee, row.salary_structure, row.total)


# ----------------------------------------------------------------------
# Write it back
# ----------------------------------------------------------------------

def _collapse(bucket, pe, settings):
	"""Reduce the split to what the company actually wants on its journal.

	A cost centre answers "where was this cost incurred", which is a question
	about the profit and loss. A liability is not incurred anywhere - PAYE is one
	debt to KRA however many greenhouses the people worked in - and splitting it
	leaves the remittance to be added back up from five lines. ERPNext takes the
	same view: it insists on a cost centre for a profit and loss account and asks
	for none on a balance sheet one.

	  Profit and Loss Only  expense and income lines carry the employee's cost
	                        centre; everything else goes to the run's
	  Every Line            the split reaches the liabilities too
	  Do Not Split          one cost centre throughout, as it was before
	"""
	mode = (settings.get("payroll_journal_cost_centre") or "Profit and Loss Only")
	if mode == "Every Line":
		return bucket

	fallback = pe.get("custom_cost_center") or pe.get("cost_center")
	out = {}
	for key in bucket:
		account = key[0]
		centre = key[1]
		if mode == "Do Not Split":
			centre = fallback
		else:
			root = frappe.get_cached_value("Account", account, "root_type")
			if root not in ("Income", "Expense"):
				centre = fallback
		new_key = (account, centre)
		out[new_key] = flt(out.get(new_key, 0.0) + bucket[key], 2)
	return out


def _apply(doc, pe, settings, debits, credits):
	total_debit = flt(sum(debits.values()), 2)
	total_credit = flt(sum(credits.values()), 2)
	difference = flt(abs(total_debit - total_credit), 2)

	# Net pay is the real figure from the slips, not a plug derived from the
	# other side, so this check can actually fail - which is the point. A
	# component missing its account mapping shows up here instead of being
	# quietly absorbed into Payroll Payable.
	if difference > 0.01:
		frappe.throw(
			_("Payroll Journal Entry for {0} does not balance.<br>"
			  "Debit: {1}<br>Credit: {2}<br>Difference: {3}<br><br>"
			  "This usually means a Salary Component used in this payroll has no "
			  "Account set for {4}.").format(
				pe.name, frappe.format_value(total_debit, {"fieldtype": "Currency"}),
				frappe.format_value(total_credit, {"fieldtype": "Currency"}),
				frappe.format_value(difference, {"fieldtype": "Currency"}), pe.company)
		)

	debits = _collapse(debits, pe, settings)
	credits = _collapse(credits, pe, settings)
	doc.set("accounts", [])

	# One row per account AND cost centre. Where a company puts everybody on one
	# cost centre this comes out exactly as it did before; where the employees
	# carry their own, the cost lands on theirs instead of the run's.
	for key in sorted(debits, key=lambda k: (k[0], k[1] or "")):
		doc.append("accounts", {
			"account": key[0], "cost_center": key[1],
			"debit_in_account_currency": flt(debits[key], 2),
			"credit_in_account_currency": 0,
		})

	for key in sorted(credits, key=lambda k: (k[0], k[1] or "")):
		account = key[0]
		row = {
			"account": account, "cost_center": key[1],
			"debit_in_account_currency": 0,
			"credit_in_account_currency": flt(credits[key], 2),
		}
		if account == pe.payroll_payable_account:
			row["reference_type"] = "Payroll Entry"
			row["reference_name"] = pe.name
		if frappe.db.get_value("Account", account, "account_type") in ("Payable", "Receivable"):
			row["party_not_required"] = 1
		doc.append("accounts", row)

	doc.user_remark = f"Consolidated Payroll JV - {pe.name}"
	doc.title = pe.name
	doc.party_not_required = 1
