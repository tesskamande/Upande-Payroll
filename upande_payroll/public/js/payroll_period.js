// Copyright (c) 2026, Teresia and contributors
// For license information, please see license.txt

// The Payroll Periods table only matters for CBA's own pause/resume
// mechanism (cba_utils.py) - a company that does not operate under a CBA at
// all has nothing reading these rows, so the section stays out of the way
// rather than asking somebody to maintain cycle rows that serve nothing.
//
// Guided by Company Payroll Settings' CBA Applicable - a stable fact about
// the company, not whether a specific CBA document happens to be submitted
// right now (see employee.js for the same reasoning on Job Category).
frappe.ui.form.on("Payroll Period", {
	refresh(frm) {
		apply_cba_visibility(frm);
	},

	company(frm) {
		frm.__cba_applicable = null;
		apply_cba_visibility(frm);
	},
});

async function apply_cba_visibility(frm) {
	const show = await company_cba_applicable(frm);
	frm.toggle_display("section_break_5", show);
	frm.toggle_display("periods", show);
}

async function company_cba_applicable(frm) {
	if (!frm.doc.company) return false;
	if (frm.__cba_applicable?.__company === frm.doc.company) {
		return frm.__cba_applicable.value;
	}

	const response = await frappe.db.get_value(
		"Company Payroll Settings", frm.doc.company, "cba_applicable"
	);
	const value = response?.message?.cba_applicable == 1;
	frm.__cba_applicable = { value, __company: frm.doc.company };
	return value;
}
