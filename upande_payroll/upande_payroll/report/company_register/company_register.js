// Copyright (c) 2026, Teresia and contributors
// For license information, please see license.txt

frappe.query_reports["Company Register"] = {
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
			fieldname: "group_by",
			label: __("Split Into Columns By"),
			fieldtype: "Select",
			options: [
				"",
				"Department",
				"Designation",
				"Employee Grade",
				"Branch",
				"Employment Type",
			],
			default: "",
		},
		{
			fieldname: "docstatus",
			label: __("Payslip Status"),
			fieldtype: "Select",
			options: "Submitted\nDraft\nCancelled",
			default: "Submitted",
			reqd: 1,
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
		},
		{
			fieldname: "include_employer_contributions",
			label: __("Include Employer Contributions"),
			fieldtype: "Check",
			default: 0,
		},
	],

	// Headings and totals carry is_group, so they read as headings rather than
	// as just another component in a long list.
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && data.is_group) {
			value = `<b>${value}</b>`;
		}
		return value;
	},
};
