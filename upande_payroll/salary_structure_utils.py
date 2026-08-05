import frappe
from frappe import _


def validate_after_submit(doc, method=None):
	"""Re-run the component checks that a save on a submitted structure skips.

	Adding a component normally means cancel, amend, and re-assign every
	employee - painful when the only change is one new row that nothing else
	depends on. A Property Setter opens the earnings and deductions tables after
	submit, and payslips read the structure as it stands when they are built, so
	a new row reaches everyone without touching a single Salary Structure
	Assignment.

	What that costs is validate(): Frappe runs it on a draft save but not on a
	submitted one, so a new row would otherwise never be checked and never pick
	up its component's defaults. HRMS already re-sanitises formulas after submit
	(before_update_after_submit), so only the rest is missing, and it is
	deliberately the same list validate() runs.
	"""
	doc.set_missing_values()
	doc.validate_amount()
	doc.validate_component_based_on_tax_slab()
	doc.validate_payment_days_based_dependent_component()
	doc.validate_timesheet_component()
	doc.validate_formula_setup()

	_warn_about_removals(doc)


def _warn_about_removals(doc):
	"""Say so when a row disappears from a live structure.

	Already-submitted payslips keep whatever they were built with, but any draft
	slip recalculates on its next save and silently loses the component. Adding
	is the safe half of this; removing deserves to be noticed.
	"""
	before = frappe.get_all(
		"Salary Detail",
		filters={"parent": doc.name, "parenttype": "Salary Structure"},
		fields=["salary_component", "parentfield"],
	)
	if not before:
		return

	current = {(r.parentfield, r.salary_component)
			   for table in ("earnings", "deductions")
			   for r in doc.get(table) or []}
	removed = [r.salary_component for r in before
			   if (r.parentfield, r.salary_component) not in current]
	if removed:
		frappe.msgprint(
			_("Removed from {0}: {1}. Submitted payslips keep it, but any draft "
			  "payslip on this structure will drop it when it is next saved.")
			.format(doc.name, ", ".join(sorted(set(removed)))),
			title=_("Component Removed"),
			indicator="orange",
		)
