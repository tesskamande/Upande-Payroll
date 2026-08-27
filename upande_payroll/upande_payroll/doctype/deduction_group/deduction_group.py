# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class DeductionGroup(Document):
	"""A tier of deductions that give way together under the 1/3 rule.

	Priority lives here rather than against every component so that a company
	with twenty deductions orders four tiers instead of numbering twenty rows.
	"""

	def autoname(self):
		"""``Company-Group`` for a company's own tier, the bare name when shared.

		This was a format string on the doctype. It is here instead because the
		name has to follow Shared Across Companies rather than Company, and
		Company cannot be trusted at this point: Document.insert fills a blank
		Link to Company from the user's default before naming runs, so a group
		meant to be shared would otherwise be named after whichever company the
		person creating it happens to work in.

		Existing groups keep their names - autoname only runs on insert.
		"""
		if self.shared:
			# Cleared here as well as in validate, because naming happens first
			# and a shared group must not carry a company into its own name.
			self.company = None
			self.name = self.group_name
		else:
			# Naming runs before the mandatory check, so a missing company has to
			# be caught here or the group is named "None-Something".
			if not self.company:
				frappe.throw(_(
					"Set a Company, or tick Shared Across Companies to let every "
					"company use this group."
				))
			self.name = f"{self.company}-{self.group_name}"

	def validate(self):
		if self.shared:
			self.company = None

		if cint(self.priority) < 1:
			frappe.throw(_("Priority must be 1 or higher. 1 is the most important tier."))
		# A group that is never trimmed never falls short, so the three fields
		# that describe what happens when it does mean nothing here. on_shortfall
		# was already cleared; the other two were left holding stale answers that
		# the form hides but a reader of the record still sees.
		if not self.reducible:
			self.on_shortfall = None
			self.tie_breaker = None
			self.catch_up_order = None
		elif self.on_shortfall == "Waive":
			# Waived means the shortfall is let go, so there is no balance left
			# to catch up on later. Catch-up order only applies to Carry Forward.
			self.catch_up_order = None

	def on_trash(self):
		used = frappe.get_all(
			"Deduction Priority Detail",
			filters={"deduction_group": self.name},
			fields=["parent"],
			limit=1,
		)
		if used:
			frappe.throw(
				_("{0} is still assigned to components in Deduction Priority {1}.")
				.format(frappe.bold(self.group_name), used[0].parent)
			)
