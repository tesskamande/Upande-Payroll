import frappe

# The statutory components the Kenya calculator writes to. Created if absent
# and then left alone - never overwritten.
#
# These are deliberately NOT shipped as fixtures. A fixture re-imports the whole
# document on every migrate, which wipes the per-company Salary Component Account
# rows (GL mappings are site data, not app definition) and reverts any local flag
# changes. Creating on demand gives every site the same starting set without ever
# clobbering what the company has configured since.
STATUTORY_COMPONENTS = [
	{"salary_component": "Employee NSSF Tier 1", "salary_component_abbr": "NSSF1E"},
	{"salary_component": "Employee NSSF Tier 2", "salary_component_abbr": "NSSF2E"},
	{"salary_component": "Social Health Insurance Fund", "salary_component_abbr": "SHIF",
	 "description": "Employee contribution only - the employer does not match SHIF."},
	{"salary_component": "Housing Levy", "salary_component_abbr": "AHLE"},
	{"salary_component": "Pay As You Earn", "salary_component_abbr": "IT",
	 "is_income_tax_component": 1},
	{"salary_component": "Employer NSSF Tier 1", "salary_component_abbr": "NSSF1R",
	 "custom_is_employer_contribution": 1},
	{"salary_component": "Employer NSSF Tier 2", "salary_component_abbr": "NSSF2R",
	 "custom_is_employer_contribution": 1},
	{"salary_component": "Employer Housing Levy", "salary_component_abbr": "AHLR",
	 "custom_is_employer_contribution": 1},
	{"salary_component": "NITA", "salary_component_abbr": "NITA",
	 "custom_is_employer_contribution": 1,
	 "description": "National Industrial Training Authority levy - employer-paid."},
	# Not worked out by the calculator the way the levies above are: the Higher
	# Education Loans Board sets each person's instalment, so the amount is
	# entered per employee. It ships anyway so the HELB return has something to
	# report on without every site inventing its own component name.
	{"salary_component": "HELB", "salary_component_abbr": "HELB",
	 "description": "Higher Education Loans Board repayment. The amount is set "
					"by HELB per employee, so enter it rather than expecting "
					"payroll to work it out."},
]

# The component the HELB return reports on. A site that calls it something else
# can point the report elsewhere; this is only the starting point.
HELB_COMPONENT = "HELB"


# Options a component can be tagged with for each KRA return. Tagging is what
# keeps the reports free of component names: a company can call its basic pay
# anything it likes as long as it is tagged Basic Salary here.
P9A_TYPES = [
	"", "Basic Salary", "Benefits NonCash", "Value of Quarters", "Total Gross Pay",
	"E1 Defined Contribution Retirement Scheme",
	"E2 Defined Contribution Retirement Scheme",
	"E3 Defined Contribution Retirement Scheme",
	"Owner Occupied Interest",
	"Retirement Contribution and Owner Occupied Interest",
	"Chargeable Pay", "Housing Levy", "SHIF", "Tax Charged",
	"Personal Relief", "Insurance Relief", "PAYE Tax",
]

P10A_TYPES = [
	"", "Basic Salary", "Housing Allowance", "Transport Allowance", "Leave Pay",
	"Overtime", "Directors Fee", "Lump Sum Payment", "Other Allowance",
	"Total Cash Pay", "Value of Car Benefit", "Value of Meals", "Value of Housing",
	"Value of other Benefit", "Other Non Cash Benefits", "Total Gross Pay",
	"30 Percent of Cash Pay", "Pension Contribution", "NSSF Contribution",
	"Actual Contribution", "Permissible Limit", "Mortgage Interest",
	"Affordable Housing Levy", "SHIF", "Amount of Benefit", "Taxable Pay",
	"Tax Payable", "Monthly Personal Relief", "Amount of Insurance",
	"PAYE Tax", "Self Assessed PAYE Tax",
]

# Deliberately not prefixed with custom_. Frappe only adds that prefix when a
# field is made through the UI without a fieldname; an app supplying its own
# fieldname keeps it.
#
# The employee identity fields a return needs - national_id, tax_id, nssf_no,
# sha_no - are deliberately NOT created here. Sites already carry their own, and
# the reports look for them rather than insisting on them, so a site that names
# them differently is not fought with.
STATUTORY_FIELDS = {
	# Union membership drives the dues formulas on the Salary Structure. The
	# usual Kenyan arrangement is that members pay dues and non-members pay an
	# agency fee at the same rate, so a formula needs to know which someone is:
	#
	#   Union Dues (COTU)    170 if union_member else 0
	#   Union Dues (KPAWU)   ((b_pay - LTD) * 0.02) if union_member else 0
	#   Agency Fees          ((b_pay - LTD) * 0.02) if not union_member else 0
	#
	# The rates stay in the structure where a company can see them; only who is
	# a member lives here.
	"Employee": [
		{"fieldname": "union_membership_section", "fieldtype": "Section Break",
		 "label": "Union Membership", "insert_after": "salary_mode",
		 "collapsible": 1},
		{"fieldname": "union_member", "fieldtype": "Check",
		 "label": "Union Member", "insert_after": "union_membership_section",
		 "description": "Tick if this employee belongs to the union. Non-members "
						"usually pay an agency fee instead of dues."},
		{"fieldname": "union", "fieldtype": "Data", "label": "Union",
		 "insert_after": "union_member", "depends_on": "eval:doc.union_member",
		 "description": "Which union, e.g. COTU or KPAWU. Useful when a company "
						"deals with more than one."},
	],
	"Salary Component": [
		{"fieldname": "p9a_tax_deduction_card_type", "fieldtype": "Select",
		 "label": "P9A Tax Deduction Card Type", "insert_after": "type",
		 "options": "\n".join(P9A_TYPES),
		 "description": "Where this component lands on the P9. Leave blank to keep it off."},
		{"fieldname": "p10a_tax_deduction_card_type", "fieldtype": "Select",
		 "label": "P10A Tax Deduction Card Type",
		 "insert_after": "p9a_tax_deduction_card_type",
		 "options": "\n".join(P10A_TYPES),
		 "description": "Where this component lands on the P10. Leave blank to keep it off."},
	],
}


def after_migrate():
	ensure_statutory_components()
	open_salary_structure_tables()
	ensure_statutory_fields()


def ensure_statutory_fields():
	"""The P9 and P10 tags, and the union membership a dues formula reads."""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields(STATUTORY_FIELDS)
	group_salary_tab()
	frappe.db.commit()


def group_salary_tab():
	"""Put this app's Employee fields under headings instead of a flat run.

	Without this they land one after another on the Salary tab in whatever order
	they were created, so a payroll clerk reads base pay, an opt-out and an
	expense account as one undifferentiated list.
	"""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	# Anchored to the end of the salary block HRMS already builds, not to
	# salary_mode: employee_advance_account is anchored there too, and two
	# fields claiming one slot leaves the loser floating to the bottom of the
	# form. The existing run is salary_mode > employee_advance_account >
	# salary_cb > payroll_cost_center, so ours picks up after it.
	ANCHOR = "payroll_cost_center"

	create_custom_fields({
		"Employee": [
			{"fieldname": "payroll_earnings_section", "fieldtype": "Section Break",
			 "label": "Earnings", "insert_after": ANCHOR},
			{"fieldname": "payroll_deductions_section", "fieldtype": "Section Break",
			 "label": "Deductions", "insert_after": "previous_base_pay",
			 "collapsible": 1},
		]
	})

	# Order matters more than the breaks themselves - a section only groups what
	# follows it, so the fields are pinned into the right run here.
	order = [
		"payroll_earnings_section", "job_category", "base_pay", "basic_pay",
		"previous_base_pay",
		"payroll_deductions_section", "custom_is_secondary_employment",
		"custom_opt_out_of_nssf", "custom_opt_out_of_shif",
		"custom_opt_out_of_housing_levy", "custom_salary_expense_account",
		"union_membership_section", "union_member", "union",
	]
	# idx as well as insert_after. Frappe walks custom fields in idx order and
	# resolves each one against what it has placed so far, so a field whose
	# anchor has not been reached yet drops to the bottom of the form. HRMS's
	# own custom fields sit at idx 0, so ours start well clear of them and climb
	# in the order they should read.
	previous = ANCHOR
	for position, fieldname in enumerate(order, start=100):
		name = frappe.db.get_value(
			"Custom Field", {"dt": "Employee", "fieldname": fieldname}, "name"
		)
		if not name:
			continue
		frappe.db.set_value(
			"Custom Field", name,
			{"insert_after": previous, "idx": position},
			update_modified=False,
		)
		previous = fieldname

	_splice_into_field_order(order, ANCHOR)
	frappe.clear_cache(doctype="Employee")


def _splice_into_field_order(order, anchor):
	"""Put our fields into the saved field order, if the site pins one.

	A field_order Property Setter lists the whole form and Frappe follows it
	literally, so anything missing from the list lands at the bottom however it
	is anchored. Customising the Employee form once is enough to create one, and
	from then on insert_after alone will not place a new field.
	"""
	setter = frappe.db.get_value(
		"Property Setter",
		{"doc_type": "Employee", "property": "field_order"},
		["name", "value"],
		as_dict=True,
	)
	if not setter:
		return

	fields = frappe.parse_json(setter.value) or []
	if not fields:
		return

	remaining = [f for f in fields if f not in order]
	if anchor not in remaining:
		return

	at = remaining.index(anchor) + 1
	frappe.db.set_value(
		"Property Setter", setter.name,
		"value", frappe.as_json(remaining[:at] + list(order) + remaining[at:]),
		update_modified=False,
	)


def open_salary_structure_tables():
	"""Let components be added to a Salary Structure after it is submitted.

	Without this, adding one component means cancel, amend and re-assign every
	employee on the structure. Payslips read the structure when they are built,
	so the new row reaches everyone without any assignment being touched.

	HRMS already allows `condition` and `formula` to be edited after submit, so
	this only extends the same idea to the tables themselves. The checks that a
	submitted save skips are re-run by salary_structure_utils.validate_after_submit.
	"""
	for fieldname in ("earnings", "deductions"):
		frappe.make_property_setter({
			"doctype": "Salary Structure",
			"fieldname": fieldname,
			"property": "allow_on_submit",
			"value": 1,
			"property_type": "Check",
		}, is_system_generated=False)

	frappe.clear_cache(doctype="Salary Structure")
	frappe.db.commit()


def ensure_statutory_components():
	for spec in STATUTORY_COMPONENTS:
		name = spec["salary_component"]
		if frappe.db.exists("Salary Component", name):
			continue

		doc = frappe.get_doc({
			"doctype": "Salary Component",
			"type": "Deduction",
			# Employer contributions stay type Deduction rather than the native
			# "Employer Contribution" type, and never statistical: both of those
			# hide the Accounts child table on the Salary Component form, leaving
			# the GL accounts unconfigurable. do_not_include_in_total on its own
			# is what keeps them out of the employee's net pay.
			"do_not_include_in_total": 1 if spec.get("custom_is_employer_contribution") else 0,
			"statistical_component": 0,
			"depends_on_payment_days": 0,
			**spec,
		})
		doc.insert(ignore_permissions=True)
		frappe.logger().info(f"upande_payroll: created Salary Component {name}")

	frappe.db.commit()
