// Copyright (c) 2026, Teresia and contributors
// For license information, please see license.txt

frappe.query_reports["Kenya P9 Card Report"] = {
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
			fieldname: "employee",
			label: __("Employee"),
			fieldtype: "Link",
			options: "Employee",
			reqd: 1,
			get_query() {
				return {
					filters: { company: frappe.query_report.get_filter_value("company") },
				};
			},
		},
		{
			fieldname: "fiscal_year",
			label: __("Fiscal Year"),
			fieldtype: "Link",
			options: "Fiscal Year",
			reqd: 1,
			default: frappe.defaults.get_user_default("fiscal_year"),
		},
		{
			fieldname: "docstatus",
			label: __("Payslip Status"),
			fieldtype: "Select",
			options: "Submitted\nDraft\nCancelled",
			default: "Submitted",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && data.month === __("Total")) {
			value = `<b>${value}</b>`;
		}
		return value;
	},
};
