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
	company(frm) {
		carry_forward_rates(frm);
	},

	effective_start_date(frm) {
		carry_forward_rates(frm);
	},

	refresh(frm) {
		// Once applied, the button goes. Pressing it again would add the
		// increase a second time, and there is nothing to catch up on: the
		// Employee form will not accept anyone below the agreed rate.
		if (frm.doc.docstatus === 1 && !frm.doc.applied_on) {
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

// Fill the pay table from the company's last agreement, so what has to be
// entered is this round's percentage rather than every rate from scratch.
// Only ever on an empty table - rates already entered are left alone.
function carry_forward_rates(frm) {
	// Both are needed before the source can be chosen. Filling on the company
	// alone would take the most recent agreement, which is the wrong one for a
	// round being backdated - so it waits for the start date.
	if (!frm.doc.company || !frm.doc.effective_start_date) return;
	if ((frm.doc.table_dqro || []).length) return;

	frappe.call({
		method: "upande_payroll.upande_payroll.doctype.cba.cba.previous_rates",
		args: {
			company: frm.doc.company,
			before: frm.doc.effective_start_date,
			exclude: frm.doc.name,
		},
		callback: (r) => {
			const rows = r.message || [];
			if (!rows.length) {
				frappe.show_alert({
					message: __("No agreement runs before {0} for this company - the pay table starts empty.", [
						frappe.datetime.str_to_user(frm.doc.effective_start_date),
					]),
					indicator: "orange",
				});
				return;
			}

			rows.forEach((row) => {
				const child = frm.add_child("table_dqro");
				child.job_category = row.job_category;
				child.current_basic_pay = row.current_basic_pay;
			});
			frm.refresh_field("table_dqro");
			frappe.show_alert({
				message: __("Rates carried forward from {0}, effective {1}.", [
					rows[0].source,
					frappe.datetime.str_to_user(rows[0].source_from),
				]),
				indicator: "green",
			});
		},
	});
}
