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
	# Not a payment - the chargeable pay the month's PAYE was worked out on,
	# carried on the payslip so the returns all quote the same figure. Flagged
	# do_not_include_in_total so it touches neither gross nor net, and left
	# without an account so it never reaches the payroll journal.
	{"salary_component": "Taxable Income", "salary_component_abbr": "TXI",
	 "do_not_include_in_total": 1,
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
		{"fieldname": "union", "fieldtype": "Data", "label": "Union",
		 "insert_after": "union_member", "depends_on": "eval:doc.union_member",
		 "description": "Which union, e.g. COTU or KPAWU. Useful when a company "
						"deals with more than one."},
		# Added at the end of the run, in its own heading, rather than slotted
		# in beside the monthly opt-outs. Dropping it into the middle renumbered
		# every field below it and rewired the one it displaced - churn on
		# fields that had nothing to do with gratuity. Appending leaves them
		# exactly where they were, and a terminal benefit reads better apart
		# from the monthly deductions anyway.
		{"fieldname": "terminal_benefits_section", "fieldtype": "Section Break",
		 "label": "Terminal Benefits", "insert_after": "union", "collapsible": 1},
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
	# grade. Employment Type is the one companies also split a run along -
	# contract staff paid separately from permanent - and it is not there.
	"Payroll Entry": [
		{"fieldname": "employment_type", "fieldtype": "Link",
		 "label": "Employment Type", "options": "Employment Type",
		 "insert_after": "grade",
		 "description": "Narrow this run to one employment type, e.g. only "
						"permanent staff or only contract. Leave it empty to "
						"include every type."},
		# The same advanced filter box the Bulk Salary Structure Assignment
		# tool has. The fields above cover the usual splits; this covers the
		# ones they do not, on any field the Employee record carries.
		{"fieldname": "advanced_filters_section", "fieldtype": "Section Break",
		 "label": "Advanced Filters", "insert_after": "employment_type",
		 "collapsible": 1},
		{"fieldname": "filter_list", "fieldtype": "HTML",
		 "insert_after": "advanced_filters_section"},
		# Where the box's conditions are kept so they travel with the document
		# when Get Employees posts it. Hidden - the box above is the interface.
		{"fieldname": "advanced_employee_filters", "fieldtype": "Small Text",
		 "label": "Advanced Employee Filters", "hidden": 1, "read_only": 1,
		 "insert_after": "filter_list"},
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
	grant_payroll_manager_access()
	seed_single_defaults()
	add_to_payroll_workspace()


# Fields on a Single doctype whose default has to be planted explicitly.
SINGLE_DEFAULTS = {
	"Kenya Payroll Settings": ["gratuity_public_scheme_annual_exemption"],
}


def seed_single_defaults():
	"""Give a Single's new fields their default value.

	A default only lands when a document is saved, and an existing site never
	re-saves its settings on migrate - so a newly shipped rate would read as
	nil, and nil here means an allowance that silently exempts nothing. Seeded
	once from the field's own default, then left alone: a site that has since
	set its own figure, zero included, keeps it.
	"""
	from frappe.utils import flt

	for doctype, fieldnames in SINGLE_DEFAULTS.items():
		meta = frappe.get_meta(doctype)
		for fieldname in fieldnames:
			# The raw Singles row, not get_single_value: that casts a missing
			# Currency to 0.0 rather than None, so "never set" and "deliberately
			# zero" come back identical and nothing would ever seed.
			stored = frappe.db.sql(
				"select value from tabSingles where doctype=%s and field=%s",
				(doctype, fieldname),
			)
			if stored:
				continue
			field = meta.get_field(fieldname)
			if field and field.default is not None:
				frappe.db.set_single_value(doctype, fieldname, flt(field.default))

	frappe.db.commit()


# Salary Slip belongs to HRMS, so the Payroll Manager permission is applied as a
# Custom DocPerm here rather than by editing HRMS's own doctype.
PAYROLL_MANAGER_DOCTYPES = ["Salary Slip"]


def grant_payroll_manager_access():
	"""Let Payroll Manager open the statutory reports.

	Listing a role on a Report is only half of it - frappe.desk.query_report.run
	also checks has_permission(ref_doctype, "report") and refuses otherwise, so
	without this the reports appear in the list and then throw when opened.

	Read, report and export only - sight of payroll, not the ability to change
	it. Export is included on purpose: these are returns that get filed with
	KRA, and a role that can open the P10 but not download it cannot do the job.
	That does make Payroll Manager able to export Salary Slip data where HR
	Manager currently cannot; if that is not wanted, drop "export" here.
	"""
	from frappe.permissions import add_permission, update_permission_property

	role = "Payroll Manager"
	if not frappe.db.exists("Role", role):
		return

	for doctype in PAYROLL_MANAGER_DOCTYPES:
		if not frappe.db.exists("DocType", doctype):
			continue
		if frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}):
			continue
		add_permission(doctype, role, 0)
		# Set every right explicitly rather than trusting add_permission's
		# defaults, so what this role can do is readable here.
		for right, value in (("read", 1), ("report", 1), ("export", 1),
							 ("write", 0), ("create", 0), ("delete", 0),
							 ("submit", 0), ("cancel", 0), ("amend", 0)):
			update_permission_property(doctype, role, 0, right, value)

	frappe.db.commit()


def ensure_statutory_fields():
	"""The P9 and P10 tags, and the union membership a dues formula reads."""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields(STATUTORY_FIELDS)
	remove_retired_fields()
	remove_retired_doctypes()
	group_salary_tab()
	frappe.db.commit()


# Fields this app used to add and no longer wants. Removed on every site, not
# just the one they were noticed on - a field left behind keeps rendering.
RETIRED_FIELDS = {
	"Employee": ["payroll_earnings_section", "payroll_deductions_section"],
}

# Doctypes this app shipped and then replaced. Employee Annual Tax Record held
# one document per employee per year, which for a few thousand staff is a list
# nobody can work with; Employee Tax History holds the same figures as a grid
# under one document per employee.
RETIRED_DOCTYPES = ["Employee Annual Tax Record"]


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


def group_salary_tab():
	"""Put this app's Employee fields under headings instead of a flat run.

	Without this they land one after another on the Salary tab in whatever order
	they were created, so a payroll clerk reads base pay, an opt-out and an
	expense account as one undifferentiated list.
	"""
	# Anchored to the end of the salary block HRMS already builds, not to
	# salary_mode: employee_advance_account is anchored there too, and two
	# fields claiming one slot leaves the loser floating to the bottom of the
	# form. The existing run is salary_mode > employee_advance_account >
	# salary_cb > payroll_cost_center, so ours picks up after it.
	ANCHOR = "payroll_cost_center"

	# No Earnings or Deductions headings. They were added to break up a flat
	# run, but they moved the pay figures and the statutory opt-outs away from
	# where people were used to finding them - straight after Payroll Cost
	# Center. The two section breaks are retired in remove_retired_fields().
	salary_run = [
		"base_pay", "basic_pay", "previous_base_pay",
		"custom_is_secondary_employment",
		"custom_opt_out_of_nssf", "custom_opt_out_of_shif",
		"custom_opt_out_of_housing_levy", "custom_salary_expense_account",
		"union_membership_section", "union_member", "union",
		"terminal_benefits_section", "paid_under_public_pension_scheme",
	]
	# Job Category classifies the person, it is not a pay figure, so it belongs
	# with Designation on the Overview tab rather than in the salary run.
	_pin(["job_category"], "designation", start=90)
	_pin(salary_run, ANCHOR, start=100)

	_splice_into_field_order(["job_category"], "designation")
	_splice_into_field_order(salary_run, ANCHOR)
	frappe.clear_cache(doctype="Employee")


def _pin(order, anchor, start):
	"""Set insert_after and idx so a run of fields reads in the given order.

	idx as well as insert_after: Frappe walks custom fields in idx order and
	resolves each against what it has placed so far, so a field whose anchor has
	not been reached yet drops to the bottom of the form. HRMS's own custom
	fields sit at idx 0, so ours start well clear of them.
	"""
	previous = anchor
	for position, fieldname in enumerate(order, start=start):
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
