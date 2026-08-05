"""Shared engine behind the NSSF, SHIF, Housing Levy and HELB registers.

Each is a return with the same bones - who the employee is, what they earned,
what came off them, what the employer added - so they run on one builder. Four
copies of this query would drift apart the first time a levy changed.

Nothing here creates fields on Employee. The identity columns are always shown,
because the return asks for them whether or not the site has them filled in, and
the value is read from whatever the site already calls that field.
"""

import frappe
from frappe import _
from frappe.utils import flt

DOCSTATUS = {"Draft": 0, "Submitted": 1, "Cancelled": 2}

# One logical column, several possible field names. Sites differ on whether they
# prefix these, and on NHIF versus SHA, so every likely spelling is tried.
IDENTITY_SOURCES = {
	"national_id": ("national_id", "custom_national_id"),
	"kra_pin": ("tax_id", "custom_kra_pin", "kra_pin"),
	"nssf_no": ("nssf_no", "custom_nssf_number", "nssf_number"),
	"sha_no": ("sha_no", "shif_no", "custom_shif_number", "custom_nhif_number"),
	"voluntary": ("nssf_voluntary", "custom_is_nssf_voluntary"),
}


def build(filters, spec):
	"""Run one register. ``spec`` declares its columns and which components it covers."""
	filters = frappe._dict(filters or {})

	if filters.from_date and filters.to_date and filters.from_date > filters.to_date:
		frappe.throw(_("From Date cannot be after To Date"))

	columns = [
		{"fieldname": f, "label": label, "fieldtype": ftype, "width": width}
		for f, label, ftype, width in spec["columns"]
	]

	employee_components, employer_components = _components(filters, spec)
	if not employee_components and not employer_components:
		frappe.msgprint(
			_("No salary component is set for {0}, so there is nothing to report.")
			.format(spec["title"]),
			indicator="orange",
		)
		return columns, []

	sources = _identity_sources(spec)
	slips = _slips(filters, sources)
	if not slips:
		return columns, []

	return columns, _rows(filters, slips, sources, spec,
						  employee_components, employer_components)


# ----------------------------------------------------------------------

def _identity_sources(spec):
	"""{column: the Employee field it reads, or None if the site hasn't got one}."""
	meta = frappe.get_meta("Employee")
	wanted = [f for f, _l, _t, _w in spec["columns"] if f in IDENTITY_SOURCES]
	return {
		key: next((f for f in IDENTITY_SOURCES[key] if meta.has_field(f)), None)
		for key in wanted
	}


def _components(filters, spec):
	"""Which components make up this levy.

	Statutory ones come from the app's own map, so renaming one follows through.
	HELB and anything like it is a company's own deduction, so the report asks
	which component rather than guessing at a name.
	"""
	if spec.get("component_filter"):
		from upande_payroll.setup import HELB_COMPONENT

		chosen = filters.get("salary_component") or HELB_COMPONENT
		return ({chosen} if frappe.db.exists("Salary Component", chosen) else set()), set()

	from upande_payroll.kenya_statutory_calculator import get_statutory_components

	comp = get_statutory_components()
	return (
		{comp[k] for k in spec.get("employee_keys", ()) if comp.get(k)},
		{comp[k] for k in spec.get("employer_keys", ()) if comp.get(k)},
	)


def _slips(filters, sources):
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

	# A column the site has no field for still appears; it just selects NULL.
	extra = "".join(
		", e.{0} AS {1}".format(field, key) if field else ", NULL AS {0}".format(key)
		for key, field in sources.items()
	)

	return frappe.db.sql(
		"""
		SELECT ss.name, ss.employee, ss.employee_name, ss.gross_pay,
			e.employee_number, e.first_name, e.middle_name, e.last_name{extra}
		FROM `tabSalary Slip` ss
		INNER JOIN `tabEmployee` e ON e.name = ss.employee
		WHERE {conditions}
		ORDER BY e.employee_number ASC, ss.employee ASC
		""".format(extra=extra, conditions=" AND ".join(conditions)),
		params,
		as_dict=True,
	)


def _rows(filters, slips, sources, spec, employee_components, employer_components):
	config = _company_config(filters.company)
	details = frappe.db.sql(
		"""
		SELECT parent, salary_component, amount
		FROM `tabSalary Detail`
		WHERE parent IN %(slips)s AND parenttype = 'Salary Slip'
		""",
		{"slips": [s.name for s in slips]},
		as_dict=True,
	)

	amounts = {}
	for row in details:
		amounts.setdefault(row.parent, []).append(row)

	# One line per employee. Several payslips in the range add together rather
	# than appearing separately, which a return would otherwise double count.
	by_employee = {}
	for slip in slips:
		line = by_employee.get(slip.employee)
		if line is None:
			line = {
				"employee_number": slip.employee_number,
				"employee_name": slip.employee_name,
				"full_name": slip.employee_name,
				"last_name": slip.last_name,
				"other_name": " ".join(
					n for n in (slip.middle_name, slip.first_name) if n
				),
				"gross_pay": 0.0,
				"gross_salary": 0.0,
				"basic_salary": 0.0,
				"member_contribution": 0.0,
				"employer_contribution": 0.0,
			}
			for key in sources:
				value = slip.get(key)
				line[key] = 1 if (key == "voluntary" and value in (1, "Yes", "yes")) else value
			if "voluntary" in sources and line.get("voluntary") not in (0, 1):
				line["voluntary"] = 0
			by_employee[slip.employee] = line

		gross = flt(slip.gross_pay)

		for row in amounts.get(slip.name, []):
			component, amount = row.salary_component, flt(row.amount)
			if component == config["basic_component"]:
				line["basic_salary"] += amount
			if component in config["absence_components"]:
				# Time not worked comes off pay on a return, rather than sitting
				# among the deductions.
				gross -= amount
			if component in employee_components:
				line["member_contribution"] += amount
			elif component in employer_components:
				line["employer_contribution"] += amount

		line["gross_pay"] += gross
		line["gross_salary"] += gross

	rows = []
	for line in by_employee.values():
		if not (line["member_contribution"] or line["employer_contribution"]):
			continue
		line["total_nssf"] = line["member_contribution"]
		line["amount"] = line["member_contribution"]
		line["total_contribution"] = (
			line["member_contribution"] + line["employer_contribution"]
		)
		rows.append(line)
	return rows


def _company_config(company):
	basic, absence = None, set()
	if company and frappe.db.exists("Company Payroll Settings", company):
		settings = frappe.get_cached_doc("Company Payroll Settings", company)
		basic = settings.terminal_dues_basic_pay_component
		absence = {
			row.salary_component
			for row in (settings.statutory_income_component_mapping or [])
			if row.category == "Absence / Unpaid Deduction"
		}
	return {"basic_component": basic, "absence_components": absence}
