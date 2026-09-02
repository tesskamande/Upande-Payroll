"""Draft Bank Entries for the liabilities a payroll run leaves behind.

The accrual journal credits every liability the run created - PAYE, NSSF, SHIF,
the Housing Levy, NITA, sacco and union deductions - and then nothing moves
them. Somebody has to raise a payment for each body, reading the figures off the
journal by hand.

This raises those payments as drafts the moment the accrual is submitted: one
Bank Entry per liability account, debiting what the journal credited and
crediting the bank it is paid from. Nothing is paid - a draft has no ledger
effect - so the figures can still be checked, and one nobody is ready to pay can
be left sitting or deleted.

The only thing it has to be told is which bank each liability leaves from -
PAYE out of KCB, NSSF out of Absa, whatever the company actually does - with a
default for the ones that all come from the same place. Everything else is
already recorded: the salary component carries the account, and the journal
carries the amount. What separates a liability that gets paid out from one that
does not is the chart itself - an account under Liability is owed to somebody,
one under Asset or Expense is not - so nothing has to be mapped by hand.
"""

import frappe
from frappe import _
from frappe.database import savepoint
from frappe.utils import flt, get_link_to_form

from upande_payroll.payroll_journal import _find_payroll_entry

# Stamped on every remittance so it can be told apart from the run's own
# payment entries.
#
# These entries DO carry the standard reference_type/reference_name row as well,
# so the Payroll Entry's Connections tab lists them like any other linked
# journal. That reference is also what has_bank_entries() (hrms
# payroll_entry.py:893) reads to decide the salaries have been paid, and it
# would hide Make Bank Entry the moment KRA was remitted - so
# PayrollEntryMixin.has_bank_entries() discounts anything carrying SOURCE_FIELD.
# The link and the button check are settled in one place rather than by leaving
# the entries unlinked.
SOURCE_FIELD = "custom_source_payroll_entry"


def create_remittance_entries(doc, method=None):
	"""Hooked on Journal Entry ``on_submit``.

	Runs off the journal that was actually posted rather than recomputing from
	the payslips, so a remittance can never disagree with the ledger it clears:
	whatever the accrual credited is what gets debited back.
	"""
	pe = _accrual_payroll_entry(doc)
	if not pe:
		return

	settings = frappe.get_cached_doc("Company Payroll Settings", pe.company)
	if not settings.get("enable_liability_remittance"):
		return

	default_bank = settings.get("liability_remittance_payment_account")
	never_remit, banks = _configured_banks(settings)

	created, homeless = [], []
	for account, amount in _remittable(doc, pe, never_remit):
		bank = banks.get(account) or default_bank
		if not bank:
			# No bank named for it and no default to fall back on. Reported
			# rather than paid out of whichever account came to hand.
			homeless.append((account, amount))
			continue
		if _existing_entry(pe.name, account):
			continue
		entry = _make_entry(doc, pe, account, amount, bank)
		if entry:
			created.append((account, entry))

	if homeless:
		_warn_no_bank(homeless, pe)
	if created:
		_announce(created, pe)


def cancel_remittance_entries(doc, method=None):
	"""Hooked on Journal Entry ``on_cancel``.

	A cancelled accrual has no liabilities left to remit, so its unsubmitted
	drafts are deleted rather than left behind for someone to post against a
	journal that no longer exists. Anything already submitted is real ledger
	movement and is reported instead of touched.
	"""
	pe = _accrual_payroll_entry(doc)
	if not pe:
		return

	entries = frappe.get_all(
		"Journal Entry",
		filters={SOURCE_FIELD: pe.name, "docstatus": ("<", 2)},
		fields=["name", "docstatus"],
	)
	if not entries:
		return

	dropped, posted, stuck = [], [], []
	for row in entries:
		if row.docstatus != 0:
			posted.append(row.name)
			continue
		# Each delete gets its own savepoint. A draft can refuse to go - stale
		# GL rows left behind by an earlier voucher of the same name, a link
		# added by another app - and this runs inside the accrual's on_cancel,
		# so letting that propagate would stop the payroll journal being
		# cancelled at all. A draft left behind moves no money; a payroll
		# journal that cannot be cancelled is a real problem, so the draft loses.
		# The savepoint keeps the failed delete from poisoning the surrounding
		# transaction, which a bare try/except would not.
		with savepoint():
			try:
				frappe.delete_doc("Journal Entry", row.name, ignore_permissions=True)
				dropped.append(row.name)
			except Exception:
				stuck.append(row.name)
				raise

	if stuck:
		frappe.msgprint(
			_("These remittance drafts could not be deleted automatically. They are "
			  "unposted, so nothing has moved, but delete them by hand:<br>{0}")
			.format("<br>".join(get_link_to_form("Journal Entry", n) for n in stuck)),
			title=_("Drafts Left Behind"), indicator="orange",
		)

	if dropped:
		frappe.msgprint(
			_("Deleted {0} unposted liability remittance draft(s) raised from this journal.")
			.format(len(dropped)),
			indicator="orange", alert=True,
		)
	if posted:
		frappe.msgprint(
			_("These liability remittances were already submitted and have not been "
			  "touched. Cancel them yourself if the money should come back:<br>{0}")
			.format("<br>".join(get_link_to_form("Journal Entry", n) for n in posted)),
			title=_("Submitted Remittances Left Alone"), indicator="red",
		)


# ----------------------------------------------------------------------
# Is this the accrual?
# ----------------------------------------------------------------------

def _accrual_payroll_entry(doc):
	"""The Payroll Entry this journal accrues for, or None.

	Same two tests the accrual rewrite uses (payroll_journal.py:36): the
	voucher type tells an accrual from the payment entries that carry the same
	Payroll Entry reference, and the reference row names the run.
	"""
	if doc.voucher_type != "Journal Entry":
		return None
	if doc.get(SOURCE_FIELD):
		return None

	name = _find_payroll_entry(doc)
	if not name:
		return None
	return frappe.get_doc("Payroll Entry", name)


# ----------------------------------------------------------------------
# What is actually owed to somebody
# ----------------------------------------------------------------------

def _remittable(doc, pe, never_remit):
	"""The credited accounts a payment should be raised for, largest first.

	Three kinds of credit are dropped, none of them by configuration:

	Anything not under Liability in the chart. A deduction can credit an asset
	or an expense - a salary advance instalment clearing Employee Advances, an
	absence reducing Salary Expense - and paying those out would reinstate the
	debt or refund the company's own cost. The root type already says which is
	which, so the company is not asked to.

	Payroll Payable, which is the staff's net pay. HRMS pays that with its own
	Make Bank Entry button; raising a second draft for it would pay the wages
	twice.

	Whatever the company has ticked Never Remit - a liability that is genuinely
	owed but not settled in cash this month.
	"""
	totals = {}
	for row in doc.get("accounts") or []:
		amount = flt(row.credit_in_account_currency, 2)
		if amount <= 0:
			continue
		totals[row.account] = flt(totals.get(row.account, 0.0) + amount, 2)

	totals.pop(pe.payroll_payable_account, None)
	for account in never_remit:
		totals.pop(account, None)

	if not totals:
		return []

	root_types = dict(
		frappe.get_all("Account", filters={"name": ("in", list(totals))},
					   fields=["name", "root_type"], as_list=True)
	)
	remittable = [
		(account, amount) for account, amount in totals.items()
		if root_types.get(account) == "Liability"
	]
	return sorted(remittable, key=lambda row: -row[1])


def _configured_banks(settings):
	"""Which bank settles each liability, and which are not settled at all."""
	never_remit, banks = set(), {}
	for row in settings.get("payroll_remittance_accounts") or []:
		if not row.liability_account:
			continue
		if row.never_remit:
			never_remit.add(row.liability_account)
		elif row.payment_account:
			banks[row.liability_account] = row.payment_account
	return never_remit, banks


def _warn_no_bank(homeless, pe):
	rows = "".join(
		"<tr><td>{0}</td><td style='text-align:right'>{1}</td></tr>".format(
			frappe.utils.escape_html(account),
			frappe.format_value(amount, {"fieldtype": "Currency"}),
		)
		for account, amount in homeless
	)
	frappe.msgprint(
		_("This payroll credited these liabilities, but no bank is named for them and "
		  "there is no default, so nothing has been raised to pay them. Give each one a "
		  "bank in Company Payroll Settings &rarr; Where Each Liability Is Paid From, or "
		  "set a Default Bank Account."
		  "<br><br><table class='table table-bordered'>"
		  "<thead><tr><th>Liability</th><th style='text-align:right'>Owed</th></tr></thead>"
		  "<tbody>{0}</tbody></table>").format(rows),
		title=_("No Bank To Pay These From"), indicator="orange",
	)


def _existing_entry(payroll_entry, account):
	"""An earlier remittance for this run and account, posted or not.

	Stops a resubmit, a repost or a second tab raising the same payment twice.
	A submitted one counts: the money has gone, and a draft alongside it is an
	invitation to pay KRA a second time.

	Which liability an entry settles is read off the entry - it is the account
	being debited - rather than repeated in a field of its own. One fact, in one
	place, and the two can never drift apart.
	"""
	found = frappe.db.sql_list(
		"""
		SELECT je.name
		FROM `tabJournal Entry` je
		INNER JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
		WHERE je.custom_source_payroll_entry = %s
			AND je.docstatus < 2
			AND jea.account = %s
			AND jea.debit_in_account_currency > 0
		LIMIT 1
		""",
		(payroll_entry, account),
	)
	return found[0] if found else None


# ----------------------------------------------------------------------
# Write the draft
# ----------------------------------------------------------------------

def _make_entry(accrual, pe, account, amount, payment_account):
	if not amount:
		return None

	cost_center = pe.get("custom_cost_center") or pe.get("cost_center")
	label = frappe.db.get_value("Account", account, "account_name") or account

	entry = frappe.new_doc("Journal Entry")
	entry.voucher_type = "Bank Entry"
	entry.company = pe.company
	entry.posting_date = accrual.posting_date
	entry.title = f"{label} - {pe.name}"
	entry.user_remark = _("Settles {0} accrued by {1}. Raised from {2}.").format(
		account, pe.name, accrual.name)
	entry.set(SOURCE_FIELD, pe.name)

	entry.append("accounts", {
		"account": account,
		"cost_center": cost_center,
		"debit_in_account_currency": flt(amount, 2),
		"credit_in_account_currency": 0,
		# What puts this entry on the Payroll Entry's Connections tab. Core
		# tags its own payment rows the same way (payroll_entry.py,
		# set_accounting_entries_for_bank_entry).
		"reference_type": "Payroll Entry",
		"reference_name": pe.name,
	})
	entry.append("accounts", {
		"account": payment_account,
		"cost_center": cost_center,
		"debit_in_account_currency": 0,
		"credit_in_account_currency": flt(amount, 2),
	})

	# Set on the PARENT, which is what validate_party actually reads
	# (erpnext journal_entry.py:603 tests self.party_not_required, not the row's
	# own flag). A statutory account typed Payable is carried through the whole
	# run without a party, so clearing it here would otherwise be refused for
	# having none - and because this runs inside the accrual's on_submit, that
	# refusal came back as the payroll journal failing to post at all. The
	# accrual sets the same flag for the same reason (payroll_journal.py:296).
	entry.party_not_required = 1

	# Left as a draft on purpose. Submitting here would pay every statutory body
	# the instant payroll was journalled, with nobody having seen the figures.
	entry.insert()
	return entry


def _announce(created, pe):
	lines = "".join(
		"<tr><td>{0}</td><td>{1}</td><td style='text-align:right'>{2}</td></tr>".format(
			frappe.utils.escape_html(account),
			get_link_to_form("Journal Entry", entry.name),
			frappe.format_value(flt(entry.total_credit, 2), {"fieldtype": "Currency"}),
		)
		for account, entry in created
	)
	frappe.msgprint(
		_("Draft bank entries raised for this run's liabilities. Nothing has been paid "
		  "until each one is submitted, and each still needs its Reference No and Date."
		  "<br><br><table class='table table-bordered'>"
		  "<thead><tr><th>Liability</th><th>Entry</th>"
		  "<th style='text-align:right'>Amount</th></tr></thead>"
		  "<tbody>{0}</tbody></table>").format(lines),
		title=_("Liability Remittances Raised"),
		indicator="green",
	)

# ----------------------------------------------------------------------
# Picking a row by component
# ----------------------------------------------------------------------

@frappe.whitelist()
def component_account(company, salary_component):
	"""The account a component posts to for this company.

	Each component carries one account per company (Salary Component Account),
	which is what the accrual credits. Looked up rather than typed, so the table
	cannot name an account the component does not actually post to.
	"""
	if not (company and salary_component):
		return None
	return frappe.db.get_value(
		"Salary Component Account",
		{"parent": salary_component, "company": company},
		"account",
	)
