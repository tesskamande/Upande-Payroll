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
	# The farm is read off the Employee like every other split here. custom_farm
	# holds the farm's own name, which is also what a Farm document is named by,
	# so the columns come out labelled the way the farms are known.
	"Farm": "custom_farm",
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
	return (
		_columns(groups, filters),
		_rows(groups, earnings, deductions, employer, filters, slips, buckets),
	)


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
		# Scoping to one run rather than a date range is what lets this report be
		# reached from Payroll Entry's Connections: the form dashboard passes the
		# document's own name into a filter of this name and nothing else.
		("payroll_entry", "ss.payroll_entry = %(payroll_entry)s"),
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


def _headcount(slips, buckets, groups, show_total):
	"""How many people each column stands for.

	Two farms at the same total mean different things at three employees and at
	thirty, and a farm that is five people short of last month is invisible in
	the money alone. Counted as distinct employees, not slips, so a range
	covering two months does not double everybody.
	"""
	seen = {}
	for slip in slips:
		seen.setdefault(buckets.get(slip.name), set()).add(slip.employee)

	row = {"component": _("Employees"), "is_group": 1, "is_headcount": 1}
	everyone = set()
	for group in groups:
		people = seen.get(group, set())
		row[frappe.scrub(group)] = len(people) or None
		everyone |= people
	if show_total:
		row["total"] = len(everyone) or None
	return row


def _deduction_groups(company):
	"""{component: (order, label)} from the company's own Deduction Priority.

	Statutory components are always grouped, because the app knows them itself.
	Beyond those, a company that has ranked nothing simply gets one Statutory
	group and everything else under Other - grouping is a reading aid, so an
	unconfigured company loses some of the aid rather than being given tiers
	nobody chose.
	"""
	from upande_payroll.kenya_statutory_calculator import get_statutory_components

	# Statutory first, and from the app's own component map rather than from
	# Deduction Priority. Priority ranks only what may give way under the two
	# thirds rule, and statutory never gives way - so keying the whole grouping
	# off Priority alone dropped PAYE, NSSF, SHIF and the Housing Levy into a
	# bucket called Other, which is the one thing a statutory register must not
	# call them.
	mapping = {
		name: (-1.0, _("Statutory"))
		for name in set(get_statutory_components().values())
	}

	priority = frappe.db.get_value("Deduction Priority", {"company": company}, "name")
	if not priority:
		return mapping

	for row in frappe.get_all(
		"Deduction Priority Detail",
		filters={"parent": priority},
		fields=["salary_component", "deduction_group"],
	):
		if not (row.salary_component and row.deduction_group):
			continue
		group = frappe.get_cached_value(
			"Deduction Group", row.deduction_group, ["group_name", "priority"], as_dict=True
		)
		if not group:
			continue
		if row.salary_component in mapping:
			continue
		mapping[row.salary_component] = (flt(group.priority), group.group_name or row.deduction_group)
	return mapping


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


def _rows(groups, earnings, deductions, employer, filters, slips, buckets):
	rows = []
	show_total = len(groups) > 1

	def section(title, totals):
		"""One block: a heading, its components, then its own total."""
		rows.append({"component": title, "is_group": 1, "is_heading": 1})
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

	def grouped(title, totals, component_groups):
		"""The deductions block, ordered by Deduction Group with a subtotal each.

		A flat list reads fine at seven components and not at all at thirty. The
		order is the company's own Deduction Priority - the same tiers the two
		thirds rule makes give way - so the register reads in the order the money
		is actually taken. Anything nobody has ranked falls to the end under its
		own subtotal rather than being dropped.
		"""
		rows.append({"component": title, "is_group": 1, "is_heading": 1})
		running = {}

		other = _("Other")
		by_group = {}
		for component in totals:
			order, label = component_groups.get(component, (float("inf"), other))
			by_group.setdefault((order, label), []).append(component)

		for key in sorted(by_group, key=lambda k: (k[0], k[1])):
			subtotal = {}
			for component in sorted(by_group[key]):
				amounts = totals[component]
				row = {"component": component}
				line = 0.0
				for group in groups:
					value = flt(amounts.get(group, 0.0))
					row[frappe.scrub(group)] = value or None
					running[group] = running.get(group, 0.0) + value
					subtotal[group] = subtotal.get(group, 0.0) + value
					line += value
				if show_total:
					row["total"] = line or None
				rows.append(row)
			rows.append(_summary(_("Total {0}").format(key[1]), subtotal, groups, show_total))

		return running

	gross = section(_("EARNINGS"), earnings)
	rows.append(_summary(_("Gross Pay"), gross, groups, show_total))
	rows.append({})

	# Statutory and voluntary deductions the employee actually bears.
	component_groups = _deduction_groups(filters.company)
	taken = (
		grouped(_("DEDUCTIONS"), deductions, component_groups)
		if component_groups
		else section(_("DEDUCTIONS"), deductions)
	)
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

	# Last, under the money it explains. A headcount at the top reads as though
	# it were part of the earnings; at the bottom it reads as what the column
	# stands for, which is what it is.
	rows.append({})
	rows.append(_headcount(slips, buckets, groups, show_total))

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
