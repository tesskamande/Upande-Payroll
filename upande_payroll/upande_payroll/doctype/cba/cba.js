// Copyright (c) 2026, Teresia and contributors
// For license information, please see license.txt

frappe.ui.form.on("CBA Pay Table", {
	percentage_increase(frm, cdt, cdn) {
		update_row_calc(frm, cdt, cdn);
	},
	current_basic_pay(frm, cdt, cdn) {
		update_row_calc(frm, cdt, cdn);
	},
});

function update_row_calc(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	const current_basic_pay = row.current_basic_pay || 0;
	const percentage_increase = row.percentage_increase || 0;
	const increase_amount = Math.ceil((current_basic_pay * percentage_increase) / 100);

	frappe.model.set_value(cdt, cdn, "increase_amount", increase_amount);
	frappe.model.set_value(cdt, cdn, "new_basic_pay", current_basic_pay + increase_amount);
}

frappe.ui.form.on("CBA", {
	refresh(frm) {
		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(
				__("Apply CBA to Employees"),
				() => {
					frappe.confirm(
						__("This will update the Basic Pay for all employees. Continue?"),
						() => {
							frappe.show_alert({ message: __("Applying CBA to employees..."), indicator: "blue" });
							frappe.call({
								method: "upande_payroll.cba_utils.apply_cba_to_employees",
								args: { cba_name: frm.doc.name },
								callback: (r) => {
									if (r.message && r.message.success) {
										frappe.msgprint({
											title: __("CBA Applied"),
											message: r.message.message,
											indicator: "green",
										});
										frm.reload_doc();
									}
								},
							});
						}
					);
				},
				__("Actions")
			);
		}
	},
});
