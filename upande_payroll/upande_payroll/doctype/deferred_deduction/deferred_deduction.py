# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class DeferredDeduction(Document):
	"""One debt: an amount the 1/3 rule stopped a salary slip collecting.

	Kept as its own submittable document rather than a running balance on the
	salary slip so that the debt survives what happens to slips. Cancelling the
	slip that created it cancels the debt; cancelling a slip that recovered
	against it puts the balance back.
	"""

	def before_insert(self):
		if not self.balance_remaining:
			self.balance_remaining = flt(self.deferred_amount)

	def on_cancel(self):
		self.db_set("status", "Cancelled")

	def apply_recovery(self, salary_slip, amount, posting_date):
		"""Record that a later slip collected part or all of this debt."""
		amount = flt(amount, 2)
		if amount <= 0:
			return

		self.append("recoveries", {
			"salary_slip": salary_slip,
			"recovered_on": posting_date,
			"amount": amount,
		})
		self.balance_remaining = max(flt(self.balance_remaining) - amount, 0.0)
		self._sync_status()
		self.save(ignore_permissions=True)

	def reverse_recovery(self, salary_slip):
		"""Undo every recovery a given slip made, when that slip is cancelled."""
		kept, returned = [], 0.0
		for row in self.recoveries:
			if row.salary_slip == salary_slip:
				returned += flt(row.amount)
			else:
				kept.append(row)

		if not returned:
			return

		self.set("recoveries", kept)
		self.balance_remaining = min(
			flt(self.balance_remaining) + returned, flt(self.deferred_amount)
		)
		self._sync_status()
		self.save(ignore_permissions=True)

	def _sync_status(self):
		if flt(self.balance_remaining) <= 0.01:
			self.status = "Cleared"
		elif self.recoveries:
			self.status = "Partially Recovered"
		else:
			self.status = "Pending"


def get_outstanding(employee, company, before_slip=None):
	"""Open debts for an employee, oldest first so the longest-standing clears first.

	``before_slip`` excludes debts this very slip created, which matters when a
	slip is re-saved: its own deferrals must not be recovered by itself.
	"""
	filters = {
		"employee": employee,
		"company": company,
		"docstatus": 1,
		"balance_remaining": (">", 0),
	}
	if before_slip:
		filters["deferred_from"] = ("!=", before_slip)

	return frappe.get_all(
		"Deferred Deduction",
		filters=filters,
		fields=["name", "salary_component", "balance_remaining", "deferral_date",
				"deferred_from"],
		order_by="deferral_date asc, creation asc",
	)
