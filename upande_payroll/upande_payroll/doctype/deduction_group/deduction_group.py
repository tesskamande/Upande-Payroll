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

	def validate(self):
		if cint(self.priority) < 1:
			frappe.throw(_("Priority must be 1 or higher. 1 is the most important tier."))
		if not self.reducible:
			self.on_shortfall = None

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
