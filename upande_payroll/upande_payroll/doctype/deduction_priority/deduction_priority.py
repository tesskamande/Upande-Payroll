# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from upande_payroll.deduction_cap import band_tie_breaker


class DeductionPriority(Document):
	def validate(self):
		self.validate_one_thing_per_row()
		self.validate_no_duplicate_components()
		self.validate_no_duplicate_base_components()
		self.validate_groups_belong_to_company()
		self.validate_statutory_not_reducible()
		self.tidy_priority_overrides()
		self.validate_tie_breaker_per_rank()
		self.set_effective_tie_breakers()

	def tidy_priority_overrides(self):
		"""An override that matches the group is not an override.

		Left stored, it reads as a deliberate exception when it is nothing of the
		kind, and it stops meaning what it says the moment the group's own
		priority moves. Cleared, the row simply follows its group again.
		"""
		for row in self.deductions:
			override = cint(row.override_priority)
			# Cleared to 0, not None: the field is an Int, so the database stores
			# 0 either way and returning None here would only make the document
			# in memory disagree with the one that comes back out.
			if not override:
				row.override_priority = 0
				continue
			if override < 1:
				frappe.throw(
					_("Row {0}: Override Pri must be 1 or higher. 1 is the most "
					  "important rank.").format(row.idx)
				)
			if override == cint(row.group_priority):
				row.override_priority = 0

	def validate_tie_breaker_per_rank(self):
		"""Two groups may share a rank, but not disagree about it.

		When deductions rank equally, one tie breaker orders the whole band. A
		row placed there by an Override Pri is a visitor and adopts the method of
		the group that owns the rank, so an override needs no permission from
		this check. What cannot be resolved is two groups each declaring the same
		priority while breaking ties differently: both own the rank, neither
		yields, and picking one would decide silently whose wages are cut.

		The remaining case - a rank made only of visitors who disagree, with no
		group of its own - is caught at the point of use, where the members are
		known.
		"""
		owners = {}
		for row in self.deductions:
			group = row.deduction_group
			if not group:
				continue
			rank = cint(row.group_priority)
			# Only groups sitting at their own rank own it; overridden rows are
			# visiting somebody else's and have no say in how it is ordered.
			if cint(row.override_priority):
				continue
			method = frappe.db.get_value("Deduction Group", group, "tie_breaker")
			seen = owners.setdefault(rank, {})
			seen.setdefault(method, (group, row.idx))
			if len(seen) > 1:
				(first_group, first_idx), (other_group, other_idx) = list(seen.values())[:2]
				first_method, other_method = list(seen.keys())[:2]
				frappe.throw(
					_(
						"Rank {0} is claimed by two groups that break ties "
						"differently: {1} uses {2} (row {3}) and {4} uses {5} "
						"(row {6}). Both own the rank, so neither can decide who "
						"bears a cut. Match the two tie breakers, or move one "
						"group to a rank of its own."
					).format(
						rank,
						frappe.bold(first_group), frappe.bold(first_method or "Pro-rata"), first_idx,
						frappe.bold(other_group), frappe.bold(other_method or "Pro-rata"), other_idx,
					),
					title=_("Conflicting Tie Breakers"),
				)

	def set_effective_tie_breakers(self):
		"""Say on each row which tie breaker will actually order its rank.

		Once a row can be overridden into another group's rank, its own group's
		method may not be the one that decides who bears a cut - and nothing on
		the form said so. This fills that in, naming the group the method is
		borrowed from when it is not the row's own.

		It reads the same helper the reduction reads, so the column cannot
		promise one thing and payroll do another. Doing it here also brings the
		one unresolvable case forward: a rank nobody owns whose visitors
		disagree used to surface weeks later, mid payroll, because that is when
		a cut first needed a method.
		"""
		groups = {}
		for row in self.deductions:
			if row.deduction_group and row.deduction_group not in groups:
				groups[row.deduction_group] = frappe.db.get_value(
					"Deduction Group", row.deduction_group,
					["priority", "tie_breaker", "group_name"], as_dict=True
				) or frappe._dict()

		bands = {}
		for row in self.deductions:
			if not row.deduction_group:
				continue
			group = groups[row.deduction_group]
			rank = cint(row.override_priority) or cint(group.priority)
			bands.setdefault(rank, []).append(row)

		for rank, rows in bands.items():
			entries = [
				(
					cint(groups[r.deduction_group].priority),
					groups[r.deduction_group].tie_breaker or "Pro-rata",
					r.deduction_group,
				)
				for r in rows
			]
			method, owner = band_tie_breaker(rank, entries)

			if method is None:
				frappe.throw(
					_(
						"Rank {0} is reached only by Override Pri, from groups that "
						"break ties differently ({1}). With no group at rank {0} to "
						"settle it, nothing decides who bears a cut. Give one of them "
						"a group at rank {0}, or match their tie breakers."
					).format(rank, ", ".join(sorted(str(m) for m in owner))),
					title=_("Conflicting Tie Breakers"),
				)

			for row in rows:
				# Named only when borrowed. A row following its own group's method
				# needs no explanation; one following somebody else's does.
				if owner and owner != row.deduction_group:
					row.effective_tie_breaker = "{0} (from {1})".format(
						method, groups[owner].group_name or owner
					)
				else:
					row.effective_tie_breaker = method

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
		"""A group is either this company's or shared.

		Shared Across Companies clears the group's Company, which is what lets
		one Statutory tier serve every company on the site instead of a copy each.
		Anything set to a different company is still refused - a tier that reads
		as another company's is the kind of thing nobody notices until the wrong
		deduction gives way.
		"""
		for row in self.deductions:
			if not row.deduction_group:
				continue
			owner = frappe.db.get_value("Deduction Group", row.deduction_group, "company")
			if owner and owner != self.company:
				frappe.throw(
					_("Row {0}: Deduction Group {1} belongs to {2}, not {3}. Use one "
					  "of {3}'s own groups, or one marked Shared Across Companies.")
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
