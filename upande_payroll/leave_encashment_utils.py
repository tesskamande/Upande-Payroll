import frappe
from frappe.utils import flt


class LeaveEncashmentMixin:
	"""Overrides core HRMS Leave Encashment behaviour so the encashment amount is
	driven by Company Payroll Settings (Basic Pay / divisor) instead of the
	Salary Structure Assignment's Leave Encashment Amount Per Day, and so a third
	payment mode - via Terminal Dues Settlement - is possible alongside core's
	own Salary Slip / Payment Entry modes.
	"""

	def set_salary_structure(self):
		# Core only needs a Salary Structure Assignment to read
		# leave_encashment_amount_per_day, which our own calculation doesn't use.
		settings = frappe.get_cached_doc("Company Payroll Settings", self.company)
		if settings.enable_leave_encashment_calculation:
			return
		return super().set_salary_structure()

	def set_encashment_amount(self):
		settings = frappe.get_cached_doc("Company Payroll Settings", self.company)
		if not settings.enable_leave_encashment_calculation:
			return super().set_encashment_amount()

		divisor = settings.leave_encashment_divisor or 26
		basic_pay = flt(frappe.db.get_value("Employee", self.employee, "basic_pay"))
		self.encashment_amount = flt((basic_pay / divisor) * flt(self.encashment_days), 2) if divisor else 0.0

	def on_submit(self):
		if self.get("custom_pay_via_terminal_dues"):
			# Skip core's create_gl_entries()/create_additional_salary() - a Terminal
			# Dues Settlement will pick this up and pay it as part of the exit settlement.
			if not self.leave_allocation:
				self.db_set("leave_allocation", self.get_leave_allocation().get("name"))
			self.set_encashed_leaves_in_allocation()
			self.create_leave_ledger_entry()
			return
		return super().on_submit()


def validate_leave_encashment(doc, method=None):
	if doc.pay_via_payment_entry and doc.get("custom_pay_via_terminal_dues"):
		frappe.throw(
			"Pay via Payment Entry and Pay via Terminal Dues Settlement are mutually "
			"exclusive - choose one mode of payment."
		)
