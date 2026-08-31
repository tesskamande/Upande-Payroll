import frappe
from frappe.utils import flt, rounded


def merge_duplicate_components(doc, method=None):
	"""One row per salary component on the payslip.

	Two Additional Salaries for the same component - two Overtime 1.5 entries
	with different payroll dates, say - come through as two rows, because core
	keeps each row tied to the Additional Salary that produced it. On the
	payslip that reads as the same thing paid twice rather than one amount.

	The rows are merged into the first of each component and the amounts added
	up. Nothing is lost: each Additional Salary is still its own document, and
	the payslip's own total is unchanged.

	Rows are only merged where they agree on how they behave. A component that
	appears once inside the total and once outside it stays as two rows, because
	folding those together would change what the payslip pays.
	"""
	for parentfield in ("earnings", "deductions"):
		rows = doc.get(parentfield) or []
		if len(rows) < 2:
			continue

		merged, seen = [], {}
		for row in rows:
			key = (
				row.salary_component,
				# Rows that behave differently are left alone.
				1 if row.do_not_include_in_total else 0,
				1 if row.get("do_not_include_in_accounts") else 0,
				1 if row.get("statistical_component") else 0,
			)
			first = seen.get(key)
			if first is None:
				seen[key] = row
				merged.append(row)
				continue

			first.amount = flt(flt(first.amount) + flt(row.amount), 2)
			first.default_amount = flt(
				flt(first.get("default_amount")) + flt(row.get("default_amount")), 2
			)
			# The surviving row KEEPS its link to the Additional Salary that
			# produced it. That link is what core matches on when it rebuilds the
			# slip, and it is what makes this safe to run on every save: the
			# kept row is reset to its own figure, the others are appended
			# fresh, and they are merged again to the same total.
			#
			# Clearing it looked tidier and was wrong - core then matched
			# nothing, appended a row for every Additional Salary on top of the
			# merged one, and the total climbed on each save: 7,500 became
			# 15,000, then 22,500.

		if len(merged) != len(rows):
			doc.set(parentfield, [])
			for row in merged:
				doc.append(parentfield, row.as_dict())


class SalarySlipMixin:
	"""Keeps net pay in step with what the two thirds rule actually allowed.

	The rule runs on validate and records what it let through in
	custom_total_actual_repayment. HRMS works net pay out from
	total_loan_repayment - the whole scheduled instalment - inside its own
	calculate_net_pay, which has already run by then, so the figure has to be
	put back before anything reads it: year to date, month to date and submit.

	This lives in payroll rather than in the lending overrides because the
	field, the rule and the correction are all payroll's business. Lending
	supplies the loan rows and nothing more.
	"""

	def compute_year_to_date(self):
		apply_capped_loan_repayment(self)
		super().compute_year_to_date()

	def compute_month_to_date(self):
		apply_capped_loan_repayment(self)
		super().compute_month_to_date()

	def before_submit(self):
		apply_capped_loan_repayment(self)
		parent = getattr(super(), "before_submit", None)
		if parent:
			parent()


def apply_capped_loan_repayment(doc):
	"""Restate net pay from the repayment the two thirds rule allowed.

	Does nothing where the rule never ran or collected nothing, which is also
	what makes it safe on a site with no loans at all.
	"""
	actual = flt(doc.get("custom_total_actual_repayment"))
	if not actual:
		return

	doc.total_loan_repayment = actual
	doc.net_pay = flt(doc.gross_pay) - flt(doc.total_deduction) - actual
	doc.rounded_total = rounded(doc.net_pay)
	doc.base_net_pay = flt(doc.net_pay) * flt(doc.exchange_rate or 1)
	doc.base_rounded_total = rounded(doc.base_net_pay)


def sort_components_by_structure(doc, method=None):
	"""Put the payslip's rows back into the order the Salary Structure lists them.

	Core builds the slip one structure row at a time, so it starts out in the
	right order, but two things disturb it. A row that works out to zero is
	dropped when its component is set to remove_if_zero_valued, and anything
	written afterwards is appended to the end: a statutory figure this app
	computes, an Additional Salary, an advance. So a component the structure
	puts in the middle can surface at the bottom of the payslip. Taxable Income
	sits sixth in DEMO One Third and came out last, below the employer
	contributions.

	Nothing about the money changes here - the same rows carry the same amounts,
	and the totals were worked out before this runs. Only the order moves.

	Rows whose component the structure does not list - an Additional Salary for
	a component that is not on it, an advance - keep their own order and follow
	the ones it does, because there is nothing to line them up against.
	"""
	if not doc.get("salary_structure"):
		return

	for parentfield in ("earnings", "deductions"):
		rows = doc.get(parentfield) or []
		if len(rows) < 2:
			continue

		position, visiting = _structure_positions(doc.salary_structure, parentfield)
		if not position:
			continue

		# sorted() is stable, so rows sharing a key hold the order they already
		# had rather than being shuffled among themselves.
		ordered = sorted(rows, key=lambda row: position.get(row.salary_component, visiting))
		if all(before is after for before, after in zip(rows, ordered)):
			continue

		# Passing the existing row objects back keeps each child row's name, so
		# this reorders them rather than deleting and reinserting the table on
		# every save. append() leaves an idx that is already set alone, which is
		# why they are renumbered afterwards.
		doc.set(parentfield, ordered)
		for index, row in enumerate(doc.get(parentfield), start=1):
			row.idx = index


def _structure_positions(structure, parentfield):
	"""Where the Salary Structure lists each component, and where a component it
	does not list belongs.

	Returns ``(position, visiting)``. A row whose component the structure has
	nothing to say about - an advance instalment, an Additional Salary for a
	component that is not on the structure - takes ``visiting``, which puts it
	just above the first row carrying do_not_include_in_total: the employer
	contributions and markers like Taxable Income.

	That placement is not cosmetic bookkeeping. salary_advance._place_above_statistical
	already puts an instalment there deliberately, because those trailing rows
	are not money the employee parts with, and a deduction printed below them
	reads as if it sits outside the total. Sending unlisted rows to the very end
	would undo that.

	Cached doc, so a payroll run of two hundred slips reads the structure once.
	"""
	rows = frappe.get_cached_doc("Salary Structure", structure).get(parentfield) or []
	position = {}
	visiting = None
	for index, row in enumerate(rows):
		# First mention wins if a structure lists a component twice.
		position.setdefault(row.salary_component, index)
		if visiting is None and (row.do_not_include_in_total or row.get("statistical_component")):
			# Half a step above that row, so an unlisted component lands there
			# whatever order the slip happened to build them in.
			visiting = index - 0.5
	if visiting is None:
		visiting = len(rows)
	return position, visiting
