# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class EmployeeSalaryAdvanceType(Document):
	"""The policy an Employee Salary Advance is granted under.

	Everything an advance needs to know that is a company decision rather than
	a property of the individual advance lives here: whether interest is
	charged, what component it is recovered as, and what limits apply. An
	advance reads its rules from this record and nothing is defaulted in code,
	so a policy nobody has stated does not quietly acquire one.
	"""

	def validate(self):
		self._validate_interest()
		self._validate_component()

	def _validate_interest(self):
		"""A rate is part of charging interest, not an optional extra.

		The form hides the rate until the method asks for one, but that is only
		the form. A type saved through the API or a fixture can arrive with a
		method of Per Annum Simple and no rate, and it would then look like a
		configured policy while behaving like an unconfigured one.
		"""
		if self.interest_method != "Per Annum Simple":
			# Nothing is charged, so a rate left lying about would only mislead.
			self.interest_rate = 0
			return

		if flt(self.interest_rate) <= 0:
			frappe.throw(
				_("Interest Method is {0}, so set an Interest Rate above zero. "
				  "For an advance that charges nothing, use {1}.").format(
					frappe.bold(_("Per Annum Simple")), frappe.bold(_("Interest Free"))
				)
			)

	def _validate_component(self):
		"""The instalment has to land on the payslip as a deduction."""
		component_type = frappe.db.get_value(
			"Salary Component", self.salary_component, "type"
		)
		if component_type != "Deduction":
			frappe.throw(
				_("Salary Component {0} is of type {1}. An advance is recovered "
				  "from pay, so it needs a Deduction component.").format(
					frappe.bold(self.salary_component), component_type or _("unset")
				)
			)
