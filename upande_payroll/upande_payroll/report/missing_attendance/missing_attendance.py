# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

"""Which employee+day pairs a Payroll Entry's own "Validate Attendance" check
would flag - laid out as a calendar grid, the same shape as the Monthly
Attendance Sheet, so the gaps are something to scan across a row rather than
read out of 1500+ individual lines. Only employees with at least one gap get
a row at all - an employee with a clean month doesn't take up space here.

"Missing" here means exactly what hrms's own
payroll_entry.get_employees_with_unmarked_attendance() means by it: no
submitted Attendance record for that employee on that date, and the date is
not a holiday on whichever Holiday List applies to that employee (their own,
or the company's default). A day explicitly marked Absent is not missing -
it is marked, same as Present or On Leave are.
"""

import frappe
from frappe import _
from frappe.utils import add_days, getdate

DAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("payroll_entry"):
		frappe.throw(_("Please select a Payroll Entry."))

	entry = frappe.get_doc("Payroll Entry", filters.payroll_entry)
	period_dates = get_dates_in_period(entry.start_date, entry.end_date)
	columns = get_columns(period_dates)

	employees = [row.employee for row in entry.employees]
	if not employees:
		return columns, [], None, None, get_summary([])

	emp_details = get_employee_details(employees)
	default_holiday_list = frappe.db.get_value(
		"Company", entry.company, "default_holiday_list", cache=True
	)
	holiday_dates = get_holiday_dates(emp_details, default_holiday_list, entry.start_date, entry.end_date)
	attendance_dates = get_attendance_dates(employees, entry.start_date, entry.end_date)

	rows = []
	for employee in employees:
		details = emp_details.get(employee)
		if not details:
			continue
		if filters.get("employee") and employee != filters.employee:
			continue
		if filters.get("department") and details.department != filters.department:
			continue

		start_date, end_date = get_employee_date_range(entry, details)
		holiday_list = details.holiday_list or default_holiday_list
		holidays = holiday_dates.get(holiday_list, set())
		marked = attendance_dates.get(employee, set())

		row = {
			"employee": employee,
			"employee_name": details.employee_name,
			"department": details.department,
		}
		missing_days = 0
		for d in period_dates:
			fieldname = d.strftime("%d-%m-%Y")
			if start_date <= d <= end_date and d not in holidays and d not in marked:
				row[fieldname] = "X"
				missing_days += 1
			else:
				row[fieldname] = ""

		if not missing_days:
			continue
		row["missing_days"] = missing_days
		rows.append(row)

	rows.sort(key=lambda r: (-r["missing_days"], r["employee"]))
	return columns, rows, None, None, get_summary(rows)


def get_dates_in_period(start_date, end_date):
	dates = []
	d = getdate(start_date)
	end = getdate(end_date)
	while d <= end:
		dates.append(d)
		d = add_days(d, 1)
	return dates


def get_employee_date_range(entry, details):
	# Same clipping payroll_entry.get_payroll_dates_for_employee() applies -
	# a day before someone joined or after they left was never theirs to mark.
	start_date = getdate(entry.start_date)
	if details.date_of_joining and getdate(details.date_of_joining) > start_date:
		start_date = getdate(details.date_of_joining)

	end_date = getdate(entry.end_date)
	if details.relieving_date and getdate(details.relieving_date) < end_date:
		end_date = getdate(details.relieving_date)

	return start_date, end_date


def get_employee_details(employees):
	rows = frappe.get_all(
		"Employee",
		filters={"name": ["in", employees]},
		fields=["name", "employee_name", "department", "date_of_joining", "relieving_date", "holiday_list"],
	)
	return {row.name: row for row in rows}


def get_holiday_dates(emp_details, default_holiday_list, start_date, end_date):
	holiday_lists = {d.holiday_list or default_holiday_list for d in emp_details.values()}
	holiday_lists.discard(None)
	if not holiday_lists:
		return {}

	rows = frappe.get_all(
		"Holiday",
		filters={
			"parent": ["in", list(holiday_lists)],
			"holiday_date": ["between", [start_date, end_date]],
		},
		fields=["parent", "holiday_date"],
	)
	dates_by_list = {}
	for row in rows:
		dates_by_list.setdefault(row.parent, set()).add(getdate(row.holiday_date))
	return dates_by_list


def get_attendance_dates(employees, start_date, end_date):
	rows = frappe.get_all(
		"Attendance",
		filters={
			"employee": ["in", employees],
			"attendance_date": ["between", [start_date, end_date]],
			"docstatus": 1,
		},
		fields=["employee", "attendance_date"],
	)
	dates_by_employee = {}
	for row in rows:
		dates_by_employee.setdefault(row.employee, set()).add(getdate(row.attendance_date))
	return dates_by_employee


def get_summary(rows):
	total_missing = sum(r["missing_days"] for r in rows)
	return [
		{"label": _("Employees With a Gap"), "value": len(rows), "datatype": "Int",
		 "indicator": "Red" if rows else "Green"},
		{"label": _("Missing Days, Total"), "value": total_missing, "datatype": "Int",
		 "indicator": "Red" if total_missing else "Green"},
	]


def get_columns(period_dates):
	columns = [
		{"label": _("Employee"), "fieldname": "employee", "fieldtype": "Link",
		 "options": "Employee", "width": 130},
		{"label": _("Employee Name"), "fieldname": "employee_name", "fieldtype": "Data", "width": 160},
		{"label": _("Department"), "fieldname": "department", "fieldtype": "Link",
		 "options": "Department", "width": 140},
		{"label": _("Missing Days"), "fieldname": "missing_days", "fieldtype": "Int", "width": 95},
	]
	for d in period_dates:
		columns.append({
			"label": f"{d.day} {DAY_ABBR[d.weekday()]}",
			"fieldtype": "Data",
			"fieldname": d.strftime("%d-%m-%Y"),
			"width": 55,
		})
	return columns
