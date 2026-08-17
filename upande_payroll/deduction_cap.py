import frappe
from frappe import _
from frappe.utils import flt, getdate

from upande_payroll.kenya_statutory_gross_pay import get_income_breakdown
from upande_payroll.upande_payroll.doctype.deferred_deduction.deferred_deduction import (
	get_outstanding,
)

# Employment Act 2007 s.19(3): total deductions may not exceed two thirds of
# wages. The fraction is in the statute, not a company policy choice, so it is
# a constant here rather than a field - Company Payroll Settings only decides
# whether the company complies, and Deduction Priority only what gives way.
PERMITTED_FRACTION = 2.0 / 3.0

# Currency comparisons at 2dp; anything under this is rounding noise, not a breach.
TOLERANCE = 0.01


# ======================================================================
# Salary Slip: validate
# ======================================================================

def apply_deduction_cap(doc, method=None):
	"""Hold total deductions to two thirds of wages (Employment Act s.19(3)).

	Runs on Salary Slip ``validate``, after the controller has built both
	component tables and set the totals. It cannot live in
	kenya_statutory_calculator.apply_regional_deductions: that hook fires part
	way through calculate_net_pay, before loans and before any voluntary
	deduction is known, and the cap has to see every deduction at once.

	Two things happen here, in this order:

	  1. Outstanding Deferred Deductions are added back onto their component, so
	     a debt is recovered as soon as a period has room for it.
	  2. If the total then breaches the cap, deductions are reduced - lowest
	     priority first - until it fits.

	Nothing is written to the ledger here. This runs on every save, including
	drafts that may never be submitted, so the debts themselves are created and
	settled on submit. All this pass leaves behind is the intent, in the slip's
	own two tables.
	"""
	settings = frappe.get_cached_doc("Company Payroll Settings", doc.company)
	if not settings.enable_one_third_rule:
		_clear(doc)
		return

	config = _config(doc.company)
	wage_base = _wage_base(doc, settings, config)
	permitted = flt(wage_base * PERMITTED_FRACTION, 2)

	rules = _priority_rules(config)
	rows = [d for d in (doc.deductions or []) if not d.do_not_include_in_total]

	# Debts are brought forward even on a final payslip - especially then, since
	# it is the last chance to collect them.
	plan = _bring_forward(doc, rows, rules)

	# A leaver's last payslip takes everything. Capping it would protect nobody:
	# there is no next payslip to collect the rest from, so whatever is left
	# uncollected is simply lost.
	final_slip = _is_final_slip(doc)
	excess = 0.0

	if not final_slip:
		# Loan repayments live in their own child table with their own total, not
		# in `deductions`. They are still a deduction from wages for s.19 purposes
		# - s.19(1)(h) caps employer loans specifically - so they count towards
		# the total. They are not reduced here: cutting a repayment without
		# rewriting the Loan Repayment schedule would leave the loan
		# mis-amortised, so for now an oversized loan surfaces as a breach rather
		# than being silently trimmed.
		loans = flt(doc.get("total_loan_repayment"))
		total = flt(sum(flt(d.amount) for d in rows) + loans, 2)

		excess = flt(total - permitted, 2)
		if excess > TOLERANCE:
			excess = _reduce(rows, rules, excess)

	# Nothing was cut on a final slip, so _allocate finds every claim fully paid
	# and raises no new debt - it only settles the ones brought forward.
	_allocate(doc, rows, plan)
	doc.custom_one_third_rule_skipped = 1 if final_slip else 0
	doc.flags.one_third_unconfigured = not rules

	doc.custom_wage_base_for_deduction_cap = wage_base
	doc.custom_maximum_permitted_deduction = permitted
	doc.custom_unreducible_excess = max(flt(excess, 2), 0.0)
	doc.custom_deduction_cap_applied = 1 if doc.custom_deferred_deductions else 0

	doc.set_net_pay()
	_notify(doc, permitted)


# ======================================================================
# Configuration
# ======================================================================

def _config(company):
	"""The company's Deduction Priority record, or None.

	Its own doctype rather than another table inside Company Payroll Settings -
	the priority list is edited on its own cycle by whoever owns the deduction
	policy, and it grows with every new deduction the company introduces.
	"""
	if not frappe.db.exists("Deduction Priority", company):
		return None
	return frappe.get_cached_doc("Deduction Priority", company)


def _is_final_slip(doc):
	"""Is this the payslip an employee leaves on, or one issued after?

	Read from the Employee rather than the slip: a relieving date on or before
	this period's end means there is no later payroll to recover anything from.
	Status is checked too, for a leaver whose relieving date was never filled in.
	"""
	emp = frappe.db.get_value(
		"Employee", doc.employee, ["status", "relieving_date"], as_dict=True
	) or frappe._dict()

	# Where a relieving date exists it decides on its own, and status is not
	# consulted at all. Status alone would mark every period final once someone
	# is flagged Left - including an earlier month run late, which the employee
	# worked in full and which is followed by a payslip that can still recover a
	# shortfall. Only the period the leaving date falls in, or a later one, is
	# genuinely the last.
	if emp.relieving_date:
		return getdate(emp.relieving_date) <= getdate(doc.end_date)

	return emp.status == "Left"


def _priority_rules(config):
	"""{component: rule} for every component the company has classified.

	Priority and shortfall behaviour come from the component's Deduction Group,
	not from the component itself: a company orders four tiers instead of
	numbering twenty rows, and moving a deduction between tiers is one field.

	A component in a group with ``reducible`` cleared still consumes the budget
	- which is what pushes lower tiers out first - but is never trimmed itself.

	No record, or an empty one, means nothing may be reduced. The cap then still
	reports a breach but never silently cuts a deduction the company has not
	agreed can give way.
	"""
	if not config:
		return {}

	groups, rules = {}, {}
	for row in (config.deductions or []):
		if not (row.salary_component and row.deduction_group):
			continue
		if row.deduction_group not in groups:
			groups[row.deduction_group] = frappe.get_cached_doc(
				"Deduction Group", row.deduction_group
			)
		group = groups[row.deduction_group]
		# Rank comes from the group, and only from the group. A row used to be
		# able to override it, which meant two deductions sitting in the same
		# group could behave differently with nothing on the form to say why.
		# Anything that needs its own rank needs its own group.
		rules[row.salary_component] = frappe._dict({
			"group": group.name,
			"priority": flt(group.priority),
			"reducible": bool(group.reducible),
			"on_shortfall": group.on_shortfall or "Carry Forward",
			"tie_breaker": group.tie_breaker or "Pro-rata",
			"order": row.idx,
		})
	return rules


def _wage_base(doc, settings, config):
	"""The figure the two thirds is measured against.

	Companies do not agree on this, so it is theirs to choose:

	  Cash Wages       cash earnings less absence. Employment Act s.2 defines
	                   wages as remuneration payable in money, so a car or
	                   housing benefit is excluded even though it is taxed. This
	                   also settles absence - it reduces the base rather than
	                   counting as a deduction inside the cap, which is what
	                   s.19(1)(c) is for.
	  Gross Pay        ERPNext's gross pay, non-cash benefits included. The most
	                   generous base, so the least protective of the employee.
	  Selected Earnings only the listed components - basic and the constant
	                   allowances, for companies that exclude overtime and
	                   one-off payments from the calculation.
	"""
	method = (config.wage_base if config else None) or "Cash Wages"

	if method == "Gross Pay":
		return flt(doc.gross_pay, 2)

	if method == "Selected Earnings":
		wanted = {r.salary_component for r in (config.base_components or [])}
		return flt(sum(
			flt(e.amount) for e in (doc.earnings or [])
			if e.salary_component in wanted
		), 2)

	is_secondary = bool(
		frappe.db.get_value("Employee", doc.employee, "custom_is_secondary_employment")
	)
	breakdown = get_income_breakdown(doc, settings, is_secondary=is_secondary)
	return flt(breakdown.statutory_cash_base, 2)


# ======================================================================
# Recovery and reduction
# ======================================================================

def _bring_forward(doc, rows, rules):
	"""Add outstanding debts onto their component so this period can pay them.

	Only components that already have a row on this slip are topped up. A debt
	on a component the employee no longer has - a SACCO they left, a deduction
	dropped from the structure - is not resurrected onto a slip that has no
	line for it; it stays open in the ledger until one does.

	Returns a plan per component holding this period's own instalment separately
	from the debts, which _allocate needs once the cap has decided how much can
	actually be collected.
	"""
	by_component = {}
	for debt in get_outstanding(doc.employee, doc.company, before_slip=doc.name):
		by_component.setdefault(debt.salary_component, []).append(debt)

	plan = {}
	for row in rows:
		component = row.salary_component
		debts = by_component.get(component, []) if component in rules else []
		brought_forward = flt(sum(flt(d.balance_remaining) for d in debts), 2)

		own_amount = flt(row.amount, 2)
		if brought_forward:
			row.amount = flt(own_amount + brought_forward, 2)

		plan[component] = frappe._dict({
			"salary_component": component,
			"own_amount": own_amount,
			"debts": debts,
			"treatment": rules[component].on_shortfall if component in rules else None,
		})

	return plan


def _allocate(doc, rows, plan):
	"""Split what was actually collected between old debts and this period's own.

	Oldest debt first. This is the difference between a ledger that tells the
	truth and one that doesn't: if the cap cut a component below what it owed,
	only the money genuinely collected may be credited against a debt. Whatever
	is left of a debt stays outstanding at its original age rather than being
	rolled into a new one.
	"""
	doc.set("custom_brought_forward_deductions", [])
	doc.set("custom_deferred_deductions", [])

	collected = {r.salary_component: flt(r.amount, 2) for r in rows}

	for component, entry in plan.items():
		remaining = collected.get(component, 0.0)

		for debt in entry.debts:
			applied = min(remaining, flt(debt.balance_remaining, 2))
			if applied <= 0:
				continue
			doc.append("custom_brought_forward_deductions", {
				"deferred_deduction": debt.name,
				"salary_component": component,
				"amount": flt(applied, 2),
				"deferred_from": debt.deferred_from,
				"deferral_date": debt.deferral_date,
			})
			remaining = flt(remaining - applied, 2)

		own_applied = flt(remaining, 2)
		own_deferred = flt(entry.own_amount - own_applied, 2)
		if own_deferred <= TOLERANCE:
			continue

		doc.append("custom_deferred_deductions", {
			"salary_component": component,
			"original_amount": entry.own_amount,
			"applied_amount": own_applied,
			"deferred_amount": own_deferred,
			"treatment": entry.treatment,
		})


def _reduce(rows, rules, excess):
	"""Cut reducible deductions until the total fits, lowest priority first.

	Returns whatever excess could not be absorbed - the amount by which
	protected deductions alone breach the cap.
	"""
	reducible = [
		r for r in rows
		if r.salary_component in rules and rules[r.salary_component].reducible
	]

	tiers = {}
	for row in reducible:
		tiers.setdefault(rules[row.salary_component].priority, []).append(row)

	# The highest priority number is the least important tier, so it gives way
	# first. A tier is only ever partially cut once the ones below it are gone.
	for priority in sorted(tiers, reverse=True):
		if excess <= TOLERANCE:
			break

		members = tiers[priority]
		tier_total = flt(sum(flt(m.amount) for m in members), 2)
		if tier_total <= 0:
			continue

		if excess >= tier_total - TOLERANCE:
			for member in members:
				member.amount = 0.0
			excess = flt(excess - tier_total, 2)
			continue

		excess = _share_the_cut(members, rules, excess, tier_total)

	return excess


def _share_the_cut(members, rules, excess, tier_total):
	"""Split a partial cut between deductions that rank equally.

	Only reached when the tier can absorb the whole excess, so this always
	returns nothing left over - what it decides is who bears it.
	"""
	method = rules[members[0].salary_component].tie_breaker

	if method in ("Largest First", "As Listed"):
		if method == "Largest First":
			ordered = sorted(members, key=lambda m: -flt(m.amount))
		else:
			# First listed is the most protected, so cut from the bottom up.
			ordered = sorted(members, key=lambda m: -rules[m.salary_component].order)

		for member in ordered:
			if excess <= TOLERANCE:
				break
			cut = min(flt(member.amount), excess)
			member.amount = flt(flt(member.amount) - cut, 2)
			excess = flt(excess - cut, 2)
		return excess

	# Pro-rata. Equal-ranking claims take the same percentage, so one creditor
	# is never short-paid while another of the same rank is settled in full.
	# Rounding lands on the largest row, which can absorb a cent without the
	# proportions visibly slipping.
	ordered = sorted(members, key=lambda m: -flt(m.amount))
	cuts = [flt(excess * flt(m.amount) / tier_total, 2) for m in ordered]
	cuts[0] = flt(cuts[0] + flt(excess - sum(cuts), 2), 2)

	for member, cut in zip(ordered, cuts):
		cut = min(max(cut, 0.0), flt(member.amount))
		member.amount = flt(flt(member.amount) - cut, 2)
		excess = flt(excess - cut, 2)

	return max(excess, 0.0)


def _clear(doc):
	doc.custom_wage_base_for_deduction_cap = 0
	doc.custom_maximum_permitted_deduction = 0
	doc.custom_deduction_cap_applied = 0
	doc.custom_unreducible_excess = 0
	doc.custom_one_third_rule_skipped = 0
	doc.set("custom_deferred_deductions", [])
	doc.set("custom_brought_forward_deductions", [])


# ======================================================================
# Salary Slip: submit and cancel
# ======================================================================

def settle_deferred_deductions(doc, method=None):
	"""Turn this slip's intent into ledger movements. Runs on ``on_submit``.

	Recovery is applied before new debts are raised so that a component which
	both cleared an old debt and created a new one leaves the ledger in the
	right order.
	"""
	for row in (doc.custom_brought_forward_deductions or []):
		if not row.deferred_deduction:
			continue
		debt = frappe.get_doc("Deferred Deduction", row.deferred_deduction)
		debt.apply_recovery(doc.name, row.amount, doc.posting_date)

	for row in (doc.custom_deferred_deductions or []):
		if row.treatment != "Carry Forward" or flt(row.deferred_amount) <= 0:
			continue
		if row.deferred_deduction:
			continue

		start = getdate(doc.start_date)
		debt = frappe.get_doc({
			"doctype": "Deferred Deduction",
			"employee": doc.employee,
			"employee_name": doc.employee_name,
			"company": doc.company,
			"salary_component": row.salary_component,
			"deferred_from": doc.name,
			"deferral_date": doc.start_date,
			"deferred_month": f"{start.year}-{start.month:02d}",
			"original_amount": row.original_amount,
			"applied_amount": row.applied_amount,
			"deferred_amount": row.deferred_amount,
			"balance_remaining": row.deferred_amount,
			"status": "Pending",
		})
		debt.insert(ignore_permissions=True)
		debt.submit()
		row.db_set("deferred_deduction", debt.name)


def unsettle_deferred_deductions(doc, method=None):
	"""Undo everything this slip did to the ledger. Runs on ``on_cancel``.

	This is the reason the debts are documents rather than a running balance on
	the slip: cancelling a slip puts recovered balances back and cancels the
	debts it raised, instead of stranding them.
	"""
	for name in frappe.get_all(
		"Deferred Deduction",
		filters={"deferred_from": doc.name, "docstatus": 1},
		pluck="name",
	):
		frappe.get_doc("Deferred Deduction", name).cancel()

	for row in (doc.custom_brought_forward_deductions or []):
		if not row.deferred_deduction:
			continue
		if not frappe.db.exists("Deferred Deduction", row.deferred_deduction):
			continue
		debt = frappe.get_doc("Deferred Deduction", row.deferred_deduction)
		if debt.docstatus == 1:
			debt.reverse_recovery(doc.name)


# ======================================================================

def _notify(doc, permitted):
	if doc.custom_one_third_rule_skipped:
		message = _(
			"{0} is leaving, so deductions were taken in full - there is no later "
			"payslip to recover a shortfall from."
		).format(doc.employee_name)
		indicator = "blue"
		if flt(doc.net_pay) < 0:
			message += " " + _(
				"Net pay is {0}. What is owed exceeds the final dues, so the balance "
				"has to be recovered outside payroll."
			).format(frappe.format_value(flt(doc.net_pay), {"fieldtype": "Currency"}))
			indicator = "red"
		frappe.msgprint(message, title=_("1/3 Rule Not Applied"), indicator=indicator)
		return

	if flt(doc.custom_unreducible_excess) > TOLERANCE:
		# Being over the limit with nothing declared reducible is the likeliest
		# way this goes wrong in a new company: the rule is on, so it looks as
		# though pay is protected, but there is nothing it is allowed to cut.
		# Say that plainly rather than reporting a breach with no cause.
		if doc.flags.get("one_third_unconfigured"):
			frappe.msgprint(
				_(
					"Deductions for {0} are {1} over the limit of {2}, but nothing "
					"was reduced: no Deduction Priority has been set up for {3}, so "
					"the rule has no deduction it is allowed to cut. Set one up, or "
					"turn off Comply with 1/3 Rule."
				).format(
					doc.employee_name,
					frappe.format_value(flt(doc.custom_unreducible_excess),
										{"fieldtype": "Currency"}),
					frappe.format_value(flt(permitted), {"fieldtype": "Currency"}),
					doc.company,
				),
				title=_("1/3 Rule Not Configured"),
				indicator="red",
			)
			return

		frappe.msgprint(
			_(
				"Deductions for {0} exceed two thirds of wages by {1} and cannot be "
				"reduced further. Protected deductions alone are above the limit of "
				"{2}, so this slip does not comply with Employment Act s.19(3)."
			).format(
				doc.employee_name,
				frappe.format_value(flt(doc.custom_unreducible_excess), {"fieldtype": "Currency"}),
				frappe.format_value(flt(permitted), {"fieldtype": "Currency"}),
			),
			title=_("1/3 Rule Breach"),
			indicator="red",
		)
		return

	if doc.custom_deduction_cap_applied:
		frappe.msgprint(
			_("Deductions for {0} were capped at {1} to keep net pay at one third of wages.").format(
				doc.employee_name,
				frappe.format_value(flt(permitted), {"fieldtype": "Currency"}),
			),
			title=_("1/3 Rule Applied"),
			indicator="orange",
		)
