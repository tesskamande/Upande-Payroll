// Copyright (c) 2026, Teresia and contributors
// For license information, please see license.txt

frappe.query_reports["Affordable Housing Levy"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(),
			reqd: 1,
		},
		{
			fieldname: "docstatus",
			label: __("Payslip Status"),
			fieldtype: "Select",
			options: "Submitted\nDraft\nCancelled",
			default: "Submitted",
		},
		{
			fieldname: "department",
			label: __("Department"),
			fieldtype: "Link",
			options: "Department",
		},
		{
			fieldname: "employee",
			label: __("Employee"),
			fieldtype: "Link",
			options: "Employee",
			get_query() {
				return {
					filters: { company: frappe.query_report.get_filter_value("company") },
				};
			},
		},
	],
};
