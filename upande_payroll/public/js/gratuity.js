frappe.ui.form.on("Gratuity", {
	refresh(frm) {
		frm.trigger("auto_fill_salary_component");
	},
	company(frm) {
		frm.trigger("auto_fill_salary_component");
	},
	pay_via_salary_slip(frm) {
		if (frm.doc.pay_via_salary_slip && frm.doc.custom_pay_via_terminal_dues) {
			frm.set_value("custom_pay_via_terminal_dues", 0);
		}
		frm.trigger("auto_fill_salary_component");
	},
	custom_pay_via_terminal_dues(frm) {
		if (frm.doc.custom_pay_via_terminal_dues && frm.doc.pay_via_salary_slip) {
			frm.set_value("pay_via_salary_slip", 0);
		}
		frm.trigger("auto_fill_salary_component");
	},
	auto_fill_salary_component(frm) {
		// Mandatory-ness on Salary Component is checked client-side before save is
		// even attempted, so it must be filled here too - the server-side auto-fill
		// in gratuity_utils.py runs too late to satisfy that client-side gate.
		if (frm.doc.salary_component || !frm.doc.company) return;
		if (!frm.doc.pay_via_salary_slip && !frm.doc.custom_pay_via_terminal_dues) return;

		frappe.db.get_value(
			"Company Payroll Settings",
			frm.doc.company,
			"gratuity_salary_component"
		).then((r) => {
			if (r.message && r.message.gratuity_salary_component) {
				frm.set_value("salary_component", r.message.gratuity_salary_component);
			}
		});
	},
});
