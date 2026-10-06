# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

"""Employee Tax Exemption Proof Submission, repurposed for Kenya.

HRMS built this doctype for India's annual declare-then-prove cycle - a
Payroll Period, several expense categories in one submission, House Rent
Allowance math baked into its own validate(). None of that applies to a KRA
certificate (Persons with Disability being the one in use today): one
certificate, one category, valid for years rather than a single payroll
period. What IS genuinely reusable is the plumbing - a submittable document,
permissions already scoped to HR User/HR Manager, and a per-row ``attach_proof``
file field - so this reads a submission the same shape HRMS already built.

The certificate number and the file itself stay on the submission - nothing
duplicates them onto Employee, since nothing in the PAYE calculation reads
either. Only `custom_tax_exemption_category` and `custom_tax_exemption_valid_until`
are mirrored there, because kenya_statutory_calculator.py reads them on every
single payslip, in the same query that already reads the NSSF/SHIF opt-outs -
a live join against this doctype for every employee on every payroll run is a
cost Employee's own row doesn't have to pay just to know WHETHER a certificate
applies.

The row's own Actual Amount is what actually gets exempted - KRA's Maximum
Exemption Amount (on the Category) is a ceiling that amount can never
exceed, not the figure itself: a certificate grants up to 150,000, but
nothing requires every case to be given the full amount. Unlike category and
validity, the amount is NOT mirrored onto Employee - it is read live off the
submission itself (get_live_approved_amount, below) at the point the
calculator already knows a certificate applies, rather than duplicating a
second number that could drift out of sync with the submission that is its
only source of truth.

Wired via hooks.py's doc_events, not written into the doctype's own
controller - this behaviour belongs to upande_payroll, not to a file HRMS
ships and could overwrite on update.
"""

import frappe
from frappe import _
from frappe.utils import flt


def sync_to_employee(doc, method=None):
	rows = [r for r in doc.tax_exemption_proofs if r.exemption_category]
	if not rows:
		frappe.msgprint(
			_("No exemption category was set on any row, so nothing was applied to {0}.")
			.format(doc.employee),
			title=_("Tax Exemption Not Applied"), indicator="orange",
		)
		return

	categories = {r.exemption_category for r in rows}
	if len(categories) > 1:
		frappe.msgprint(
			_("More than one exemption category is on this submission ({0}). Only {1} was "
			  "applied to {2} - split the others into their own submission.").format(
				", ".join(sorted(categories)), rows[0].exemption_category, doc.employee,
			),
			title=_("Only the First Category Applied"), indicator="orange",
		)

	_recompute(doc.employee)


def unsync_from_employee(doc, method=None):
	_recompute(doc.employee)


def _recompute(employee):
	"""Whatever is live for this employee right now - the most recently
	submitted, not-cancelled Proof Submission that actually names a category.
	Re-run after every submit and every cancel, so a renewal naturally
	replaces an expiring one and cancelling an old submission falls back to
	whatever is still live, with nothing to track in between.

	``child.idx asc`` matches sync_to_employee()'s own rows[0] (the first
	child row with a category, in the same idx order) - without it, a
	submission with more than one category warned about there could sync a
	different row here than the one that warning named.
	"""
	current = frappe.db.sql(
		"""
		select parent.custom_valid_until, child.exemption_category, child.amount
		from `tabEmployee Tax Exemption Proof Submission` parent
		join `tabEmployee Tax Exemption Proof Submission Detail` child
			on child.parent = parent.name
		where parent.employee = %s
			and parent.docstatus = 1
			and child.exemption_category is not null
			and child.exemption_category != ''
		order by parent.creation desc, child.idx asc
		limit 1
		""",
		employee, as_dict=True,
	)

	frappe.db.set_value(
		"Employee", employee,
		{
			"custom_tax_exemption_category": current[0].exemption_category if current else None,
			"custom_tax_exemption_valid_until": current[0].custom_valid_until if current else None,
		},
		update_modified=False,
	)


def get_live_approved_amount(employee, category):
	"""The Actual Amount on whatever submission is currently live for this
	employee and category - read fresh at calculation time rather than
	trusting a mirrored figure that could go stale between a submission's
	amendment and the next payroll run.

	None (not 0) means no live submission was found for this category -
	kenya_statutory_calculator.py falls back to the category's own Maximum
	Exemption Amount in that case; a submission whose Actual Amount is
	genuinely 0 is indistinguishable from one that left it blank, since
	Currency columns are NOT NULL DEFAULT 0 at the DB level either way - both
	read back as 0.0, and both fall back to the ceiling the same as no
	submission at all.
	"""
	current = frappe.db.sql(
		"""
		select child.amount
		from `tabEmployee Tax Exemption Proof Submission` parent
		join `tabEmployee Tax Exemption Proof Submission Detail` child
			on child.parent = parent.name
		where parent.employee = %(employee)s
			and parent.docstatus = 1
			and child.exemption_category = %(category)s
		order by parent.creation desc, child.idx asc
		limit 1
		""",
		{"employee": employee, "category": category}, as_dict=True,
	)
	return flt(current[0].amount) if current and flt(current[0].amount) else None
