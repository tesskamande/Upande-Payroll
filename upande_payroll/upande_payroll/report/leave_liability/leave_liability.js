// Copyright (c) 2026, Teresia and contributors
// For license information, please see license.txt

frappe.query_reports["Leave Liability"] = {
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
			default: frappe.datetime.add_months(frappe.datetime.year_start(), 0),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(),
		},
		{
			fieldname: "breakdown",
			label: __("Show"),
			fieldtype: "Select",
			options: "Each Period\nDepartment\nEmployee",
			default: "Each Period",
			// Department and Employee only make sense for one period at a time -
			// a liability is a standing figure, so adding periods together would
			// count the same debt twice.
		},
	],
};
