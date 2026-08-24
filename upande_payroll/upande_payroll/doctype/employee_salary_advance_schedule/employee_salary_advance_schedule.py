# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class EmployeeSalaryAdvanceSchedule(Document):
	"""One period of an advance's repayment plan.

	Kept deliberately dumb. Every figure on the row is derived by the parent
	Employee Salary Advance, which is the only thing that can see the whole
	plan and so the only thing that can keep it adding up.
	"""

	pass
