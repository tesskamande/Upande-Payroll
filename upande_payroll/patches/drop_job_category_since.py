"""Drop the Job Category Since column from Employee.

The field recorded when somebody entered their current job category, for
automatic promotion under a CBA. It turned out to be unnecessary: a progression
rule names the category it promotes FROM, so an employee already moved on no
longer matches it. The category is what prevents a second promotion, and service
is measured from the joining date.

Retiring the Custom Field takes it off the form but leaves the column behind -
Frappe never drops one - so this removes it for good.
"""

import frappe

DOCTYPE = "Employee"
COLUMN = "custom_job_category_since"


def execute():
	if not frappe.db.table_exists(DOCTYPE):
		return

	# The Custom Field goes first, or the next migrate would recreate the column
	# from a definition that is still sitting there.
	name = frappe.db.get_value("Custom Field", {"dt": DOCTYPE, "fieldname": COLUMN}, "name")
	if name:
		frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)

	if frappe.db.has_column(DOCTYPE, COLUMN):
		frappe.db.sql_ddl(f"ALTER TABLE `tab{DOCTYPE}` DROP COLUMN `{COLUMN}`")
		frappe.clear_cache(doctype=DOCTYPE)
