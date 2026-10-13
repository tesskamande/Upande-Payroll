// Copyright (c) 2026, Teresia and contributors
// For license information, please see license.txt

// Fields that only make sense under a particular Company Payroll Settings
// value, and are hidden otherwise.
//
// depends_on cannot do this: it is evaluated against the document on screen,
// and the setting lives on another doctype altogether. So the setting is read
// for the employee's company and the field toggled here.
//
// Add a rule to this list rather than writing another handler.
const COMPANY_SETTING_RULES = [
	{
		field: "custom_salary_expense_account",
		setting: "gross_pay_account_method",
		show_when: ["Per Employee"],
	},
	// CBA Applicable is a stable fact about the company (does it bargain
	// collectively at all), not whether a particular CBA document happens to
	// be submitted right now - a company between agreements, or one not yet
	// finished drafting its first, should not have Job Category flicker out
	// of view just because nothing is submitted at this exact moment.
	{
		field: "job_category",
		setting: "cba_applicable",
		show_when: [1],
	},
];

frappe.ui.form.on("Employee", {
	refresh(frm) {
		apply_company_setting_rules(frm);
	},

	company(frm) {
		// The company decides which settings apply, so the answer changes with
		// it. Cached figure dropped first or the old company's answer sticks.
		frm.__payroll_settings = null;
		apply_company_setting_rules(frm);
	},
});

async function apply_company_setting_rules(frm) {
	const settings = await get_payroll_settings(frm);

	for (const rule of COMPANY_SETTING_RULES) {
		// No company, or a company with no payroll settings, means there is
		// nothing to say the field applies - so it stays hidden. Hiding a field
		// that turns out to be relevant is a question; showing one that is not
		// invites an account that will never be posted to.
		const value = settings ? settings[rule.setting] : null;
		frm.toggle_display(rule.field, rule.show_when.includes(value));
	}
}

async function get_payroll_settings(frm) {
	if (!frm.doc.company) return null;
	if (frm.__payroll_settings?.__company === frm.doc.company) {
		return frm.__payroll_settings;
	}

	const fields = [...new Set(COMPANY_SETTING_RULES.map((r) => r.setting))];
	const response = await frappe.db.get_value(
		"Company Payroll Settings",
		frm.doc.company,
		fields
	);

	// get_value returns an empty message rather than an error when the company
	// has no settings record at all, which is a perfectly normal state on a
	// site part way through being set up.
	const values = response?.message;
	if (!values || !Object.keys(values).length) return null;

	frm.__payroll_settings = { ...values, __company: frm.doc.company };
	return frm.__payroll_settings;
}
