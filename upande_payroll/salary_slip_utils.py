import frappe
from frappe.utils import flt


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
