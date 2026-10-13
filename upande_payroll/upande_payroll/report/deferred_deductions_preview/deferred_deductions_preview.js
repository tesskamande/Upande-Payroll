// Copyright (c) 2026, Teresia and contributors
// For license information, please see license.txt

frappe.query_reports["Deferred Deductions Preview"] = {
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
			fieldname: "payroll_entry",
			label: __("Payroll Entry"),
			fieldtype: "Link",
			options: "Payroll Entry",
			get_query: () => ({
				filters: { company: frappe.query_report.get_filter_value("company") },
			}),
			// Pick a run and its period comes with it - same pattern as Payroll
			// Register's own payroll_entry filter.
			on_change: () => {
				const run = frappe.query_report.get_filter_value("payroll_entry");
				if (!run) return;
				frappe.db
					.get_value("Payroll Entry", run, ["company", "start_date", "end_date"])
					.then(({ message }) => {
						if (!message) return;
						frappe.query_report.set_filter_value({
							company: message.company,
							from_date: message.start_date,
							to_date: message.end_date,
						});
					});
			},
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "employee",
			label: __("Employee"),
			fieldtype: "Link",
			options: "Employee",
		},
		{
			fieldname: "docstatus",
			label: __("Status"),
			fieldtype: "Select",
			options: [
				{ value: "", label: __("Draft + Submitted") },
				{ value: "0", label: __("Draft Only") },
				{ value: "1", label: __("Submitted Only") },
			],
		},
	],
};
