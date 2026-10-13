# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

"""Does what the payroll said it would do agree with what the ledger says it did.

One payroll run produces two records of the same event: the payslips, and the
journal raised from them. Nothing until now compared the two. A salary component
with no account for the company, an account remapped between runs, or a run only
half submitted all leave the payslips correct and the ledger wrong, and neither
side complains - the journal still balances, because it balances against itself.

So the comparison is made here, per run:

  Gross pay          the payslips' gross            vs the debits raised
  Net pay            the payslips' net              vs Payroll Payable credited
  Each deduction     what the payslips deducted     vs that account credited
  Each loan          what the payslips recovered    vs the loan accounts credited
  Employer costs     what the company owes on top   vs the debits raised

The expense side is compared in total rather than per account on purpose: a
company posting Per Employee or to a Single Account spreads the same gross over
different accounts by design, so a per-account expectation would report a
difference where there is none. Gross is gross whichever account it lands in.

Deductions are compared per account, because that is where a mapping error
actually hides - one component pointing at the wrong liability still totals
correctly in gross, and is only visible against the account it credited.
"""

import frappe
from frappe import _
from frappe.utils import flt

TOLERANCE = 0.01


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.payroll_entry:
		frappe.throw(_("Please select a Payroll Entry."))

	pe = frappe.get_doc("Payroll Entry", filters.payroll_entry)
	slips = _slips(pe)
	if not slips:
		return _columns(), [], _no_slips_message(pe)

	journal = _journal_lines(pe)
	rows = _rows(pe, slips, journal)

	if filters.only_differences:
		rows = [r for r in rows if r.get("is_section") or abs(flt(r.get("difference"))) >= TOLERANCE]

	return _columns(), rows, _summary(rows, journal)


# ----------------------------------------------------------------------
# The two sides
# ----------------------------------------------------------------------

def _slips(pe):
	"""The submitted payslips of this run. Drafts have not reached the ledger.

	total_loan_repayment is hrms's own field, created only once the `lending`
	app has actually been installed (hrms.setup.after_app_install) - a
	payroll-only site without it has no such column to select at all.
	"""
	fields = ["name", "employee", "gross_pay", "net_pay", "total_deduction"]
	if frappe.db.has_column("Salary Slip", "total_loan_repayment"):
		fields.append("total_loan_repayment")
	return frappe.get_all(
		"Salary Slip",
		filters={"payroll_entry": pe.name, "docstatus": 1},
		fields=fields,
	)


def _journal_lines(pe):
	"""Every posted line of every journal raised for this run.

	The run's name is stamped on the Payroll Payable row and on nothing else, so
	asking for the referencing lines returns the payable row alone - the expense
	and liability lines, which are the ones worth reconciling, carry no
	reference at all. The entries are identified by that one row and then read
	in full.

	Submitted entries only: a cancelled journal is not in the ledger, and
	counting it would report an agreement the trial balance does not show.
	"""
	entries = frappe.db.sql_list(
		"""
		SELECT DISTINCT jea.parent
		FROM `tabJournal Entry Account` jea
		JOIN `tabJournal Entry` je ON je.name = jea.parent
		WHERE je.docstatus = 1
			  AND jea.reference_type = 'Payroll Entry' AND jea.reference_name = %s
		""",
		pe.name,
	)
	if not entries:
		return {}

	rows = frappe.db.sql(
		"""
		SELECT jea.account,
			   SUM(jea.debit_in_account_currency)  AS debit,
			   SUM(jea.credit_in_account_currency) AS credit
		FROM `tabJournal Entry Account` jea
		WHERE jea.parent IN %(entries)s
		GROUP BY jea.account
		""",
		{"entries": entries}, as_dict=True,
	)
	return {r.account: r for r in rows}


# ----------------------------------------------------------------------
# What the payslips say
# ----------------------------------------------------------------------

def _component_totals(slip_names, parentfield):
	"""What the payslips booked against each salary component."""
	if not slip_names:
		return []
	return frappe.db.sql(
		"""
		SELECT sd.salary_component, SUM(sd.amount) AS amount,
			   MAX(sd.do_not_include_in_total) AS excluded
		FROM `tabSalary Detail` sd
		WHERE sd.parent IN %(slips)s AND sd.parenttype = 'Salary Slip'
			  AND sd.parentfield = %(field)s
			  AND IFNULL(sd.statistical_component, 0) = 0
		GROUP BY sd.salary_component
		ORDER BY sd.salary_component
		""",
		{"slips": slip_names, "field": parentfield}, as_dict=True,
	)


def _loan_totals(slip_names):
	"""What the payslips recovered against each loan product."""
	if not slip_names:
		return []
	return frappe.db.sql(
		"""
		SELECT sl.loan_product, SUM(sl.total_payment) AS amount
		FROM `tabSalary Slip Loan` sl
		WHERE sl.parent IN %(slips)s AND sl.parenttype = 'Salary Slip'
		GROUP BY sl.loan_product
		ORDER BY sl.loan_product
		""",
		{"slips": slip_names}, as_dict=True,
	)


# ----------------------------------------------------------------------
# Comparison
# ----------------------------------------------------------------------

def _rows(pe, slips, journal):
	from upande_payroll.liability_remittance import component_account

	names = [s.name for s in slips]
	rows = []

	def line(label, account, expected, actual, note=""):
		return {
			"label": label, "account": account,
			"per_payslips": flt(expected, 2), "per_ledger": flt(actual, 2),
			"difference": flt(flt(expected) - flt(actual), 2), "note": note,
		}

	def section(title):
		return {"label": title, "is_section": 1}

	# ---- gross pay against the debits raised ---------------------------
	gross = sum(flt(s.gross_pay) for s in slips)
	debits = sum(flt(r.debit) for r in journal.values())
	credits = sum(flt(r.credit) for r in journal.values())

	rows.append(section(_("Earnings")))
	earnings = _component_totals(names, "earnings")
	counted = sum(flt(e.amount) for e in earnings if not e.excluded)
	rows.append(line(_("Gross pay on the payslips"), "", gross, counted,
					 _("the slips' own gross against the sum of their earning rows")))

	# ---- liabilities, grouped by the account they credit ---------------
	# Per account, not per component. Several components share one liability -
	# both NSSF tiers credit NSSF Payable, and the employer's own contribution
	# credits the same account as the employee's - so a per-component figure
	# compared against an account balance reports a difference every time
	# without one existing. What the ledger holds is an account total, so an
	# account total is what has to be compared with it.
	rows.append(section(_("Liabilities credited")))
	by_account = {}
	unmapped = []
	for row in _component_totals(names, "deductions"):
		account = component_account(pe.company, row.salary_component)
		if not account:
			if not row.excluded:
				unmapped.append(row)
			continue
		entry = by_account.setdefault(account, {"amount": 0.0, "components": []})
		entry["amount"] += flt(row.amount)
		entry["components"].append(row.salary_component)

	for account in sorted(by_account):
		entry = by_account[account]
		posted = flt(journal.get(account, {}).get("credit"))
		feeds = ", ".join(sorted(entry["components"]))
		rows.append(line(
			account.split(" - ")[0], account, entry["amount"], posted,
			feeds if len(entry["components"]) > 1 else "",
		))

	for row in unmapped:
		# A component the employee really lost, posting nowhere. It reduced net
		# pay and left no liability behind, so the money is owed and unrecorded.
		rows.append(line(row.salary_component, "", row.amount, 0.0,
						 _("no account mapped for this company - nothing was credited")))

	# ---- loans ---------------------------------------------------------
	loans = _loan_totals(names)
	if loans:
		rows.append(section(_("Loan recovery")))
		recovered = sum(flt(l.amount) for l in loans)
		slip_loans = sum(flt(s.total_loan_repayment) for s in slips)
		rows.append(line(_("Recovered on the payslips"), "", slip_loans, recovered,
						 _("slip totals against the loan rows behind them")))

	# ---- net pay against payroll payable -------------------------------
	rows.append(section(_("Net pay")))
	net = sum(flt(s.net_pay) for s in slips)
	payable = flt(journal.get(pe.payroll_payable_account, {}).get("credit"))
	rows.append(line(_("Net pay"), pe.payroll_payable_account or "", net, payable,
					 _("what the run owes the staff against what was credited to payable")))

	# ---- the journal's own integrity ------------------------------------
	rows.append(section(_("Journal")))
	rows.append(line(_("Debits against credits"), "", debits, credits,
					 _("a payroll journal that does not balance has not posted what it claims")))

	return rows


def _summary(rows, journal):
	off = [r for r in rows if not r.get("is_section")
		   and abs(flt(r.get("difference"))) >= TOLERANCE]
	if not journal:
		return [{"value": _("No posted journal found for this run"),
				 "indicator": "Red", "label": _("Ledger"), "datatype": "Data"}]
	if not off:
		return [{"value": _("Payroll agrees with the ledger"),
				 "indicator": "Green", "label": _("Reconciliation"), "datatype": "Data"}]
	return [{"value": _("{0} line(s) do not agree").format(len(off)),
			 "indicator": "Red", "label": _("Reconciliation"), "datatype": "Data"}]


def _no_slips_message(pe):
	return [{"value": _("No submitted payslips on this run"), "indicator": "Orange",
			 "label": _("Reconciliation"), "datatype": "Data"}]


def _columns():
	return [
		{"label": _("Line"), "fieldname": "label", "fieldtype": "Data", "width": 260},
		{"label": _("Account"), "fieldname": "account", "fieldtype": "Link",
		 "options": "Account", "width": 240},
		{"label": _("Per Payslips"), "fieldname": "per_payslips", "fieldtype": "Currency", "width": 140},
		{"label": _("Per Ledger"), "fieldname": "per_ledger", "fieldtype": "Currency", "width": 140},
		{"label": _("Difference"), "fieldname": "difference", "fieldtype": "Currency", "width": 130},
		{"label": _("Note"), "fieldname": "note", "fieldtype": "Data", "width": 320},
	]
