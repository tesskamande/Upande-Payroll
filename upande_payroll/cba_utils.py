import frappe
from frappe.utils import flt


def validate_basic_pay_against_cba(doc, method=None):
	"""Track Previous Base Pay whenever Basic Pay changes, and block saving an
	Employee whose Basic Pay or Base Pay is below the CBA minimum for their
	Job Category."""
	before_save = doc.get_doc_before_save()
	if before_save and flt(before_save.basic_pay) != flt(doc.basic_pay):
		doc.previous_base_pay = before_save.basic_pay

	if not doc.job_category:
		return

	minimum = get_cba_minimum(doc.job_category)
	if minimum is None:
		return

	if flt(doc.basic_pay) < minimum:
		frappe.throw(
			f"Basic Pay ({flt(doc.basic_pay):,.2f}) is below the CBA minimum of "
			f"{minimum:,.2f} for Job Category '{doc.job_category}'."
		)

	if flt(doc.base_pay) < minimum:
		frappe.throw(
			f"Base Pay ({flt(doc.base_pay):,.2f}) is below the CBA minimum of "
			f"{minimum:,.2f} for Job Category '{doc.job_category}'."
		)


def get_cba_minimum(job_category):
	"""Return the minimum Basic Pay for a Job Category from the latest submitted CBA."""
	cba_name = frappe.db.get_value(
		"CBA",
		{"docstatus": 1},
		"name",
		order_by="effective_start_date desc",
	)
	if not cba_name:
		return None

	return frappe.db.get_value(
		"CBA Pay Table",
		{"parent": cba_name, "job_category": job_category},
		"current_basic_pay",
	)


@frappe.whitelist()
def apply_cba_to_employees(cba_name):
	"""Bulk-apply a submitted CBA's pay table to every active employee in the
	CBA's Company whose Job Category matches a row in it. Employees below the
	category minimum are bumped up to it; everyone else gets the category's
	flat Increase Amount on top of their current Basic Pay. Base Pay, Basic
	Pay, and Previous Base Pay are all updated together.
	"""
	cba = frappe.get_doc("CBA", cba_name)
	if cba.docstatus != 1:
		frappe.throw("CBA must be submitted before it can be applied.")

	cba_map = {
		row.job_category: {
			"minimum": flt(row.current_basic_pay),
			"increase_amount": flt(row.increase_amount),
		}
		for row in cba.table_dqro
		if row.job_category
	}
	if not cba_map:
		frappe.throw("CBA Pay Table is empty. Add job categories before applying.")

	employees = frappe.get_all(
		"Employee",
		filters={
			"status": "Active",
			"company": cba.company,
			"job_category": ["in", list(cba_map.keys())],
		},
		fields=["name", "job_category", "basic_pay"],
	)

	updated = 0
	for emp in employees:
		rule = cba_map[emp.job_category]
		current_pay = flt(emp.basic_pay)
		if current_pay < rule["minimum"]:
			new_pay = rule["minimum"]
		else:
			new_pay = round(current_pay + rule["increase_amount"], 2)

		frappe.db.set_value(
			"Employee",
			emp.name,
			{
				"previous_base_pay": current_pay,
				"base_pay": new_pay,
				"basic_pay": new_pay,
			},
			update_modified=False,
		)
		updated += 1

	frappe.db.commit()

	if employees:
		message = f"Updated {updated} employee(s)."
	else:
		message = "No active employees found matching this CBA's job categories."

	return {"success": True, "message": message, "updated": updated}
