"""Turn Job Category from a Select into a Link, without losing what is set.

Employee.job_category and CBA Pay Table's job_category/promotes_to were a
Select with the categories hardcoded into the field's options - a colleague
asked for a Link instead, so HR can add a category from a list view rather
than a developer editing code.

A Select and a Link both just store a string, so the column itself needs no
migration - only somewhere for the Link to point. This creates a CBA Job
Category record for every value already on the site (the standard eight the
Select shipped with, plus anything else actually in use, in case a category
was added to the options list by hand at some point), before the fixture
turns the field into a Link. Runs before fixtures sync, so nothing is ever
looking for a category that does not exist yet.
"""

import frappe

STANDARD_CATEGORIES = [
	"Unskilled", "Semi-skilled", "Security", "Tractor Driver",
	"Clerk", "Small Truck Driver", "Large Truck Driver", "Artisan",
]


def execute():
	if not frappe.db.table_exists("Employee"):
		return

	frappe.reload_doctype("CBA Job Category")

	categories = set(STANDARD_CATEGORIES)

	if frappe.db.has_column("Employee", "job_category"):
		categories.update(
			v for v in frappe.db.sql_list(
				"SELECT DISTINCT job_category FROM `tabEmployee` WHERE job_category IS NOT NULL AND job_category != ''"
			)
		)

	if frappe.db.table_exists("CBA Pay Table"):
		for column in ("job_category", "promotes_to"):
			if frappe.db.has_column("CBA Pay Table", column):
				categories.update(
					v for v in frappe.db.sql_list(
						f"SELECT DISTINCT `{column}` FROM `tabCBA Pay Table` "
						f"WHERE `{column}` IS NOT NULL AND `{column}` != ''"
					)
				)

	for name in sorted(categories):
		if not frappe.db.exists("CBA Job Category", name):
			frappe.get_doc({
				"doctype": "CBA Job Category",
				"category_name": name,
			}).insert(ignore_permissions=True)

	frappe.db.commit()
