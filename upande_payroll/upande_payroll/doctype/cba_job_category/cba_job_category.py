# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class CBAJobCategory(Document):
	"""A job category a CBA pay table or an employee record can point to.

	Was a Select on Employee, with the categories hardcoded into its options -
	changing them meant a developer editing code. A Link to a doctype lets HR
	add a new category from the list view like any other record.
	"""

	def on_trash(self):
		used_by_employee = frappe.get_all(
			"Employee", filters={"job_category": self.name}, limit=1
		)
		used_by_pay_table = frappe.get_all(
			"CBA Pay Table",
			or_filters=[["job_category", "=", self.name], ["promotes_to", "=", self.name]],
			limit=1,
		)
		if used_by_employee or used_by_pay_table:
			frappe.throw(_(
				"{0} is still in use on an Employee record or a CBA pay table and cannot be deleted."
			).format(frappe.bold(self.name)))
