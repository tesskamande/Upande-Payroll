# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt

# What an employee can be grouped by. The label is what the filter shows; the
# value is the Employee field the figures are bucketed on. Kept here rather than
# hardcoded into the columns so the same report serves a company split by farm,
# by department or not split at all.
GROUP_FIELDS = {
	"Department": "department",
	"Designation": "designation",
	"Employee Grade": "grade",
	"Branch": "branch",
	"Employment Type": "employment_type",
}

DOCSTATUS = {"Draft": 0, "Submitted": 1, "Cancelled": 2}

UNASSIGNED = "Unassigned"


def execute(filters=None):
	filters = frappe._dict(filters or {})

	if filters.from_date and filters.to_date and filters.from_date > filters.to_date:
		frappe.throw(_("From Date cannot be after To Date"))

	slips = _get_slips(filters)
	if not slips:
		return _columns([], filters), []

	buckets = _bucket_by_group(slips, filters)
	earnings, deductions, employer = _totals(slips, buckets, filters)

	groups = _groups_present(buckets, earnings, deductions, employer)
	return _columns(groups, filters), _rows(groups, earnings, deductions, employer, filters)


# ----------------------------------------------------------------------

def _get_slips(filters):
	conditions = ["ss.docstatus = %(docstatus_value)s"]
	params = dict(filters)
	params["docstatus_value"] = DOCSTATUS.get(filters.docstatus, 1)

	for field, clause in (
		("company", "ss.company = %(company)s"),
		("from_date", "ss.start_date >= %(from_date)s"),
		("to_date", "ss.end_date <= %(to_date)s"),
		("department", "ss.department = %(department)s"),
		("employee", "ss.employee = %(employee)s"),
	):
		if filters.get(field):
			conditions.append(clause)

	return frappe.db.sql(
		"""
		SELECT ss.name, ss.employee
		FROM `tabSalary Slip` ss
		WHERE {conditions}
		""".format(conditions=" AND ".join(conditions)),
		params,
		as_dict=True,
	)


def _bucket_by_group(slips, filters):
	"""{salary slip: the column its figures belong in}."""
	group_field = GROUP_FIELDS.get(filters.group_by)
	if not group_field:
		return {s.name: _("Total") for s in slips}

	employees = list({s.employee for s in slips})
	values = frappe.get_all(
		"Employee",
		filters={"name": ("in", employees)},
		fields=["name", group_field],
	)
	by_employee = {e.name: (e.get(group_field) or UNASSIGNED) for e in values}
	return {s.name: by_employee.get(s.employee, UNASSIGNED) for s in slips}


def _totals(slips, buckets, filters):
	"""Sum each component into its column, split earnings / deductions / employer.

	Everything carrying do_not_include_in_total stays out of the employee's net
	pay, but that one flag covers two very different things: what the employer
	pays on top, and non-cash items that are only there to be taxed or relieved
	- a car benefit, an insurance premium. Only the first is a cost to the
	company, so the employer section keys off custom_is_employer_contribution
	rather than the flag. The rest is left out entirely; counting a car benefit
	as employer cash would overstate the payroll.

	do_not_include_in_total is read from the payslip row, not from the component
	master. The row is what the payslip actually used, and the master can have
	been changed since - flag a component today and every register ever printed
	would silently stop reconciling to the payslips it came from.
	"""
	rows = frappe.db.sql(
		"""
		SELECT sd.parent, sd.parentfield, sd.salary_component, sd.amount,
			sd.do_not_include_in_total, sc.custom_is_employer_contribution
		FROM `tabSalary Detail` sd
		INNER JOIN `tabSalary Component` sc ON sc.name = sd.salary_component
		WHERE sd.parent IN %(slips)s
			AND sd.parenttype = 'Salary Slip'
			AND sd.parentfield IN ('earnings', 'deductions')
		""",
		{"slips": [s.name for s in slips]},
		as_dict=True,
	)

	earnings, deductions, employer = {}, {}, {}
	for row in rows:
		group = buckets.get(row.parent)
		if row.custom_is_employer_contribution:
			if not filters.include_employer_contributions:
				continue
			target = employer
		elif row.do_not_include_in_total:
			continue
		else:
			target = earnings if row.parentfield == "earnings" else deductions

		target.setdefault(row.salary_component, {})
		target[row.salary_component][group] = (
			target[row.salary_component].get(group, 0.0) + flt(row.amount)
		)

	return earnings, deductions, employer


def _groups_present(buckets, *totals):
	"""Only show columns that actually carry a figure."""
	used = set()
	for section in totals:
		for amounts in section.values():
			used.update(amounts)
	if not used:
		used = set(buckets.values())
	return sorted(used, key=lambda g: (g == UNASSIGNED, g))


# ----------------------------------------------------------------------

def _columns(groups, filters):
	columns = [{
		"fieldname": "component",
		"label": _("Component"),
		"fieldtype": "Data",
		"width": 260,
	}]
	for group in groups:
		columns.append({
			"fieldname": frappe.scrub(group),
			"label": group,
			"fieldtype": "Currency",
			"options": "currency",
			"width": 160,
		})
	if len(groups) > 1:
		columns.append({
			"fieldname": "total",
			"label": _("Total"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 160,
		})
	return columns


def _rows(groups, earnings, deductions, employer, filters):
	rows = []
	show_total = len(groups) > 1

	def section(title, totals):
		"""One block: a heading, its components, then its own total."""
		rows.append({"component": title, "is_group": 1})
		running = {}
		for component in sorted(totals):
			amounts = totals[component]
			row = {"component": component}
			line = 0.0
			for group in groups:
				value = flt(amounts.get(group, 0.0))
				row[frappe.scrub(group)] = value or None
				running[group] = running.get(group, 0.0) + value
				line += value
			if show_total:
				row["total"] = line or None
			rows.append(row)
		return running

	gross = section(_("EARNINGS"), earnings)
	rows.append(_summary(_("Gross Pay"), gross, groups, show_total))
	rows.append({})

	# Statutory and voluntary deductions the employee actually bears.
	taken = section(_("DEDUCTIONS"), deductions)
	rows.append(_summary(_("Total Deductions"), taken, groups, show_total))
	rows.append({})

	net = {g: flt(gross.get(g, 0.0)) - flt(taken.get(g, 0.0)) for g in groups}
	rows.append(_summary(_("Net Pay"), net, groups, show_total))

	if filters.include_employer_contributions and employer:
		rows.append({})
		paid = section(_("EMPLOYER CONTRIBUTIONS"), employer)
		rows.append(_summary(_("Total Employer Contributions"), paid, groups, show_total))
		rows.append({})
		cost = {g: flt(gross.get(g, 0.0)) + flt(paid.get(g, 0.0)) for g in groups}
		rows.append(_summary(_("Total Cost to Company"), cost, groups, show_total))

	return rows


def _summary(label, amounts, groups, show_total):
	row = {"component": label, "is_group": 1}
	line = 0.0
	for group in groups:
		value = flt(amounts.get(group, 0.0))
		row[frappe.scrub(group)] = value or None
		line += value
	if show_total:
		row["total"] = line or None
	return row
