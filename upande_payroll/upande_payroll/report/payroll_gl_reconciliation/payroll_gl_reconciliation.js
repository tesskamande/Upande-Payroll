// Copyright (c) 2026, Teresia and contributors
// For license information, please see license.txt

frappe.query_reports["Payroll GL Reconciliation"] = {
	filters: [
		{
			fieldname: "payroll_entry",
			label: __("Payroll Entry"),
			fieldtype: "Link",
			options: "Payroll Entry",
			reqd: 1,
		},
		{
			fieldname: "only_differences",
			label: __("Only Lines That Disagree"),
			fieldtype: "Check",
			default: 0,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		// The difference column is the whole point of the report, so a line
		// that does not reconcile has to be visible without reading every row.
		if (data && column.fieldname === "difference" && Math.abs(flt(data.difference)) >= 0.01) {
			value = `<span style="color:var(--red-600);font-weight:600">${value}</span>`;
		}
		if (data && data.is_section) {
			value = `<span style="font-weight:600">${value}</span>`;
		}
		return value;
	},
};
