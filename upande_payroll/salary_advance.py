# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

"""Recovering Employee Salary Advances through payroll.

The advance document holds the plan; the payslip collects against it. Three
things follow from where the boundary is drawn:

  * The instalment is claimed as an ordinary row in the slip's `deductions`
	table, keyed by the advance type's salary component. That is what earns it
	the two thirds cap, the Deduction Priority ranking and the group tie
	breakers for free - deduction_cap.py works on components, so a component is
	what an advance has to present itself as.

  * Arrears belong to the advance, not to the Deferred Deduction ledger. A
	Deferred Deduction is keyed by salary component, and an employee may hold
	more than one advance on the same component, so the ledger cannot say which
	advance a shortfall belongs to. The schedule can: anything due and unpaid is
	arrears by definition, and _due() simply claims it again.

  * What was actually collected is only known after the cap has run, so it is
	written back on submit rather than on validate, into a recovery log the
	schedule's paid figures are derived from.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

# The same test the two thirds rule uses. Deliberately shared: if the cap thinks
# a payslip is somebody's last and takes everything, an advance has to agree, or
# one of them collects on a period the other has already written off.
from upande_payroll.deduction_cap import _is_final_slip

# Currency comparisons at 2dp; below this is rounding noise.
TOLERANCE = 0.01

# The advances payroll still has something to collect from.
COLLECTING = ("Unpaid", "Partially Repaid")


# ======================================================================
# Salary Slip: validate
# ======================================================================

def apply_salary_advances(doc, method=None):
	"""Put what the advances are owed this period onto the slip.

	Runs before the two thirds cap, so the instalment is one of the deductions
	the cap weighs rather than something added after it has finished. If the cap
	then trims the row, the shortfall stays owed on the schedule and is claimed
	again next period - no ledger entry needed, because the schedule never
	stopped saying it was due.
	"""
	advances = _advances(doc)
	if not advances:
		return

	# Every component any of this employee's advances uses, including advances
	# already repaid. Claiming nothing has to be able to clear a row this hook
	# put there last time, and only the components it owns may be touched.
	claims = {a.salary_component: 0.0 for a in advances if a.salary_component}
	policy = _shortfall_policy(doc.company, set(claims))
	final = _is_final_slip(doc)

	for advance in advances:
		if advance.status not in COLLECTING or not advance.repay_from_salary:
			continue
		claims[advance.salary_component] = flt(
			claims[advance.salary_component] + _due(
				advance.name, advance.outstanding_amount, doc.start_date, doc.end_date,
				arrears=policy.get(advance.salary_component) == "advance",
				catch_up_cap=flt(frappe.get_cached_value(
					"Employee Salary Advance Type", advance.advance_type,
					"max_catch_up_amount",
				)),
				final=final,
			), 2
		)

	_write_rows(doc, claims)
	doc.set_net_pay()


def _advances(doc):
	"""This employee's submitted advances, oldest first.

	Repaid and closed advances are included on purpose: they are what tells
	_write_rows which components this hook is entitled to clear.
	"""
	return frappe.get_all(
		"Employee Salary Advance",
		filters={"employee": doc.employee, "company": doc.company, "docstatus": 1},
		fields=["name", "advance_type", "salary_component", "status",
				"repay_from_salary", "outstanding_amount", "posting_date"],
		order_by="posting_date asc, creation asc",
	)


def _due(advance, outstanding, start, end, arrears=True, catch_up_cap=0.0,
		 final=False):
	"""What an advance is owed on this slip.

	With `arrears`, every instalment already due and not yet fully paid: a period
	the payslip could not meet is still due, so it is claimed again, and arrears
	fall out of the schedule rather than needing to be tracked anywhere.

	Without it, only the period the slip covers. That is for a component whose
	Deduction Group carries a shortfall forward - the Deferred Deduction ledger is
	already re-claiming the unpaid part, and claiming it here as well would take
	the same arrears twice.

	On a leaver's last payslip every remaining period is claimed, due or not, and
	neither the arrears policy nor the ceiling applies. There is no later payroll
	to collect from, so a period the schedule puts in December is money that walks
	out of the door if this slip does not take it.

	`catch_up_cap` limits the overdue part only, never the instalment the period
	itself is for. Without it one payroll run can recover every missed instalment
	at once, so an employee who missed three months meets four in the fourth -
	which is legal under the two thirds cap and still not something to do to
	somebody by accident.

	Either way the claim is capped at the advance's own outstanding balance, so a
	schedule caught mid-edit cannot ask for more than is owed.
	"""
	rows = frappe.get_all(
		"Employee Salary Advance Schedule",
		filters={"parent": advance, "parenttype": "Employee Salary Advance"},
		fields=["due_date", "instalment_amount", "paid_amount", "status"],
	)

	current = overdue = future = 0.0
	for row in rows:
		# A waived period is money the company has given up. It is still in the
		# total, so it is still in `outstanding`, which is why it has to be dropped
		# here rather than relied on to fall out of the cap below.
		if row.status == "Waived":
			continue

		due = getdate(row.due_date)
		owing = max(flt(row.instalment_amount) - flt(row.paid_amount), 0.0)

		if due > getdate(end):
			future += owing
		elif due < getdate(start):
			overdue += owing
		else:
			current += owing

	if final:
		return flt(min(current + overdue + future, flt(outstanding)), 2)

	if not arrears:
		overdue = 0.0
	elif catch_up_cap > 0:
		overdue = min(overdue, flt(catch_up_cap))

	return flt(min(current + overdue, flt(outstanding)), 2)


def _write_rows(doc, claims):
	"""Set each advance component's row to what the advances are claiming.

	One row per component however many advances share it - the employee is
	deducted once, on one line, and the split between advances is settled on
	submit. A claim of nothing removes the row rather than leaving a zero on the
	payslip.
	"""
	for component, amount in claims.items():
		amount = flt(amount, 2)
		row = next(
			(d for d in (doc.deductions or []) if d.salary_component == component), None
		)

		if amount <= TOLERANCE:
			if row and not row.get("additional_salary"):
				doc.deductions.remove(row)
			continue

		if row:
			row.amount = amount
			row.default_amount = amount
			continue

		row = doc.append("deductions", {
			"salary_component": component,
			"abbr": frappe.db.get_value(
				"Salary Component", component, "salary_component_abbr"
			),
			"amount": amount,
			"default_amount": amount,
			"additional_amount": 0,
			"statistical_component": 0,
			# A fixed instalment, not a rate for time worked: half a month
			# present does not halve what the employee borrowed.
			"depends_on_payment_days": 0,
			"amount_based_on_formula": 0,
		})
		_place_above_statistical(doc, row)


def _place_above_statistical(doc, row):
	"""Keep the instalment among the deductions the employee actually bears.

	append puts a new row last, which on a payslip is below the employer
	contributions and Taxable Income - rows that are there for the calculation and
	the accounts, carry do_not_include_in_total, and are not money the employee
	parts with. An advance instalment printed underneath them reads like an
	afterthought, or worse like something outside the total.

	Only the display order changes. The two thirds rule ranks by Deduction Group
	and its tie breaker reads the Deduction Priority row's position, not the
	payslip's, so nothing about what gets cut depends on where this sits.
	"""
	rows = doc.deductions
	rows.remove(row)

	position = len(rows)
	for index, existing in enumerate(rows):
		if existing.do_not_include_in_total or existing.get("statistical_component"):
			position = index
			break

	rows.insert(position, row)
	for index, existing in enumerate(rows, start=1):
		existing.idx = index


def _shortfall_policy(company, components):
	"""Who owns an instalment the payslip could not collect.

	Two arrangements are correct and the only real mistake is running both at
	once, which takes the same arrears twice - once from the schedule that still
	says the period is due, once from the Deferred Deduction the cap raised. Both
	records look right on their own, which is what makes it worth deciding here
	rather than leaving it to configuration to get right.

	  ledger   the component's Deduction Group carries a shortfall forward, so
	           deduction_cap.py brings the debt back onto the component's own row
	           next period. The advance claims only the period in front of it.
	  advance  anything else. The schedule keeps claiming what is due and unpaid,
	           which is also the only arrangement that can attribute a shortfall
	           when an employee holds two advances on one component - a Deferred
	           Deduction is keyed by component and cannot tell them apart.
	"""
	policy = {component: "advance" for component in components}
	if not components:
		return policy

	priority = frappe.db.get_value("Deduction Priority", {"company": company}, "name")
	if not priority:
		return policy

	rows = frappe.get_all(
		"Deduction Priority Detail",
		filters={"parent": priority, "salary_component": ("in", list(components))},
		fields=["salary_component", "deduction_group"],
	)
	for row in rows:
		if not row.deduction_group:
			continue
		on_shortfall = frappe.db.get_value(
			"Deduction Group", row.deduction_group, "on_shortfall"
		)
		if on_shortfall == "Carry Forward":
			policy[row.salary_component] = "ledger"

	return policy


# ======================================================================
# Salary Slip: submit and cancel
# ======================================================================

def settle_salary_advances(doc, method=None):
	"""Credit what the slip actually collected against the advances.

	Only reachable on submit, because until the cap has run the amount on the
	row is a claim rather than a collection. Where several advances share a
	component the oldest is settled first: it has been outstanding longest and
	is nearest to being cleared, which is the same rule the deduction groups
	apply to competing loans.
	"""
	advances = [
		a for a in _advances(doc)
		if a.status in COLLECTING and a.repay_from_salary and a.salary_component
	]
	if not advances:
		return

	final = _is_final_slip(doc)

	pots = {}
	for row in (doc.deductions or []):
		if row.do_not_include_in_total:
			continue
		pots[row.salary_component] = flt(
			pots.get(row.salary_component, 0.0) + flt(row.amount), 2
		)

	for advance in advances:
		available = flt(pots.get(advance.salary_component, 0.0), 2)
		if available <= TOLERANCE:
			continue
		document = frappe.get_doc("Employee Salary Advance", advance.name)
		spent = document.apply_recovery(
			"Salary Slip", doc.name, doc.posting_date or doc.end_date, available,
			# A last payslip took every remaining period, so every remaining period
			# has to be creditable; an ordinary one is bounded by what is due.
			as_at=None if final else doc.end_date,
		)
		pots[advance.salary_component] = flt(available - spent, 2)

	if final:
		_warn_if_unrecovered(doc, advances)


def _warn_if_unrecovered(doc, advances):
	"""Say so when a leaver's last payslip could not clear an advance.

	Nothing is written off here - the balance stays on the advance, which is
	where it belongs if anyone is going to chase it or decide to waive it. What
	matters is that the last payroll that could have collected it does not pass
	in silence, because after this there is no payslip left to notice.
	"""
	left = []
	for advance in advances:
		outstanding = flt(frappe.db.get_value(
			"Employee Salary Advance", advance.name, "outstanding_amount"
		))
		if outstanding > TOLERANCE:
			left.append((advance.name, outstanding))

	if not left:
		return

	currency = frappe.get_cached_value("Company", doc.company, "default_currency")
	frappe.msgprint(
		_("This is the last payslip for {0}, and these advances are still not "
		  "cleared: {1}. Recover the balance through the Terminal Dues "
		  "Settlement, or waive it - no later payroll will claim it.").format(
			frappe.bold(doc.employee_name or doc.employee),
			", ".join(f"{name} ({frappe.utils.fmt_money(amount, currency=currency)})"
			          for name, amount in left),
		),
		title=_("Advance not fully recovered"),
		indicator="orange",
	)


def unsettle_salary_advances(doc, method=None):
	"""Undo this slip's recoveries when it is cancelled."""
	affected = {
		row.parent for row in frappe.get_all(
			"Employee Salary Advance Recovery",
			filters={"reference_doctype": "Salary Slip", "reference_name": doc.name,
					 "parenttype": "Employee Salary Advance"},
			fields=["parent"],
		)
	}

	for advance in affected:
		frappe.get_doc("Employee Salary Advance", advance).reverse_recovery(
			"Salary Slip", doc.name
		)
