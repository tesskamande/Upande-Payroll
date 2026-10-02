// Copyright (c) 2026, Teresia and contributors
// For license information, please see license.txt

frappe.query_reports["Loan Portfolio at Risk"] = {
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
			fieldname: "as_on_date",
			label: __("As On Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "loan_product",
			label: __("Loan Product"),
			fieldtype: "Link",
			options: "Loan Product",
		},
		{
			fieldname: "applicant",
			label: __("Employee"),
			fieldtype: "Link",
			options: "Employee",
		},
		{
			fieldname: "bucket",
			label: __("Bucket"),
			fieldtype: "Select",
			options: ["", "Current", "1-30 Days", "31-60 Days", "61-90 Days", "90+ Days"],
		},
	],
};
