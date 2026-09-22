// Copyright (c) 2026, Teresia and contributors
// For license information, please see license.txt

frappe.query_reports["Salary Review"] = {
	filters: [
		{
			fieldname: "docstatus",
			label: __("Status"),
			fieldtype: "Select",
			options: ["Submitted", "Draft", "Cancelled"],
			default: "Submitted",
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "employee",
			label: __("Employee"),
			fieldtype: "Link",
			options: "Employee",
		},
		{
			fieldname: "review_type",
			label: __("Review Type"),
			fieldtype: "Select",
			options: "\nAnnual Review\nPromotion\nMarket Adjustment\nCorrection",
		},
		{
			fieldname: "from_date",
			label: __("Effective From"),
			fieldtype: "Date",
		},
		{
			fieldname: "to_date",
			label: __("Effective To"),
			fieldtype: "Date",
		},
	],
};
