// The list view was showing "Draft" for a withheld payslip. Salary Slip has a
// status field that already says Withheld - get_status() returns it ahead of
// Draft and Submitted - but HRMS defines no get_indicator, so the list falls
// back to Frappe's default, which colours submittable documents by docstatus
// alone. The form said Withheld and the list said Draft.
//
// Extended rather than reassigned: hrms/salary_slip_list.js sets this object
// up with its own onload, and replacing it would drop the Email Salary Slips
// menu item.
frappe.listview_settings["Salary Slip"] = frappe.listview_settings["Salary Slip"] || {};

Object.assign(frappe.listview_settings["Salary Slip"], {
	get_indicator: function (doc) {
		if (doc.status === "Withheld") {
			return [__("Withheld"), "orange", "status,=,Withheld"];
		}
		if (doc.docstatus === 2) {
			return [__("Cancelled"), "red", "docstatus,=,2"];
		}
		if (doc.docstatus === 1) {
			return [__("Submitted"), "blue", "docstatus,=,1"];
		}
		return [__("Draft"), "gray", "docstatus,=,0"];
	},
});
