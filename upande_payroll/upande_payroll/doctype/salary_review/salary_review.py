# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, fmt_money

# Currency comparisons at 2dp; anything under this is rounding noise, not a
# real difference between what was proposed and what actually moved.
TOLERANCE = 0.01


class SalaryReview(Document):
	"""A reasoned, dated record of one employee's basic pay changing.

	Submitting a review is the whole approval step - there is no separate
	workflow, matching how core HRMS's own Employee Promotion works. HR User
	can raise one; only HR Manager can submit it, so propose and approve stay
	two different people without a bespoke Workflow doctype to maintain.

	It is not the only way Basic Pay can change - Employee.basic_pay stays a
	plain editable field, so a direct edit still bypasses this entirely. That
	is a deliberate choice, not an oversight of this doctype: closing that gap
	was considered and turned down.

	The Salary Structure Assignment this raise eventually needs is never
	created here. An employee can have several structures running at once -
	a promotion moving them to a different one, someone kept on their current
	structure with only the figure changing - and there is no way to infer
	which of those a given review means from the numbers alone. That choice
	stays a person's, made when they create the assignment themselves; this
	document only offers a field to link it afterwards, for the audit trail.
	"""

	# ==================================================================
	# Document lifecycle
	# ==================================================================

	def validate(self):
		self._compute_proposed_pay()
		if flt(self.proposed_basic_pay) <= 0:
			frappe.throw(_("Proposed Basic Pay must be more than zero."))

	def on_submit(self):
		if self.auto_update_basic_pay:
			self._update_employee_basic_pay()

	def on_cancel(self):
		"""Put Basic Pay back, as far as it is still safe to.

		Only if nothing has moved it on since - if a later review, or a direct
		edit, already changed it again, restoring this review's "before" value
		would overwrite work that has nothing to do with this cancellation.

		Nothing to do here for the Salary Structure Assignment - this document
		never created one, so there is nothing of its own to undo. Whatever is
		linked in Salary Structure Assignment, if anything, is left exactly as
		it is; cancelling this review does not reach into a document it did
		not make.
		"""
		if not self.auto_update_basic_pay:
			return

		current = flt(frappe.db.get_value("Employee", self.employee, "basic_pay"))
		if abs(current - flt(self.proposed_basic_pay)) <= TOLERANCE:
			emp = frappe.get_doc("Employee", self.employee)
			emp.basic_pay = self.current_basic_pay
			emp.save(ignore_permissions=True)

	# ==================================================================
	# Pay change
	# ==================================================================

	def _compute_proposed_pay(self):
		"""All three - Increment Amount, Increment %, Proposed Basic Pay - stay
		mutually consistent, following from whichever one the review actually
		gave. No separate "change" fields: with the three kept in sync, one of
		them already says the size of the raise in whatever form was given.

		Which one leads is whichever the user (or the imported row) actually
		set, not a fixed order - otherwise editing Increment % on a document
		that already has a Proposed Basic Pay from an earlier edit would be
		silently ignored, proposed pay having non-zero precedence forever
		after the first save. A new document has no prior save to compare
		against, so the first save falls back to Proposed Basic Pay, then
		Amount, then Percent, in that order.
		"""
		current = flt(self.current_basic_pay)
		source = self._pay_field_just_set()

		if source == "proposed_basic_pay":
			self.increment_amount = flt(flt(self.proposed_basic_pay) - current, 2)
			self.increment_percent = flt((self.increment_amount / current) * 100, 2) if current else 0.0
		elif source == "increment_amount":
			self.proposed_basic_pay = flt(current + flt(self.increment_amount), 2)
			self.increment_percent = flt((flt(self.increment_amount) / current) * 100, 2) if current else 0.0
		elif source == "increment_percent":
			self.increment_amount = flt(current * flt(self.increment_percent) / 100.0, 2)
			self.proposed_basic_pay = flt(current + self.increment_amount, 2)
		elif not self.get_doc_before_save():
			# Genuinely new and empty - a document already saved once with none
			# of the three touched on this particular save has nothing to
			# recompute, which is not the same thing as having nothing at all.
			frappe.throw(
				_("Give Proposed Basic Pay, Increment Amount, or Increment % - one of the three."),
				title=_("No Pay Change Given"),
			)

	def _pay_field_just_set(self):
		"""Which of the three the user actually touched since the last save.

		A new document has nothing to compare against, so falls back to
		whichever of the three already carries a value - the shape a bulk
		import row takes, one column filled in and the other two blank.
		"""
		before = self.get_doc_before_save()
		fields = ("proposed_basic_pay", "increment_amount", "increment_percent")

		if before:
			for fieldname in fields:
				if flt(before.get(fieldname)) != flt(self.get(fieldname)):
					return fieldname
			return None

		for fieldname in fields:
			if flt(self.get(fieldname)):
				return fieldname
		return None

	# ==================================================================
	# Employee
	# ==================================================================

	def _update_employee_basic_pay(self):
		"""Through the document, not a raw field write - Employee has its own
		track_changes, and going around save() would leave this move out of
		that history."""
		emp = frappe.get_doc("Employee", self.employee)
		emp.basic_pay = self.proposed_basic_pay
		emp.save(ignore_permissions=True)

	# ==================================================================
	# Helpers
	# ==================================================================

	def _money(self, amount):
		currency = frappe.get_cached_value("Company", self.company, "default_currency")
		return fmt_money(flt(amount), currency=currency)
