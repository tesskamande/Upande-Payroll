import frappe
from frappe import _

# The statutory components the Kenya calculator writes to. Created if absent
# and then left alone - never overwritten.
#
# These are deliberately NOT shipped as fixtures. A fixture re-imports the whole
# document on every migrate, which wipes the per-company Salary Component Account
# rows (GL mappings are site data, not app definition) and reverts any local flag
# changes. Creating on demand gives every site the same starting set without ever
# clobbering what the company has configured since.
STATUTORY_COMPONENTS = [
	{"salary_component": "Employee NSSF Tier 1", "salary_component_abbr": "NSSFTIER1"},
	{"salary_component": "Employee NSSF Tier 2", "salary_component_abbr": "NSSFTIER2"},
	{"salary_component": "Social Health Insurance Fund", "salary_component_abbr": "SHIF",
	 "description": "Employee contribution only - the employer does not match SHIF."},
	{"salary_component": "Housing Levy", "salary_component_abbr": "AHLE"},
	{"salary_component": "Pay As You Earn", "salary_component_abbr": "PAYE",
	 "is_income_tax_component": 1},
	{"salary_component": "Employer NSSF Tier 1", "salary_component_abbr": "NSSFTIER1E",
	 "custom_is_employer_contribution": 1},
	{"salary_component": "Employer NSSF Tier 2", "salary_component_abbr": "NSSFTIER2E",
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
	# Not a payment - the chargeable pay the month's PAYE was worked out on,
	# carried on the payslip so the returns all quote the same figure. Flagged
	# do_not_include_in_total so it touches neither gross nor net, and left
	# without an account so it never reaches the payroll journal.
	{"salary_component": "Taxable Income", "salary_component_abbr": "TXI",
	 "do_not_include_in_total": 1, "do_not_include_in_accounts": 1,
	 "statistical_component": 0, "remove_if_zero_valued": 0,
	 "depends_on_payment_days": 0,
	 "description": "The pay this month's PAYE was charged on. It is shown for "
					"the returns to read and does not add anything to what the "
					"employee is paid."},
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
		# Added at the end of the run, in its own heading, rather than slotted
		# in beside the monthly opt-outs. Dropping it into the middle renumbered
		# every field below it and rewired the one it displaced - churn on
		# fields that had nothing to do with gratuity. Appending leaves them
		# exactly where they were, and a terminal benefit reads better apart
		# from the monthly deductions anyway.
		{"fieldname": "terminal_benefits_section", "fieldtype": "Section Break",
		 "label": "Terminal Benefits", "insert_after": "union_member", "collapsible": 1},
		{"fieldname": "paid_under_public_pension_scheme", "fieldtype": "Check",
		 "label": "Paid Under a Public Pension Scheme",
		 "insert_after": "terminal_benefits_section",
		 "description": "Tick only where this employee's gratuity is paid under a "
						"public pension scheme. Their gratuity is then tax free up "
						"to the yearly allowance in Kenya Payroll Settings, and only "
						"the excess is taxed. Leave it clear for ordinary "
						"contractual or CBA gratuity."},
	],
	# Payroll Entry already filters by branch, department, designation and
	# grade. The advanced box below covers anything else a company splits a run
	# along, on any field the Employee record carries.
	"Payroll Entry": [
		{"fieldname": "advanced_filters_section", "fieldtype": "Section Break",
		 "label": "Advanced Filters", "insert_after": "grade",
		 "collapsible": 1},
		{"fieldname": "filter_list", "fieldtype": "HTML",
		 "insert_after": "advanced_filters_section"},
		# Where the box's conditions are kept so they travel with the document
		# when Get Employees posts it. Hidden - the box above is the interface.
		{"fieldname": "advanced_employee_filters", "fieldtype": "Small Text",
		 "label": "Advanced Employee Filters", "hidden": 1, "read_only": 1,
		 "insert_after": "filter_list"},
		# Task workers are paid entirely through Additional Salary, so a run for
		# them should pull the ones who actually earned something rather than
		# everybody who holds a salary structure.
		{"fieldname": "custom_only_with_additional_salary", "fieldtype": "Check",
		 "label": "Only Employees With Additional Salary",
		 "insert_after": "advanced_employee_filters",
		 "description": "Pull only employees who have an Additional Salary "
						"falling in this period. Everyone else is left out of "
						"the run rather than given an empty payslip."},
	],
	# Links a liability remittance back to the run it clears. Deliberately not
	# the reference_type row core uses: hrms reads that as "the salaries have
	# been paid" and hides Make Bank Entry (liability_remittance.py:27).
	"Journal Entry": [
		{"fieldname": "custom_source_payroll_entry", "fieldtype": "Link",
		 "label": "Source Payroll Entry", "options": "Payroll Entry",
		 "insert_after": "voucher_type", "read_only": 1, "no_copy": 1,
		 "description": "The payroll run whose liabilities this entry remits."},
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


WORKSPACE = "Payroll"

# What this app puts on the Payroll workspace. Child tables are deliberately
# absent - they are only ever reached through their parent.
WORKSPACE_CARDS = [
	("Kenya Payroll", "DocType", [
		"Kenya Payroll Settings",
		"Company Payroll Settings",
		"Deduction Priority",
		"Deduction Group",
		"Deferred Deduction",
		"Terminal Dues Settlement",
		"Leave Provision",
		"Employee Tax History",
		"CBA",
	]),
	("Kenya Statutory Reports", "Report", [
		"Company Register",
		"National Social Security Fund",
		"Social Health Insurance Fund",
		"Affordable Housing Levy",
		"HELB Report",
		"Kenya P9 Card Report",
		"Kenya P10 Report",
		"Leave Liability",
	]),
]


def add_to_payroll_workspace():
	"""Put this app's doctypes and returns on the Payroll workspace.

	Re-applied on every migrate, deliberately. The workspace belongs to HRMS and
	is rebuilt from HRMS's own definition whenever it syncs, which drops whatever
	anyone else added - so doing this once would hold until the next HRMS update
	and then quietly vanish. after_migrate runs last, so putting them back here
	is what makes them stay.
	"""
	if not frappe.db.exists("Workspace", WORKSPACE):
		return

	workspace = frappe.get_doc("Workspace", WORKSPACE)
	present = {(row.type, row.label) for row in workspace.links}
	changed = False

	for card, link_type, targets in WORKSPACE_CARDS:
		# Only what actually exists on this site. A site without the lending app,
		# or one where a report has been removed, should not get a dead link.
		live = [t for t in targets if frappe.db.exists(link_type, t)]
		if not live:
			continue

		if ("Card Break", card) not in present:
			workspace.append("links", {
				"type": "Card Break", "label": card,
				"link_count": len(live), "onboard": 0, "hidden": 0,
			})
			changed = True

		for target in live:
			if ("Link", target) in present:
				continue
			workspace.append("links", {
				"type": "Link", "label": target,
				"link_type": link_type, "link_to": target,
				# Script Reports open through the query report view; a DocType
				# link must not carry this or it opens the wrong route.
				"is_query_report": 1 if link_type == "Report" else 0,
				"onboard": 0, "hidden": 0,
			})
			changed = True

	if _add_workspace_cards_to_content(workspace):
		changed = True

	if changed:
		workspace.save(ignore_permissions=True)


def _add_workspace_cards_to_content(workspace):
	"""Put the cards into the workspace's own layout.

	Links in the child table are only the contents of a card - what actually
	gets drawn is the JSON in the content field, a list of blocks. A Card Break
	with no matching card block in there exists, holds its links, and never
	appears on the page. That is why the first attempt looked correct in the
	database and showed nothing on screen.
	"""
	try:
		blocks = frappe.parse_json(workspace.content) or []
	except Exception:
		return False

	present = {
		block.get("data", {}).get("card_name")
		for block in blocks
		if block.get("type") == "card"
	}

	added = False
	for card, _link_type, _targets in WORKSPACE_CARDS:
		if card in present:
			continue
		if not any(row.type == "Card Break" and row.label == card
				   for row in workspace.links):
			continue
		blocks.append({
			# Frappe generates ten character block ids; anything unique works,
			# it only has to be stable so the layout is not rewritten each time.
			"id": frappe.generate_hash(length=10),
			"type": "card",
			"data": {"card_name": card, "col": 4},
		})
		added = True

	if added:
		workspace.content = frappe.as_json(blocks)

	return added


def after_migrate():
	ensure_statutory_components()
	open_salary_structure_tables()
	ensure_statutory_fields()
	link_employee_bank_to_bank_doctype()
	add_to_payroll_workspace()


def ensure_statutory_fields():
	"""The P9 and P10 tags, and the union membership a dues formula reads."""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields(STATUTORY_FIELDS)
	remove_retired_fields()
	remove_retired_doctypes()
	frappe.db.commit()


# Fields this app used to add and no longer wants. Removed on every site, not
# just the one they were noticed on - a field left behind keeps rendering.
RETIRED_FIELDS = {
	# The wage base and the limit were only ever written, never read back - the
	# figures they showed are already implied by the deductions on the slip and
	# the wage base method on Deduction Priority.
	"Salary Slip": ["custom_wage_base_for_deduction_cap",
					"custom_maximum_permitted_deduction"],
	# Payroll Entry's own Employment Type filter. The advanced filter box does
	# the same job on any Employee field, so a dedicated one earned its space
	# only if companies split runs that way, and they do not.
	"Payroll Entry": ["employment_type"],
	# Renamed while nothing was configured: the remittance is per liability
	# account, so there is no separate grouping label to carry.
	# Which liability an entry settles is the account it debits. Carrying it in
	# a field as well was one fact in two places, and the field only ever
	# repeated what the row beneath it already said.
	"Journal Entry": ["custom_remittance_payee_group", "custom_remittance_remit_to",
					  "custom_remitted_liability_account"],
	"Employee": [# Service time for a promotion is measured from the joining date,
				 # so a second date recording when somebody entered their current
				 # category had nothing to add. It only mattered for chained
				 # rules, and a category that has been promoted out of has no
				 # rule left to match.
				 "custom_job_category_since",
				 # Which union somebody belongs to was never read by anything -
				 # the tick box is what a dues formula needs.
				 "union",
				 "payroll_earnings_section", "payroll_deductions_section",
				 # Dropped: a salary slip already records what someone was on,
				 # period by period, where one field could only hold the last
				 # change.
				 "previous_base_pay",
				 # Written by the CBA and read by nothing. The increment checks
				 # Basic Pay, and so would a performance rise.
				 "base_pay"],
}

# Doctypes this app shipped and then replaced. Employee Annual Tax Record held
# one document per employee per year, which for a few thousand staff is a list
# nobody can work with; Employee Tax History holds the same figures as a grid
# under one document per employee.
RETIRED_DOCTYPES = [
	"Employee Annual Tax Record",
	# Both replaced by Payroll Remittance Account before any of them shipped.
	"Payroll Remittance Group",
	"Payroll Remittance Exception",
]


def remove_retired_doctypes():
	for doctype in RETIRED_DOCTYPES:
		if not frappe.db.exists("DocType", doctype):
			continue
		# Never delete one that has data in it. Better a stray doctype than a
		# migration that silently discards figures somebody keyed in.
		if frappe.db.count(doctype):
			frappe.msgprint(_(
				"{0} has been replaced by Employee Tax History but still holds "
				"{1} record(s), so it has been left in place. Move the figures "
				"across, then delete it."
			).format(doctype, frappe.db.count(doctype)))
			continue
		frappe.delete_doc("DocType", doctype, ignore_permissions=True, force=True)


def remove_retired_fields():
	for doctype, fieldnames in RETIRED_FIELDS.items():
		for fieldname in fieldnames:
			name = frappe.db.get_value(
				"Custom Field", {"dt": doctype, "fieldname": fieldname}, "name"
			)
			if name:
				frappe.delete_doc("Custom Field", name,
								  ignore_permissions=True, force=True)
		_drop_from_field_order(doctype, fieldnames)


def _drop_from_field_order(doctype, fieldnames):
	"""Take retired fields out of the pinned order as well as deleting them.

	Deleting the Custom Field alone leaves its name sitting in the field_order
	Property Setter, which is the list the form is actually built from.
	"""
	setter = frappe.db.get_value(
		"Property Setter",
		{"doc_type": doctype, "property": "field_order"},
		["name", "value"],
		as_dict=True,
	)
	if not setter:
		return

	fields = frappe.parse_json(setter.value) or []
	kept = [f for f in fields if f not in fieldnames]
	if len(kept) != len(fields):
		frappe.db.set_value("Property Setter", setter.name, "value",
							frappe.as_json(kept), update_modified=False)


# Nothing here reorders the Employee form any more.
#
# It used to place this app's fields on every migrate, so a field moved on the
# form was dragged back the next time anyone migrated - repeatedly, and with no
# indication why. Placing a new field is a one-off job for whoever adds it;
# fighting the form on a schedule is not worth the confusion it causes.
#
# New fields get their position from insert_after when they are created. If a
# site pins its own field order, a newly added field lands at the bottom of the
# form and has to be dragged into place once, by hand.


def link_employee_bank_to_bank_doctype():
	"""Make the Employee's Bank Name a link to the Bank record, not free text.

	As a Data field every payroll officer types the bank their own way -
	"KCB", "K.C.B", "Kenya Commercial Bank" - and a bank payment file has to
	group by an exact name. A link means one spelling, chosen from a list.

	Only the field type and its target change. bank_ac_no stays free text,
	because an account number is genuinely per employee.
	"""
	for prop, value, prop_type in (
		("fieldtype", "Link", "Select"),
		("options", "Bank", "Text"),
	):
		frappe.make_property_setter({
			"doctype": "Employee",
			"fieldname": "bank_name",
			"property": prop,
			"value": value,
			"property_type": prop_type,
		}, is_system_generated=False)

	frappe.clear_cache(doctype="Employee")
	frappe.db.commit()


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
