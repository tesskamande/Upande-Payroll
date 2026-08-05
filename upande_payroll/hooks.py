app_name = "upande_payroll"
app_title = "Upande Payroll"
app_publisher = "Teresia"
app_description = "Kenyan Upande Payroll Customizations"
app_email = "teresia@upande.com"
app_license = "mit"

fixtures = [
	{
		"dt": "Custom Field",
		"prefix": "gratuity",
		"filters": [["dt", "=", "Gratuity"], ["fieldname", "like", "custom_%"]],
	},
	{
		"dt": "Custom Field",
		"prefix": "employee",
		"filters": [
			["dt", "=", "Employee"],
			["fieldname", "in", [
				"job_category",
				"base_pay", "basic_pay", "previous_base_pay",
				"custom_is_secondary_employment",
				"custom_opt_out_of_nssf", "custom_opt_out_of_shif",
				"custom_opt_out_of_housing_levy",
				"custom_salary_expense_account",
				"union_membership_section", "union_member", "union",
				"payroll_earnings_section", "payroll_deductions_section",
			]],
		],
	},
	{
		"dt": "Custom Field",
		"prefix": "leave_encashment",
		"filters": [["dt", "=", "Leave Encashment"], ["fieldname", "like", "custom_%"]],
	},
	{
		"dt": "Custom Field",
		"prefix": "salary_component",
		"filters": [["dt", "=", "Salary Component"], ["fieldname", "like", "custom_%"]],
	},
	{
		"dt": "Custom Field",
		"prefix": "salary_slip",
		"filters": [
			["dt", "=", "Salary Slip"],
			["fieldname", "in", [
				"custom_personal_relief_method",
				"custom_personal_relief_section", "custom_personal_relief_brought_forward",
				"custom_tax_charged",
				"custom_personal_relief_available_this_month", "custom_personal_relief_carried_forward",
				"column_break_relief", "custom_personal_relief_utilized", "custom_annual_personal_relief",
				"custom_deduction_cap_section", "custom_wage_base_for_deduction_cap",
				"custom_maximum_permitted_deduction", "column_break_deduction_cap",
				"custom_deduction_cap_applied", "custom_unreducible_excess",
				"custom_brought_forward_deductions", "custom_deferred_deductions",
			]],
		],
	},
	{
		"dt": "Property Setter",
		"prefix": "field_order",
		"filters": [
			["doc_type", "in", ["Employee", "Gratuity", "Leave Encashment", "Salary Component", "Salary Slip"]],
			["property", "=", "field_order"],
		],
	},
	{
		"dt": "Property Setter",
		"prefix": "salary_structure_open_tables",
		"filters": [
			["doc_type", "=", "Salary Structure"],
			["property", "=", "allow_on_submit"],
		],
	},
	{
		"dt": "Property Setter",
		"prefix": "gratuity_payment_mode",
		"filters": [
			["doc_type", "=", "Gratuity"],
			["field_name", "in", [
				"mode_of_payment", "expense_account", "payable_account",
				"salary_component", "posting_date", "payroll_date",
			]],
		],
	}
]

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "upande_payroll",
# 		"logo": "/assets/upande_payroll/logo.png",
# 		"title": "Upande Payroll Customizations",
# 		"route": "/upande_payroll",
# 		"has_permission": "upande_payroll.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/upande_payroll/css/upande_payroll.css"
# app_include_js = "/assets/upande_payroll/js/upande_payroll.js"

# include js, css files in header of web template
# web_include_css = "/assets/upande_payroll/css/upande_payroll.css"
# web_include_js = "/assets/upande_payroll/js/upande_payroll.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "upande_payroll/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
after_migrate = "upande_payroll.setup.after_migrate"
after_install = "upande_payroll.setup.after_migrate"

doctype_js = {
	"Gratuity": "public/js/gratuity.js",
	"Leave Encashment": "public/js/leave_encashment.js",
	"Payroll Entry": "public/js/payroll_entry.js",
	"Salary Slip": "public/js/salary_slip.js",
}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "upande_payroll/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "upande_payroll.utils.jinja_methods",
# 	"filters": "upande_payroll.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "upande_payroll.install.before_install"
# after_install = "upande_payroll.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "upande_payroll.uninstall.before_uninstall"
# after_uninstall = "upande_payroll.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "upande_payroll.utils.before_app_install"
# after_app_install = "upande_payroll.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "upande_payroll.utils.before_app_uninstall"
# after_app_uninstall = "upande_payroll.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "upande_payroll.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "upande_payroll.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Employee": {
		"validate": "upande_payroll.cba_utils.validate_basic_pay_against_cba",
	},
	"Gratuity": {
		"validate": "upande_payroll.gratuity_utils.calculate_gratuity",
	},
	"Leave Encashment": {
		"validate": "upande_payroll.leave_encashment_utils.validate_leave_encashment",
	},
	"Journal Entry": {
		"before_insert": "upande_payroll.payroll_journal.rewrite_payroll_journal",
	},
	"Leave Application": {
		"on_submit": "upande_payroll.leave_travelling_allowance.create_lta",
		"on_cancel": "upande_payroll.leave_travelling_allowance.cancel_lta",
	},
	# After the controller's own validate, so both component tables and the
	# totals exist. The 1/3 rule has to see every deduction at once, which is
	# why it can't sit in the regional_overrides hook with the statutory ones.
	# The earnings and deductions tables are open after submit (Property Setter),
	# so a component can be added without cancelling and re-assigning everyone.
	# validate() doesn't run on a submitted save, so its checks are re-run here.
	"Salary Structure": {
		"before_update_after_submit": "upande_payroll.salary_structure_utils.validate_after_submit",
	},
	"Salary Slip": {
		"validate": "upande_payroll.deduction_cap.apply_deduction_cap",
		# The ledger only moves once the slip is real. validate runs on every
		# save, including drafts that may never be submitted.
		"on_submit": "upande_payroll.deduction_cap.settle_deferred_deductions",
		"on_cancel": "upande_payroll.deduction_cap.unsettle_deferred_deductions",
	},
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"upande_payroll.tasks.all"
# 	],
# 	"daily": [
# 		"upande_payroll.tasks.daily"
# 	],
# 	"hourly": [
# 		"upande_payroll.tasks.hourly"
# 	],
# 	"weekly": [
# 		"upande_payroll.tasks.weekly"
# 	],
# 	"monthly": [
# 		"upande_payroll.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "upande_payroll.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
extend_doctype_class = {
	"Leave Encashment": "upande_payroll.leave_encashment_utils.LeaveEncashmentMixin",
	"Overtime Slip": "upande_payroll.overtime_utils.OvertimeSlipMixin",
}

# Regional overrides for HRMS's own apply_regional_deductions hook point -
# the first-class extension mechanism for country-specific Salary Slip
# statutory deductions (same one core HRMS uses for India/UAE).
regional_overrides = {
	"Kenya": {
		"hrms.payroll.doctype.salary_slip.salary_slip.apply_regional_deductions":
			"upande_payroll.kenya_statutory_calculator.apply_regional_deductions",
	},
}

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "upande_payroll.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "upande_payroll.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["upande_payroll.utils.before_request"]
# after_request = ["upande_payroll.utils.after_request"]

# Job Events
# ----------
# before_job = ["upande_payroll.utils.before_job"]
# after_job = ["upande_payroll.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"upande_payroll.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

