# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

"""Which employee+day pairs a Payroll Entry's own "Validate Attendance" check
would flag - laid out as a calendar grid, the same shape as the Monthly
Attendance Sheet, so the gaps are something to scan across a row rather than
read out of 1500+ individual lines. Only employees with at least one gap get
a row at all - an employee with a clean month doesn't take up space here.

Every cell carries a label, not just the missing ones - a blank cell on its
own does not say whether the day was Present, a weekly off, or just never
looked at, and that distinction is exactly what somebody reading this report
needs. "X" means missing: no submitted Attendance record for that employee on
that date, and the date is not a holiday on whichever Holiday List applies to
them on it. Everything else uses the same P/A/HD/L/WFH/H/WO abbreviations as
HRMS's own Monthly Attendance Sheet, so a day explicitly marked Absent reads
as "A", not blank - it is marked, same as Present or On Leave are, just not
the thing this report exists to find. A day before someone joined or after
they left is "-": never theirs to mark, not a gap of any kind.

The holiday list is resolved through Holiday List Assignment, not the old
Employee.holiday_list field - HRMS hides that field in the UI unconditionally
(hrms/public/js/erpnext/employee.js, Employee.refresh), so nobody can actually
set it from the form any more, whatever the field still looks like in the
doctype's own schema. A Holiday List Assignment is date-ranged: an employee
can have no assignment, one covering the whole period, or several in
sequence, and the company's own assignment (also via Holiday List Assignment,
keyed on the company's name instead of an employee's) fills any date the
employee has none for - same precedence hrms.utils.holiday_list.
get_holiday_list_for_employee() uses, just resolved for every employee in the
Payroll Entry at once rather than one at a time.

hrms's own payroll_entry.get_employees_with_unmarked_attendance() has not
been updated to this yet - it still reads the hidden Employee.holiday_list
field, so a Holiday List Assignment currently has no effect on Payroll
Entry's own "Validate Attendance" check at all. This report no longer claims
to mirror it exactly for that reason.
"""

import frappe
from frappe import _
from frappe.utils import add_days, getdate

from upande_payroll.holiday_list_utils import (
	get_effective_holiday_ranges,
	get_holiday_dates,
	holiday_list_for_date,
)

DAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Same abbreviations and meanings as hrms's Monthly Attendance Sheet
# (hrms/hr/report/monthly_attendance_sheet/monthly_attendance_sheet.py,
# status_map), so a label here means the same thing it does there.
STATUS_ABBR = {
	"Present": "P",
	"Absent": "A",
	"Half Day": "HD",
	"On Leave": "L",
	"Work From Home": "WFH",
}


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
	holiday_ranges = get_effective_holiday_ranges(employees, entry.company, entry.start_date, entry.end_date)
	holiday_dates = get_holiday_dates(holiday_ranges, entry.start_date, entry.end_date)
	attendance_status = get_attendance_status(employees, entry.start_date, entry.end_date)

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
		ranges = holiday_ranges.get(employee, [])
		marked = attendance_status.get(employee, {})

		row = {
			"employee": employee,
			"employee_name": details.employee_name,
			"department": details.department,
		}
		missing_days = 0
		for d in period_dates:
			fieldname = d.strftime("%d-%m-%Y")
			if d < start_date or d > end_date:
				row[fieldname] = "-"
				continue

			status = marked.get(d)
			if status:
				row[fieldname] = STATUS_ABBR.get(status, status)
				continue

			holiday_list = holiday_list_for_date(ranges, d)
			is_weekly_off = holiday_dates.get(holiday_list, {}).get(d) if holiday_list else None
			if is_weekly_off is not None:
				row[fieldname] = "WO" if is_weekly_off else "H"
				continue

			row[fieldname] = "X"
			missing_days += 1

		if not missing_days:
			continue
		row["missing_days"] = missing_days
		rows.append(row)

	rows.sort(key=lambda r: (-r["missing_days"], r["employee"]))
	return columns, rows, get_message(), None, get_summary(rows)


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
		fields=["name", "employee_name", "department", "date_of_joining", "relieving_date"],
	)
	return {row.name: row for row in rows}


def get_attendance_status(employees, start_date, end_date):
	rows = frappe.get_all(
		"Attendance",
		filters={
			"employee": ["in", employees],
			"attendance_date": ["between", [start_date, end_date]],
			"docstatus": 1,
		},
		fields=["employee", "attendance_date", "status"],
	)
	status_by_employee = {}
	for row in rows:
		status_by_employee.setdefault(row.employee, {})[getdate(row.attendance_date)] = row.status
	return status_by_employee


def get_message():
	legend = {
		"X": ("Missing", "red"), "P": ("Present", "green"), "A": ("Absent", "red"),
		"HD": ("Half Day", "orange"), "L": ("On Leave", "#3187D8"),
		"WFH": ("Work From Home", "#914EE3"), "H": ("Holiday", "#878787"),
		"WO": ("Weekly Off", "#878787"), "-": ("Not Yet Employed / Left", "#878787"),
	}
	return "".join(
		f"""<span style='border-left: 2px solid {colour}; padding-right: 12px;
			padding-left: 5px; margin-right: 3px;'>{_(label)} - {abbr}</span>"""
		for abbr, (label, colour) in legend.items()
	)


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
