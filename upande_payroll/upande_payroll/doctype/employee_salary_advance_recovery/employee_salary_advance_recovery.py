# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class EmployeeSalaryAdvanceRecovery(Document):
	"""One amount one salary slip collected against one period of an advance.

	The schedule's paid figures are derived from these rows rather than
	accumulated in place, which is what makes a cancelled slip reversible: its
	entries are removed and the schedule is recomputed from what is left. A
	running total on the row could not be unwound, because two slips can pay
	towards the same period and a single total cannot say how much each put in.
	"""

	pass
