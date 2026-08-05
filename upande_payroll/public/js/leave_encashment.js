frappe.ui.form.on("Leave Encashment", {
	employee(frm) {
		frm.trigger("recalculate_amount");
	},
	encashment_days(frm) {
		frm.trigger("recalculate_amount");
	},
	pay_via_payment_entry(frm) {
		if (frm.doc.pay_via_payment_entry && frm.doc.custom_pay_via_terminal_dues) {
			frm.set_value("custom_pay_via_terminal_dues", 0);
		}
	},
	custom_pay_via_terminal_dues(frm) {
		if (frm.doc.custom_pay_via_terminal_dues && frm.doc.pay_via_payment_entry) {
			frm.set_value("pay_via_payment_entry", 0);
		}
	},

	recalculate_amount(frm) {
		// Live preview only - the authoritative amount is set server-side in
		// leave_encashment_utils.py, keeping this in sync with Basic Pay / divisor
		// instead of the core Salary Structure per-day rate.
		if (!frm.doc.employee || !frm.doc.company) return;

		frappe.db.get_value("Company Payroll Settings", frm.doc.company, [
			"enable_leave_encashment_calculation",
			"leave_encashment_divisor",
		]).then((r) => {
			if (!r.message || !r.message.enable_leave_encashment_calculation) return;
			const divisor = r.message.leave_encashment_divisor || 26;

			frappe.db.get_value("Employee", frm.doc.employee, "basic_pay").then((e) => {
				const basic_pay = (e.message && e.message.basic_pay) || 0;
				const days = frm.doc.encashment_days || 0;
				frm.set_value("encashment_amount", Math.round((basic_pay / divisor) * days * 100) / 100);
			});
		});
	},
});
