// Copyright (c) 2026, Teresia and contributors
// For license information, please see license.txt

frappe.query_reports["CBA Application Log"] = {
	filters: [
		{
			fieldname: "cba",
			label: __("CBA"),
			fieldtype: "Link",
			options: "CBA",
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
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
			fieldname: "employee",
			label: __("Employee"),
			fieldtype: "Link",
			options: "Employee",
		},
		{
			fieldname: "from_date",
			label: __("Applied From"),
			fieldtype: "Date",
		},
		{
			fieldname: "to_date",
			label: __("Applied To"),
			fieldtype: "Date",
		},
	],
};
