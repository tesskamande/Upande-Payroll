// Copyright (c) 2026, Teresia and contributors
// For license information, please see license.txt

frappe.query_reports["HELB Report"] = {
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
			fieldname: "salary_component",
			label: __("Salary Component"),
			fieldtype: "Link",
			options: "Salary Component",
			// Not required. A company that runs HELB as a loan rather than a
			// deduction has no component to name, and clearing this is how it
			// says so.
			default: "HELB",
			get_query() {
				return { filters: { type: "Deduction" } };
			},
		},
		{
			fieldname: "loan_product",
			label: __("Loan Product"),
			fieldtype: "Link",
			options: "Loan Product",
			// Set this where HELB is tracked as a loan. Whatever each payslip
			// repaid on it is added to the deduction above, so a site midway
			// through moving from one to the other reports both.
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
