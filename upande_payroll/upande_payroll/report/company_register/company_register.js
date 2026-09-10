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
			fieldname: "group_by",
			label: __("Split Into Columns By"),
			fieldtype: "Select",
			options: [
				"",
				"Farm",
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

	// Headings and the spacer rows carry no key for the amount columns at all,
	// and Frappe's currency formatter turns a missing Currency value into 0.00 -
	// so without this every heading reads as a row of zeros. Keyed on the value
	// being absent rather than on is_group, because Gross Pay, Total Deductions
	// and Net Pay carry is_group too and do have figures to show.
	formatter(value, row, column, data, default_formatter) {
		const amount = column.fieldname !== "component";

		if (amount && data) {
			// Keyed on the row, not on the value. A heading carries no figure for
			// these columns, and whether that reaches us as null or as 0 is the
			// datatable's business - either way a heading must not read 0.00.
			if (data.is_heading || !data.component) return "";

			// A count of people in a column typed as Currency, so the default
			// formatter would print three employees as 3.00 beside the money.
			if (data.is_headcount) {
				return value == null ? "" : `<b>${Math.round(value)}</b>`;
			}
		}

		value = default_formatter(value, row, column, data);
		// Headings and totals both carry is_group, so they read as headings rather
		// than as just another component in a long list.
		if (data && data.is_group) {
			value = `<b>${value}</b>`;
		}
		return value;
	},
};
