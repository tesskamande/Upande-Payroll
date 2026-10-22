"""Resolve which Holiday List covers a given employee on a given date, the
way HRMS itself now resolves it - through Holiday List Assignment, not the
Employee.holiday_list field. HRMS hides that field in the UI unconditionally
(hrms/public/js/erpnext/employee.js, Employee.refresh), so nobody can set it
from the form any more, whatever the field still looks like in the doctype's
own schema. HRMS's own payroll_entry.get_employees_with_unmarked_attendance()
has not been updated to match - it still reads the hidden field - so this
module exists to give this app's own callers (Missing Attendance,
PayrollEntryMixin's override of that same check) the resolution HRMS's UI
actually commits employees to.

A Holiday List Assignment is date-ranged: an employee can have no assignment,
one covering the whole period, or several in sequence, and the company's own
assignment (same doctype, keyed on the company's name instead of an
employee's) fills any date the employee has none for - this is exactly
hrms.utils.holiday_list.get_holiday_list_for_employee()'s own precedence,
just resolved for every employee at once instead of one at a time.
"""

from frappe.utils import getdate
from hrms.utils.holiday_list import (
	build_holiday_list_map,
	fill_employee_holiday_list_date_gaps_with_company_holiday_list,
)

import frappe


def get_effective_holiday_ranges(employees, company, start_date, end_date):
	"""{employee: [{holiday_list, from_date, to_date}, ...]} for every
	employee in ``employees``, clipped to [start_date, end_date]. A date
	covered by neither the employee's nor the company's assignment is left
	out of the result entirely - there is nothing to exclude it from being
	unmarked with.

	One query for every employee and the company together, since Holiday
	List Assignment keys both on the same assigned_to column."""
	start_date, end_date = getdate(start_date), getdate(end_date)
	ranges_by_assignee = build_holiday_list_map([*employees, company], start_date, end_date)
	company_ranges = ranges_by_assignee.get(company, [])

	return {
		employee: fill_employee_holiday_list_date_gaps_with_company_holiday_list(
			ranges_by_assignee.get(employee, []), company_ranges, start_date, end_date
		)
		for employee in employees
	}


def holiday_list_for_date(ranges, d):
	for r in ranges:
		if r["from_date"] <= d <= r["to_date"]:
			return r["holiday_list"]
	return None


def get_holiday_dates(holiday_ranges, start_date, end_date):
	"""{holiday_list: {date: is_weekly_off, ...}} for every Holiday List
	referenced anywhere in holiday_ranges, within [start_date, end_date]. A
	plain dict rather than a set so a caller can tell a weekly off from a
	named holiday - `in` still works for a caller that only wants membership."""
	holiday_lists = {r["holiday_list"] for ranges in holiday_ranges.values() for r in ranges}
	if not holiday_lists:
		return {}

	rows = frappe.get_all(
		"Holiday",
		filters={
			"parent": ["in", list(holiday_lists)],
			"holiday_date": ["between", [start_date, end_date]],
		},
		fields=["parent", "holiday_date", "weekly_off"],
	)
	dates_by_list = {}
	for row in rows:
		dates_by_list.setdefault(row.parent, {})[getdate(row.holiday_date)] = bool(row.weekly_off)
	return dates_by_list


def count_holidays_in_range(ranges, holiday_dates, start_date, end_date):
	"""How many days in [start_date, end_date] are a holiday under whichever
	Holiday List covers that specific day - a day-by-day walk rather than a
	single list's count, because the covering list can change mid-range."""
	from frappe.utils import add_days

	count = 0
	d = getdate(start_date)
	end = getdate(end_date)
	while d <= end:
		holiday_list = holiday_list_for_date(ranges, d)
		if holiday_list and d in holiday_dates.get(holiday_list, {}):
			count += 1
		d = add_days(d, 1)
	return count
