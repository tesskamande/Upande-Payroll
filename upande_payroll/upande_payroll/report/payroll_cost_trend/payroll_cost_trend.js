// Copyright (c) 2026, Teresia and contributors
// For license information, please see license.txt

frappe.query_reports["Payroll Cost Trend"] = {
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
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -12),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "job_category",
			label: __("Job Category"),
			fieldtype: "Select",
			options: frappe.meta.get_docfield("CBA Pay Table", "job_category")
				? "\n" + frappe.meta.get_docfield("CBA Pay Table", "job_category").options
				: "",
		},
	],
};
