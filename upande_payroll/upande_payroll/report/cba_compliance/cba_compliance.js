// Copyright (c) 2026, Teresia and contributors
// For license information, please see license.txt

frappe.query_reports["CBA Compliance"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			reqd: 1,
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "job_category",
			label: __("Job Category"),
			fieldtype: "Select",
			// Read off the pay table so the two never drift apart.
			options: frappe.meta.get_docfield("CBA Pay Table", "job_category")
				? "\n" + frappe.meta.get_docfield("CBA Pay Table", "job_category").options
				: "",
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: "\nActive\nInactive\nSuspended\nLeft",
		},
		{
			fieldname: "only_below_minimum",
			label: __("Only Below Minimum"),
			fieldtype: "Check",
		},
	],
};
