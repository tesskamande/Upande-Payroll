import frappe
from frappe import _
from frappe.utils import flt, getdate, now


def validate_basic_pay_against_cba(doc, method=None):
	"""Refuse to save an employee below the agreed rate for their Job Category.

	This is what makes applying an agreement a one-off act. Nobody can be
	entered under the scale afterwards, so a new starter does not need the whole
	agreement pushed out again to bring them into line - they are simply entered
	at the rate that already applies.
	"""
	if not doc.job_category:
		return

	minimum = get_cba_minimum(doc.job_category, doc.company)
	if minimum is None:
		return

	if flt(doc.basic_pay) < flt(minimum):
		frappe.throw(_(
			"Basic Pay of {0} is below the agreed rate of {1} for {2}. Enter at "
			"least the agreed rate."
		).format(
			frappe.format_value(flt(doc.basic_pay), {"fieldtype": "Currency"}),
			frappe.format_value(flt(minimum), {"fieldtype": "Currency"}),
			doc.job_category,
		))


def get_cba_minimum(job_category, company=None):
	"""The agreed rate for a Job Category under the agreement in force today.

	Dated on purpose: an agreement signed for next year must not hold up today's
	saves, and the rate that binds is the one currently running. Scoped to the
	employee's company too, so one company's scale never governs another's staff
	on a bench that carries more than one.
	"""
	filters = {"docstatus": 1, "effective_start_date": ("<=", getdate())}
	if company:
		filters["company"] = company

	cba = frappe.db.get_value(
		"CBA", filters, ["name", "applied_on"], as_dict=True,
		order_by="effective_start_date desc, creation desc",
	)
	if not cba:
		return None

	row = frappe.db.get_value(
		"CBA Pay Table",
		{"parent": cba.name, "job_category": job_category},
		["current_basic_pay", "new_basic_pay"],
		as_dict=True,
	)
	if not row:
		return None

	# Which of the two rates binds depends on whether the increase has actually
	# been paid out. Until the agreement is applied, everyone is still on the
	# old rate - holding them to the new one would refuse every save in the gap
	# between signing and applying. Once applied, the new rate is what everyone
	# is on and what a new starter must be entered at.
	if cba.applied_on:
		return flt(row.new_basic_pay) or flt(row.current_basic_pay)
	return flt(row.current_basic_pay)


@frappe.whitelist()
def apply_cba_to_employees(cba_name):
	"""Bulk-apply a submitted CBA's pay table to every active employee in the
	CBA's Company whose Job Category matches a row in it.

	Employees below the category minimum are lifted to it; everyone else gets
	the category's flat Increase Amount on top of their current Basic Pay.

	What someone was on before is not copied onto the Employee record - their
	salary slips already carry it, period by period, and a single field could
	only ever hold the last change.
	"""
	cba = frappe.get_doc("CBA", cba_name)
	if cba.docstatus != 1:
		frappe.throw(_("CBA must be submitted before it can be applied."))

	# Once only. Every press adds the increase again, and re-applying to pick up
	# a new starter would raise everyone else a second time. The minimum check on
	# the Employee form is what makes this safe: nobody can be entered below the
	# agreed rate, so there is nothing to catch up on later.
	if cba.applied_on:
		frappe.throw(_(
			"This agreement was applied on {0}. Applying it again would increase "
			"everyone a second time. A new employee is entered at the agreed rate "
			"directly - the form will not accept less."
		).format(frappe.format_value(cba.applied_on, {"fieldtype": "Datetime"})))

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
			{"basic_pay": new_pay},
			update_modified=False,
		)
		updated += 1

	frappe.db.set_value("CBA", cba.name, "applied_on", now(), update_modified=False)
	frappe.db.commit()

	if employees:
		message = f"Updated {updated} employee(s)."
	else:
		message = "No active employees found matching this CBA's job categories."

	return {"success": True, "message": message, "updated": updated}
