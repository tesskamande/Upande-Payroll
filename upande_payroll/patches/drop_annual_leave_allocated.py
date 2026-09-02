"""Drop the Annual Leave Allocated column from Salary Slip.

Set alongside the unattended-days fields to pro-rate a leaver's annual leave
entitlement, but nothing ever consumed it - no formula, no report, no other
part of the app read it back. Turned out to be scope nobody asked for.

Retiring the Custom Field takes it off the form but leaves the column behind -
Frappe never drops one - so this removes it for good.
"""

import frappe

DOCTYPE = "Salary Slip"
COLUMN = "custom_annual_leave_allocated"


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
