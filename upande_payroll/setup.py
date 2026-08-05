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
]


def after_migrate():
	ensure_statutory_components()
	open_salary_structure_tables()


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
