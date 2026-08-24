# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class DeductionPriority(Document):
	def validate(self):
		self.validate_one_thing_per_row()
		self.validate_no_duplicate_components()
		self.validate_no_duplicate_base_components()
		self.validate_groups_belong_to_company()
		self.validate_statutory_not_reducible()

	def validate_one_thing_per_row(self):
		"""A row ranks a salary component or a loan product, never both.

		Both filled is two rankings in one row, and the second would be silently
		ignored. Neither filled is a group with nothing in it, which quietly
		consumes a priority nobody can see.
		"""
		for row in self.deductions:
			if row.salary_component and row.loan_product:
				frappe.throw(
					_("Row {0} has both {1} and {2}. A row ranks one or the other, "
					  "so put the loan on its own row.")
					.format(row.idx, frappe.bold(row.salary_component),
							frappe.bold(row.loan_product))
				)
			if not row.salary_component and not row.loan_product:
				frappe.throw(
					_("Row {0} has neither a Salary Component nor a Loan Product. "
					  "Set one, or remove the row.").format(row.idx)
				)

	def validate_no_duplicate_components(self):
		seen = {}
		for row in self.deductions:
			key = row.salary_component or row.loan_product
			if key in seen:
				frappe.throw(
					_("{0} appears twice, in rows {1} and {2}. It belongs to one group.")
					.format(frappe.bold(key), seen[key], row.idx)
				)
			seen[key] = row.idx

	def validate_no_duplicate_base_components(self):
		"""The wage base is a list of what counts as wages, not a tally.

		A component listed twice is read once - the base is built from a set -
		so the second row changes nothing and only makes the list look like it
		means something it does not.
		"""
		seen = {}
		for row in (self.base_components or []):
			if not row.salary_component:
				continue
			if row.salary_component in seen:
				frappe.throw(
					_("{0} is listed twice in the wage base, in rows {1} and {2}.")
					.format(frappe.bold(row.salary_component),
							seen[row.salary_component], row.idx)
				)
			seen[row.salary_component] = row.idx

	def validate_groups_belong_to_company(self):
		for row in self.deductions:
			if not row.deduction_group:
				continue
			owner = frappe.db.get_value("Deduction Group", row.deduction_group, "company")
			if owner != self.company:
				frappe.throw(
					_("Row {0}: Deduction Group {1} belongs to {2}, not {3}.")
					.format(row.idx, frappe.bold(row.deduction_group), owner, self.company)
				)

	def validate_statutory_not_reducible(self):
		"""Statutory deductions are owed to the state under their own statutes.

		Reducing one to satisfy the 1/3 rule doesn't lower the liability, it just
		moves it onto the employer, who is still assessed for the full amount.
		They may be listed - a Statutory tier that consumes budget ahead of
		everything else is a fair way to describe them - but only in a group
		that can never be trimmed.
		"""
		from upande_payroll.kenya_statutory_calculator import get_statutory_components

		statutory = set(get_statutory_components().values())
		for row in self.deductions:
			if row.salary_component not in statutory:
				continue
			if frappe.db.get_value("Deduction Group", row.deduction_group, "reducible"):
				frappe.throw(
					_(
						"Row {0}: {1} is a statutory deduction, so it cannot sit in {2}, "
						"which may be reduced. Put it in a group with May Be Reduced "
						"cleared, or leave it out entirely - anything unlisted is already "
						"protected."
					).format(row.idx, frappe.bold(row.salary_component),
							 frappe.bold(row.deduction_group)),
					title=_("Statutory Deduction"),
				)
