import frappe
from frappe import _


def resolve_bank_branch(doc, method=None):
	"""Let an import carry Bank Branch as a plain branch name.

	custom_bank_branch is a Link to Bank Branch, whose record name is
	"{bank} - {branch_name}" - not what a bulk import of employees carries,
	which is just the branch as the source data names it ("Moi Avenue"). An
	import maps that text onto the staging field custom_bank_branch_name
	instead, because resolving it straight into the Link field would be too
	late: Frappe checks a Link field's value against the database before any
	validate hook of ours runs, so a plain name there is rejected before we
	ever see it. The staging field is plain Data, so nothing checks it early.

	Resolved against the employee's own Bank, since the same branch name is
	reused across banks (Eldoret, Kitale) and Bank Branch's own Link filter
	already scopes the picker the same way.

	Never auto-creates: a branch name that matches nothing under that bank is
	a data problem - the wrong bank, a typo, or a branch not yet on file -
	and importing it as a new, codeless Bank Branch would hide that.
	"""
	value = (doc.custom_bank_branch_name or "").strip()
	if not value:
		return

	if not doc.bank_name:
		frappe.throw(
			_("Bank Branch Name {0} needs a Bank set first, to know which bank's branch it is.").format(
				frappe.bold(value)
			),
			title=_("No Bank"),
		)

	match = frappe.db.get_value(
		"Bank Branch", {"bank": doc.bank_name, "branch_name": value}, "name"
	)
	if not match:
		frappe.throw(
			_("{0} is not a Branch Name on file for {1}. Add it under Bank Branch first.").format(
				frappe.bold(value), frappe.bold(doc.bank_name)
			),
			title=_("Unknown Branch"),
		)
	doc.custom_bank_branch = match
	doc.custom_bank_branch_name = None
