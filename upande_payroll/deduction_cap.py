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

	# Built for every slip, final or not: a leaver's last payslip is the last
	# chance to recover an old debt, so it takes the whole of it.
	floor = max([flt(rule.priority) for rule in rules.values()] or [0.0])
	loan_rows = _loan_rows(doc, rules)
	arrears_rows = _arrears_rows(plan, rules, floor)

	if not final_slip:
		# Loan repayments live in their own child table with their own total, not
		# in `deductions`. They are still a deduction from wages for s.19 purposes
		# - s.19(1)(h) caps employer loans specifically - so they count towards
		# the total. They are not reduced here: cutting a repayment without
		# rewriting the Loan Repayment schedule would leave the loan
		# mis-amortised, so for now an oversized loan surfaces as a breach rather
		# than being silently trimmed.
		# Loan repayments live in their own child table with their own total.
		# Each one is presented to the reduction as if it were a deduction row,
		# keyed by its loan product, so a company that has ranked that product
		# can have it give way like anything else. A product nobody has ranked
		# is not in `rules`, so it still only consumes the budget.
		# This period is met first and old debts are recovered from what is left.
		# Putting every arrears claim below the whole of the current period does
		# that, while the group's own priority still decides which debt is
		# recovered before which - and, within one priority, the oldest first.
		for proxy in loan_rows:
			if proxy.get("kind") == "arrears":
				proxy.priority_override = _catch_up_rank(
					rules.get(proxy.salary_component, frappe._dict()), floor
				)
		claims = rows + loan_rows + arrears_rows
		total = flt(sum(flt(c.amount) for c in claims), 2)

		excess = flt(total - permitted, 2)
		if excess > TOLERANCE:
			excess = _reduce(claims, rules, excess)

	# What each component is taking for this period, before anything is put
	# towards its old debt. Captured first because the recovery is added onto the
	# same row next, and the two have to stay tellable apart.
	own_collected = {r.salary_component: flt(r.amount, 2) for r in rows}

	# The recovery is its own claim on the budget, but it is still collected
	# through the component's own row - that is the line the employee is
	# deducted on, and what total_deduction is built from.
	for proxy in arrears_rows:
		if flt(proxy.amount) <= TOLERANCE:
			continue
		for row in rows:
			if row.salary_component == proxy.salary_component:
				row.amount = flt(flt(row.amount) + flt(proxy.amount), 2)
				break

	# Written on every run, cut or not, so the row never carries last period's
	# split.
	_apply_loan_cuts(doc, loan_rows)

	# Nothing was cut on a final slip, so _allocate finds every claim fully paid
	# and raises no new debt - it only settles the ones brought forward.
	_allocate(doc, rows, plan, arrears_rows, own_collected)
	doc.custom_one_third_rule_skipped = 1 if final_slip else 0

	# One figure for everything this period could not take, components and loans
	# together. Reporting only the loans under a label that says "total" hides
	# the component shortfall sitting in the table directly above it.
	component_deferred = flt(sum(
		flt(row.deferred_amount) for row in (doc.custom_deferred_deductions or [])
	), 2)
	loan_deferred = flt(sum(
		flt(row.custom_deferred_amount) + flt(row.custom_arrears_deferred)
		for row in (doc.get("loans") or [])
	), 2)
	doc.custom_total_deferred_deductions = flt(component_deferred + loan_deferred, 2)
	doc.custom_has_pending_deductions = (
		1 if doc.custom_total_deferred_deductions > TOLERANCE else 0
	)

	doc.custom_unreducible_excess = max(flt(excess, 2), 0.0)
	doc.custom_deduction_cap_applied = 1 if doc.custom_deferred_deductions else 0

	doc.set_net_pay()

	# HRMS works these out inside its own validate, which runs before this hook,
	# so they were built from the untrimmed net pay. They recompute from scratch
	# rather than accumulate, so asking again once the cap has settled is safe
	# and is the only way the stored figures match what was actually paid.
	doc.compute_year_to_date()
	doc.compute_month_to_date()


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
		# A row ranks either a salary component or a loan product. Loans are a
		# deduction from wages like any other for s.19 purposes, so a company
		# that wants the staff loan to give way before the welfare deduction
		# needs to be able to say so in the same list.
		key = row.salary_component or row.loan_product
		if not (key and row.deduction_group):
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
		rules[key] = frappe._dict({
			"group": group.name,
			"priority": flt(group.priority),
			"reducible": bool(group.reducible),
			"on_shortfall": group.on_shortfall or "Carry Forward",
			"tie_breaker": group.tie_breaker or "Pro-rata",
			# Catching up does not have to mirror the order things give way in:
			# a deduction can be the first to stop and the last to be caught up.
			# Three answers cover it - before the others, after them, or in the
			# same order as Priority - so nobody has to keep a second set of
			# numbers in step with the first.
			"catch_up_order": group.get("catch_up_order") or "In Priority Order",
			"order": row.idx,
		})
	return rules


def _catch_up_rank(rule, floor):
	"""Where this group sits in the queue for spare money.

	Everything here ranks below the whole of the current period - that is what
	the floor is for. Above it, First jumps the queue, In Priority Order keeps
	the group's own rank, and Last goes to the back.
	"""
	choice = rule.get("catch_up_order") or "In Priority Order"
	if choice == "First":
		return floor + 1
	if choice == "Last":
		return floor + floor + 2
	return floor + 1 + flt(rule.priority)


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
	"""What each component still owes from earlier periods.

	Nothing is added to this period's own row. An old debt is a separate claim
	on the budget, made after every current deduction has been met, so adding it
	here would let last month's arrears crowd out this month's deduction - which
	is the opposite of how a payroll is run.

	Only components that already have a row on this slip are considered. A debt
	on a component the employee no longer has is not resurrected onto a slip
	that has no line for it; it stays open until one does.
	"""
	by_component = {}
	for debt in get_outstanding(doc.employee, doc.company, before_slip=doc.name):
		by_component.setdefault(debt.salary_component, []).append(debt)

	plan = {}
	for row in rows:
		component = row.salary_component
		debts = by_component.get(component, []) if component in rules else []
		plan[component] = frappe._dict({
			"salary_component": component,
			"own_amount": flt(row.amount, 2),
			"owed": flt(sum(flt(d.balance_remaining) for d in debts), 2),
			"debts": debts,
			"treatment": rules[component].on_shortfall if component in rules else None,
		})

	return plan


def _arrears_rows(plan, rules, floor):
	"""Old component debts, as claims that rank below every current deduction.

	Each keeps its own group's priority relative to the other debts - a debt on
	a component the company ranks highly is recovered before one it does not -
	but all of them sit below the whole of this period. Adding the floor to the
	group's own priority does both at once: every arrears claim outranks nothing
	current, and among themselves they stay in the order the company set.
	"""
	proxies = []
	for component, entry in plan.items():
		if not entry.owed or component not in rules:
			continue
		proxies.append(frappe._dict({
			"salary_component": component,
			"amount": entry.owed,
			"priority_override": _catch_up_rank(rules[component], floor),
			"age": min(str(d.deferral_date or "") for d in entry.debts),
			"plan_entry": entry,
		}))
	return proxies


def _allocate(doc, rows, plan, arrears_rows=None, own_collected=None):
	"""Split what was actually collected between old debts and this period's own.

	Oldest debt first. This is the difference between a ledger that tells the
	truth and one that doesn't: if the cap cut a component below what it owed,
	only the money genuinely collected may be credited against a debt. Whatever
	is left of a debt stays outstanding at its original age rather than being
	rolled into a new one.
	"""
	doc.set("custom_brought_forward_deductions", [])
	doc.set("custom_deferred_deductions", [])

	# On a final slip nothing was reduced, so what each row holds is what it
	# collected; otherwise the figure captured before the recovery was added on.
	collected = own_collected or {r.salary_component: flt(r.amount, 2) for r in rows}
	recovered = {p.salary_component: flt(p.amount, 2) for p in (arrears_rows or [])}

	for component, entry in plan.items():
		collected_here = flt(collected.get(component, 0.0), 2)

		# What survived the reduction on this component's arrears claim. The
		# current period's own amount is a separate claim and is not touched by
		# what is put towards the debt.
		remaining = flt(recovered.get(component, 0.0), 2)

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

		own_applied = collected_here
		own_deferred = flt(max(entry.own_amount - own_applied, 0.0), 2)
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
		# An arrears claim carries its own rank, set below every current claim so
		# this period is met before anything is put towards an old debt.
		priority = row.get("priority_override") or rules[row.salary_component].priority
		tiers.setdefault(priority, []).append(row)

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


def _loan_rows(doc, rules):
	"""Each loan repayment on the slip, as one claim on the budget.

	The claim is this period's instalment plus whatever earlier periods left
	unpaid, ranked by the loan's own group like any other deduction. Recovery
	then follows the same order as everything else: the reduction cuts the least
	important tier first, so the most important is funded first, and inside a
	tier the group's tie breaker decides - Oldest Loan First settling the oldest
	debt before a newer one.

	`salary_component` carries the loan product because that is the key _reduce
	and _share_the_cut look rules up by; giving the proxies the same shape keeps
	one reduction path for both loans and components.
	"""
	proxies = []
	for row in (doc.get("loans") or []):
		# HRMS appends the row without a product - it carries the loan, the
		# accounts and the amounts, nothing else - so the product has to be
		# fetched or the loan is invisible to the ranking.
		if not row.loan_product and row.loan:
			row.loan_product = frappe.db.get_value("Loan", row.loan, "loan_product")
		if not row.loan_product:
			continue

		claimed, interest_due = _payable(row, doc.end_date)
		arrears_owed, oldest = _arrears(row.loan, doc.start_date)
		brought_forward = flt(min(arrears_owed, claimed), 2)
		instalment = flt(max(claimed - brought_forward, 0.0), 2)

		proxies.append(frappe._dict({
			"salary_component": row.loan_product,
			"amount": instalment,
			"kind": "instalment",
			"instalment": instalment,
			"brought_forward": brought_forward,
			"interest_due": flt(interest_due, 2),
			"loan_row": row,
		}))

		if brought_forward > TOLERANCE:
			proxies.append(frappe._dict({
				"salary_component": row.loan_product,
				"amount": brought_forward,
				"kind": "arrears",
				"age": oldest,
				"loan_row": row,
			}))

	return proxies


def _payable(row, as_at):
	"""What the loan is owed this period, asked of lending rather than the row.

	total_payment on the row cannot be the claim: this function has usually
	already reduced it on an earlier save, and reading it back would treat last
	save's cut as this save's demand. The reduction would then be permanent -
	invisibly so, because the figures still add up - and the instalment would
	never recover if the employee's other deductions fell away.

	Lending is the one that knows: payable_amount is this period's instalment
	plus whatever earlier demands are still outstanding.
	"""
	try:
		from lending.loan_management.doctype.loan_repayment.loan_repayment import (
			calculate_amounts,
		)
	except ImportError:
		return flt(row.total_payment), flt(row.interest_amount)

	if not row.loan:
		return flt(row.total_payment), flt(row.interest_amount)

	amounts = calculate_amounts(row.loan, as_at) or {}
	payable = flt(amounts.get("payable_amount")) or flt(row.total_payment)
	return payable, flt(amounts.get("interest_amount"))


def _arrears(loan, start_date):
	"""What earlier periods still owe on this loan.

	Read from the demands, which is where lending records an instalment that was
	not paid in full. Anything dated before this period and still outstanding is
	arrears by definition, whatever the schedule says the instalment was.
	"""
	rows = frappe.get_all(
		"Loan Demand",
		filters={
			"loan": loan,
			"docstatus": 1,
			"demand_date": ("<", start_date),
			"outstanding_amount": (">", 0),
		},
		fields=["outstanding_amount", "demand_date"],
		order_by="demand_date asc",
	)
	owed = flt(sum(flt(row.outstanding_amount) for row in rows), 2)
	oldest = str(rows[0].demand_date)[:10] if rows else ""
	return owed, oldest


def _apply_loan_cuts(doc, loan_rows):
	"""Write the applied and deferred split back onto the slip.

	What was collected settles the arrears before this period's instalment. That
	is not a preference: lending allocates a repayment against the oldest demand
	first, so recording it any other way would leave the payslip disagreeing with
	the loan it just paid.

	Interest is settled before principal, which is the order the collection
	offset order allocates in, so the shortfall falls on principal.

	Nothing is written to the loan itself. What was not collected simply was not
	collected - the demand stays outstanding and is claimed again next period.
	"""
	deferred_total = 0.0

	claims = {}
	for proxy in loan_rows:
		entry = claims.setdefault(id(proxy.loan_row), frappe._dict({
			"row": proxy.loan_row, "instalment": 0.0, "brought_forward": 0.0,
			"instalment_paid": 0.0, "arrears_paid": 0.0, "interest_due": 0.0,
		}))
		if proxy.get("kind") == "arrears":
			entry.arrears_paid = flt(proxy.amount, 2)
		else:
			entry.instalment = flt(proxy.instalment, 2)
			entry.brought_forward = flt(proxy.brought_forward, 2)
			entry.interest_due = flt(proxy.interest_due, 2)
			entry.instalment_paid = flt(proxy.amount, 2)

	for entry in claims.values():
		row = entry.row
		proxy = entry
		applied = flt(entry.instalment_paid + entry.arrears_paid, 2)
		arrears_paid = flt(entry.arrears_paid, 2)
		instalment_paid = flt(entry.instalment_paid, 2)

		deferred = flt(max(proxy.instalment - instalment_paid, 0.0), 2)
		arrears_deferred = flt(max(proxy.brought_forward - arrears_paid, 0.0), 2)
		deferred_total += deferred + arrears_deferred

		row.custom_scheduled_payment = flt(proxy.instalment, 2)
		row.custom_brought_forward_amount = flt(proxy.brought_forward, 2)
		row.custom_deferred_amount = deferred
		row.custom_arrears_deferred = arrears_deferred
		row.total_payment = applied

		# Split what was collected, never subtract from what the row already
		# holds: this runs on every save, and the row it would subtract from is
		# the one the last save already reduced.
		row.interest_amount = flt(min(applied, proxy.interest_due), 2)
		row.principal_amount = flt(applied - row.interest_amount, 2)

	collected = flt(sum(flt(r.total_payment) for r in (doc.get("loans") or [])), 2)
	doc.total_loan_repayment = collected
	doc.custom_total_actual_repayment = collected


def _share_the_cut(members, rules, excess, tier_total):
	"""Split a partial cut between deductions that rank equally.

	Only reached when the tier can absorb the whole excess, so this always
	returns nothing left over - what it decides is who bears it.
	"""
	# A tier made of arrears is settled oldest first, whatever the group's own
	# tie breaker says: the longest standing debt is the one to clear.
	if all(m.get("age") is not None for m in members):
		ordered = sorted(members, key=lambda m: (m.get("age") or ""), reverse=True)
		for member in ordered:
			if excess <= TOLERANCE:
				break
			cut = min(flt(member.amount), excess)
			member.amount = flt(flt(member.amount) - cut, 2)
			excess = flt(excess - cut, 2)
		return excess

	method = rules[members[0].salary_component].tie_breaker

	if method == "Oldest Loan First":
		# Two loans of equal rank are not equal claims: the one taken first has
		# been running longest and is nearest to being cleared, so it is settled
		# in full before anything is taken off the newer one. Anything that is
		# not a loan has no date to sort by and is cut last, since a component
		# was never what this method was chosen for.
		ordered = sorted(members, key=lambda m: (_loan_started(m) or ""), reverse=True)
		for member in ordered:
			if excess <= TOLERANCE:
				break
			cut = min(flt(member.amount), excess)
			member.amount = flt(flt(member.amount) - cut, 2)
			excess = flt(excess - cut, 2)
		return excess

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


def _loan_started(member):
	"""When the loan behind this claim began repaying.

	Sorted as a string so a claim with no date - anything that is not a loan -
	sorts before every dated one and is therefore cut first.
	"""
	row = member.get("loan_row")
	if not row or not row.loan:
		return ""

	dates = frappe.db.get_value(
		"Loan", row.loan, ["repayment_start_date", "posting_date"], as_dict=True
	) or frappe._dict()

	# When two loans start repaying in the same month, the one taken earlier is
	# the older debt, so the date it was booked breaks the tie.
	return "{0}|{1}".format(
		dates.repayment_start_date or "", dates.posting_date or ""
	) if (dates.repayment_start_date or dates.posting_date) else ""


def _clear(doc):
	doc.custom_deduction_cap_applied = 0
	doc.custom_unreducible_excess = 0
	doc.custom_one_third_rule_skipped = 0
	# These three carry on being read after the rule is switched off: the Salary
	# Slip mixin restates net pay from custom_total_actual_repayment on every
	# save. Left behind, a company that turned the rule off kept having its net
	# pay forced to whatever the rule last decided.
	doc.custom_total_actual_repayment = 0
	doc.custom_total_deferred_deductions = 0
	doc.custom_has_pending_deductions = 0
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
