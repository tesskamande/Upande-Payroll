# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

"""Payroll Command Center backend.

Action-routed like the accounting dashboard this was modelled on: one
whitelisted surface (meta / trend / drill) instead of a method per widget, so
the command center's JS only ever talks to one place. Unlike that script this
is app code, not a Server Script, so it can have a real function per action
and one can call another directly.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, get_first_day, get_last_day, nowdate

ALLOWED_ROLES = ("System Manager", "HR Manager", "Payroll Manager")


def check_access():
	if frappe.session.user == "Guest":
		frappe.throw(_("Please log in to view the payroll dashboard."), frappe.PermissionError)
	if frappe.session.user != "Administrator" and not set(frappe.get_roles()) & set(ALLOWED_ROLES):
		frappe.throw(_("You need an HR or Payroll role to view this dashboard."), frappe.PermissionError)


@frappe.whitelist()
def meta():
	check_access()
	companies = frappe.get_all("Company", fields=["name", "default_currency as currency"], order_by="name")
	default_company = frappe.defaults.get_user_default("Company") or (companies[0].name if companies else "")
	employees = []
	if default_company:
		employees = frappe.get_all(
			"Employee",
			filters={"company": default_company, "status": ("in", ("Active", "Inactive", "Suspended"))},
			fields=["name", "employee_name"],
			order_by="employee_name",
			limit_page_length=0,
		)
	return {
		"companies": companies,
		"default_company": default_company,
		"employees": employees,
		"user": frappe.db.get_value("User", frappe.session.user, "full_name") or frappe.session.user,
	}


@frappe.whitelist()
def employees_for_company(company):
	check_access()
	return frappe.get_all(
		"Employee",
		filters={"company": company, "status": ("in", ("Active", "Inactive", "Suspended"))},
		fields=["name", "employee_name"],
		order_by="employee_name",
		limit_page_length=0,
	)


@frappe.whitelist()
def trend(company=None, employee=None, months=12):
	"""Monthly Gross/Net Pay, company-wide or for one employee, over the last
	``months`` months ending this month - bounded at both ends so future-dated
	test data (e.g. demo slips run ahead for testing) can't leak into the window."""
	check_access()
	months = cint(months) or 12
	company = company or frappe.defaults.get_user_default("Company")

	start = get_first_day(frappe.utils.add_months(nowdate(), -(months - 1)))
	end = get_last_day(nowdate())
	conditions = ["docstatus = 1", "start_date BETWEEN %(start)s AND %(end)s"]
	params = {"start": start, "end": end}
	if company:
		conditions.append("company = %(company)s")
		params["company"] = company
	if employee:
		conditions.append("employee = %(employee)s")
		params["employee"] = employee

	rows = frappe.db.sql(
		"""
		SELECT DATE_FORMAT(start_date, '%%Y-%%m-01') AS period,
		       SUM(gross_pay) AS gross, SUM(net_pay) AS net, COUNT(*) AS count
		FROM `tabSalary Slip`
		WHERE {conditions}
		GROUP BY period ORDER BY period
		""".format(conditions=" AND ".join(conditions)),
		params, as_dict=True,
	)
	for r in rows:
		r.gross = flt(r.gross)
		r.net = flt(r.net)

	return {"company": company, "employee": employee, "months": months, "rows": rows}


@frappe.whitelist()
def drill(period, company=None, employee=None):
	"""Who made up one month's total - the breakdown behind a clicked point."""
	check_access()
	filters = {
		"docstatus": 1,
		"start_date": ("between", [period, get_last_day(period)]),
	}
	if company:
		filters["company"] = company
	if employee:
		filters["employee"] = employee

	rows = frappe.get_all(
		"Salary Slip",
		filters=filters,
		fields=["name", "employee", "employee_name", "department", "gross_pay", "net_pay"],
		order_by="gross_pay desc",
		limit_page_length=200,
	)
	total_gross = sum(flt(r.gross_pay) for r in rows)
	total_net = sum(flt(r.net_pay) for r in rows)
	return {"period": period, "rows": rows, "total_gross": total_gross, "total_net": total_net}
