frappe.ui.form.on("Salary Slip", {
	refresh(frm) {
		frm.trigger("fetch_personal_relief_method");
	},

	company(frm) {
		frm.trigger("fetch_personal_relief_method");
	},

	fetch_personal_relief_method(frm) {
		if (!frm.doc.company) return;
		// Only ever fill this in on a slip that hasn't been saved yet. A saved
		// slip records the method that was actually applied when it ran, and the
		// company setting may well have changed since - overwriting it here
		// would hide the carry-forward ledger on historical slips that genuinely
		// used it, and would dirty a submitted document.
		if (!frm.is_new()) return;
		frappe.db.get_value(
			"Company Payroll Settings", frm.doc.company,
			["enable_taxable_income_calculation", "personal_relief_method"],
			(r) => {
				if (r && r.enable_taxable_income_calculation) {
					frm.set_value("custom_personal_relief_method", r.personal_relief_method || "");
				} else {
					frm.set_value("custom_personal_relief_method", "");
				}
			}
		);
	},
});
