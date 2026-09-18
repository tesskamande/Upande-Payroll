// Copyright (c) 2026, Teresia and contributors
// For license information, please see license.txt

frappe.query_reports["Payroll Register"] = {
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
			fieldname: "docstatus",
			label: __("Document Status"),
			fieldtype: "Select",
			options: ["Submitted", "Draft", "Cancelled"],
			default: "Draft",
		},
		{
			fieldname: "payroll_entry",
			label: __("Payroll Entry"),
			fieldtype: "Link",
			options: "Payroll Entry",
			get_query: () => ({
				filters: { company: frappe.query_report.get_filter_value("company") },
			}),
			// Pick a run and the period comes with it. Opened from the Payroll
			// Entry's Connections the dashboard sets this filter and nothing else,
			// so without this the dates would still read whatever the report
			// defaulted to - the current month, not the run being looked at.
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
			fieldname: "farm",
			label: __("Farm"),
			fieldtype: "Link",
			options: "Farm",
			// Matched against the Employee's own Farm field, which holds the farm
			// name - the same string a Farm document is named by.
		},
		{
			fieldname: "employee",
			label: __("Employee"),
			fieldtype: "Link",
			options: "Employee",
			get_query: () => ({
				filters: { company: frappe.query_report.get_filter_value("company") },
			}),
		},
		{
			fieldname: "department",
			label: __("Department"),
			fieldtype: "Link",
			options: "Department",
			get_query: () => ({
				filters: { company: frappe.query_report.get_filter_value("company") },
			}),
		},
		{
			fieldname: "designation",
			label: __("Designation"),
			fieldtype: "Link",
			options: "Designation",
		},
		{
			fieldname: "branch",
			label: __("Branch"),
			fieldtype: "Link",
			options: "Branch",
		},
		{
			fieldname: "show_deferred",
			label: __("Show What Was Not Collected"),
			fieldtype: "Check",
			default: 0,
		},
		{
			fieldname: "show_employer_cost",
			label: __("Show Employer Contributions"),
			fieldtype: "Check",
			default: 0,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		// A withheld payslip is still owed to the employee, so it reads like any
		// other line here. Colouring it stops someone reconciling a bank run
		// against a register that includes pay nobody received.
		if (column.fieldname === "status" && data && data.status === "Withheld") {
			value = `<span style="color: var(--orange-600); font-weight: 500">${value}</span>`;
		}
		return value;
	},
};
