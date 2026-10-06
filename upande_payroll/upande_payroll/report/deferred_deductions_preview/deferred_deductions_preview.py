# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

"""What a payroll run is about to defer, before it is submitted.

deduction_cap.py runs on every Salary Slip save, draft included - a run still
sitting in Draft already carries its deferred breakdown, on the slip's own
custom_deferred_deductions (ordinary components) and loans (loan instalments
and arrears) child tables. This reads both and puts them side by side, one
row per deferred item, so a Draft run can be checked before anyone commits to
submitting it rather than only after.

One row per item, not per slip: an employee with both a deferred Sacco
deduction and a deferred loan instalment in the same period gets two rows,
so neither is hidden inside a single lump sum.
"""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company"):
		frappe.throw(_("Please select a Company."))

	slips = get_slips(filters)
	rows = []
	for slip in slips:
		rows.extend(get_component_rows(slip))
		rows.extend(get_loan_rows(slip))

	return get_columns(), rows, None, None, get_summary(rows)


def get_slips(filters):
	conditions = {"company": filters.company, "custom_total_deferred_deductions": (">", 0)}
	if filters.get("docstatus") not in (None, ""):
		conditions["docstatus"] = frappe.utils.cint(filters.docstatus)
	else:
		# Draft and submitted both, by default - a run is most useful to check
		# before it is submitted, not only after.
		conditions["docstatus"] = ("in", [0, 1])
	if filters.get("payroll_entry"):
		conditions["payroll_entry"] = filters.payroll_entry
	if filters.get("from_date"):
		conditions["start_date"] = (">=", filters.from_date)
	if filters.get("to_date"):
		conditions["end_date"] = ("<=", filters.to_date)
	if filters.get("employee"):
		conditions["employee"] = filters.employee

	return frappe.get_all(
		"Salary Slip",
		filters=conditions,
		fields=["name", "employee", "employee_name", "docstatus", "end_date", "payroll_entry"],
		order_by="end_date, employee_name",
	)


def get_component_rows(slip):
	details = frappe.get_all(
		"Salary Slip Deferred Deduction",
		filters={"parent": slip.name},
		fields=["salary_component", "original_amount", "applied_amount", "deferred_amount", "treatment"],
	)
	rows = []
	for d in details:
		if flt(d.deferred_amount) <= 0:
			continue
		rows.append(_row(slip, _("Deduction"), d.salary_component,
						  d.original_amount, d.applied_amount, d.deferred_amount, d.treatment))
	return rows


def get_loan_rows(slip):
	details = frappe.get_all(
		"Salary Slip Loan",
		filters={"parent": slip.name},
		fields=["loan", "loan_product", "custom_scheduled_payment", "custom_brought_forward_amount",
				"custom_deferred_amount", "custom_arrears_deferred", "total_payment"],
	)
	rows = []
	for d in details:
		if flt(d.custom_deferred_amount) > 0:
			applied = flt(d.custom_scheduled_payment) - flt(d.custom_deferred_amount)
			rows.append(_row(slip, _("Loan Instalment"), d.loan_product,
							  d.custom_scheduled_payment, applied, d.custom_deferred_amount, None, loan=d.loan))
		if flt(d.custom_arrears_deferred) > 0:
			applied = flt(d.custom_brought_forward_amount) - flt(d.custom_arrears_deferred)
			rows.append(_row(slip, _("Loan Arrears"), d.loan_product,
							  d.custom_brought_forward_amount, applied, d.custom_arrears_deferred, None, loan=d.loan))
	return rows


def _row(slip, item_type, item, original, applied, deferred, treatment, loan=None):
	return {
		"employee": slip.employee,
		"employee_name": slip.employee_name,
		"salary_slip": slip.name,
		"status": _("Draft") if slip.docstatus == 0 else _("Submitted"),
		"end_date": slip.end_date,
		"item_type": item_type,
		"item": item,
		"loan": loan,
		"original_amount": round(flt(original), 2),
		"applied_amount": round(flt(applied), 2),
		"deferred_amount": round(flt(deferred), 2),
		"treatment": treatment,
	}


def get_summary(rows):
	total_deferred = sum(flt(r["deferred_amount"]) for r in rows)
	affected = len({r["salary_slip"] for r in rows})
	return [
		{"label": _("Payslips With a Deferral"), "value": affected, "datatype": "Int"},
		{
			"label": _("Total Deferred"), "value": round(total_deferred, 2),
			"datatype": "Currency", "indicator": "Orange" if total_deferred else "Green",
		},
	]


def get_columns():
	return [
		{"label": _("Employee"), "fieldname": "employee", "fieldtype": "Link",
		 "options": "Employee", "width": 130},
		{"label": _("Employee Name"), "fieldname": "employee_name", "fieldtype": "Data", "width": 160},
		{"label": _("Salary Slip"), "fieldname": "salary_slip", "fieldtype": "Link",
		 "options": "Salary Slip", "width": 140},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 90},
		{"label": _("Period End"), "fieldname": "end_date", "fieldtype": "Date", "width": 100},
		{"label": _("Type"), "fieldname": "item_type", "fieldtype": "Data", "width": 110},
		{"label": _("Component / Loan Product"), "fieldname": "item", "fieldtype": "Data", "width": 170},
		{"label": _("Loan"), "fieldname": "loan", "fieldtype": "Link", "options": "Loan", "width": 140},
		{"label": _("Original Amount"), "fieldname": "original_amount", "fieldtype": "Currency", "width": 130},
		{"label": _("Collected"), "fieldname": "applied_amount", "fieldtype": "Currency", "width": 110},
		{"label": _("Deferred"), "fieldname": "deferred_amount", "fieldtype": "Currency", "width": 110},
		{"label": _("Treatment"), "fieldname": "treatment", "fieldtype": "Data", "width": 110},
	]
