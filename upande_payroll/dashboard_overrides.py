# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

from frappe import _

# report_links.html gives every group its own col-md-4 and stacks the items
# inside one, so two items under a single label sit one above the other and only
# separate groups sit side by side. To get both registers in a row under one
# heading, the second group carries a blank label. It has to be a non-breaking
# space rather than "": frappe._ returns its argument untouched when falsy, and
# an empty span leaves .form-link-title with no height, which would lift the
# second link a line above the first.
BLANK_LABEL = " "


def get_dashboard_for_payroll_entry(data):
	"""Show the registers in Connections, beside the documents the run made.

	The form dashboard renders reports as well as linked documents, but it
	passes exactly one filter through: the document's own name, into
	`data["fieldname"]`. On Payroll Entry that is already `payroll_entry`.

	All three registers here carry a filter of that name, so each arrives
	scoped to the run. The statutory returns do not - they take a company and
	a date range, and would open on their own defaults, the current month
	rather than this payroll, which reads as filtered when it is not. Give
	one of those a payroll_entry filter and it belongs here too; until then
	it stays a toolbar button, where the dates can be passed explicitly.

	Three groups rather than two: report_links.html gives each its own
	col-md-4, and three of those fill one row exactly, so Bank Remittance
	sits beside the other two instead of stacking under either of them.
	"""
	data.setdefault("reports", []).extend(
		[
			{"label": _("Registers"), "items": ["Payroll Register"]},
			{"label": BLANK_LABEL, "items": ["Company Register"]},
			{"label": BLANK_LABEL, "items": ["Bank Remittance"]},
		]
	)
	return data
