// Copyright (c) 2026, Teresia and contributors
// For license information, please see license.txt

frappe.query_reports["Missing Attendance"] = {
	filters: [
		{
			fieldname: "payroll_entry",
			label: __("Payroll Entry"),
			fieldtype: "Link",
			options: "Payroll Entry",
			reqd: 1,
		},
		{
			fieldname: "employee",
			label: __("Employee"),
			fieldtype: "Link",
			options: "Employee",
		},
		{
			fieldname: "department",
			label: __("Department"),
			fieldtype: "Link",
			options: "Department",
		},
	],

	// Day columns are named "dd-mm-yyyy" (missing_attendance.py get_columns) -
	// the same shape Monthly Attendance Sheet uses for its own day columns.
	// A blank cell is a marked day (present, absent, on leave, a holiday -
	// anything already accounted for); "X" is the one value this report
	// ever puts in a day column, so it is also the only one styled.
	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (/^\d{2}-\d{2}-\d{4}$/.test(column.fieldname) && data && data[column.fieldname] === "X") {
			value =
				"<span style='background-color:#fee2e2;color:#991b1b;font-weight:700;" +
				"padding:1px 7px;border-radius:3px'>&#10005;</span>";
		}
		return value;
	},
};
