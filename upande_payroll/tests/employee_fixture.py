"""Insert an Employee for a check module, whatever the doctype currently demands.

upande_hr makes custom_business_unit, custom_farm and employee_category
mandatory on Employee. A payroll check module knows nothing about those fields
and should not have to: what it needs is an employee that saves.

Rather than name the fields - which would break again the next time an app on
the same site adds one - this fills whatever the doctype says right now is both
mandatory and a Link, by pointing at a record that already exists. Nothing is
created, so a check module never leaves masters behind, and a field the caller
set itself is left alone.
"""

import frappe


def fill_mandatory_links(doc):
	"""Point every unset mandatory Link at an existing record of its target."""
	for field in doc.meta.fields:
		if not (field.reqd and field.fieldtype == "Link" and field.options):
			continue
		if doc.get(field.fieldname):
			continue
		existing = frappe.db.get_value(field.options, {}, "name", order_by="creation asc")
		if existing:
			doc.set(field.fieldname, existing)
	return doc


def new_employee(**values):
	"""Return an inserted Employee built from `values`."""
	doc = frappe.get_doc(dict(doctype="Employee", **values))
	fill_mandatory_links(doc)
	return doc.insert(ignore_permissions=True)
