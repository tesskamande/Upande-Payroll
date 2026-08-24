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
