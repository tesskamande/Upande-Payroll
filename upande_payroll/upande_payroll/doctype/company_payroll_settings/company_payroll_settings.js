// Copyright (c) 2026, Teresia and contributors
// For license information, please see license.txt

frappe.ui.form.on("Company Payroll Settings", {
	refresh(frm) {
		// Only real bank and cash accounts of this company can settle a
		// remittance - the group headings above them cannot be posted to.
		frm.set_query("liability_remittance_payment_account", () => bank_filter(frm));
		frm.set_query("payment_account", "payroll_remittance_accounts", () => bank_filter(frm));

		// Deductions only. An earning is never owed to anybody, so offering the
		// whole component list would only invite a row that can never match
		// anything the journal credits.
		frm.set_query("salary_component", "payroll_remittance_accounts", () => ({
			filters: { type: "Deduction" },
		}));

		// The direct route, for what has no component behind it - net pay, or a
		// loan account written by the slip rather than a salary component.
		frm.set_query("liability_account", "payroll_remittance_accounts", () => ({
			filters: { company: frm.doc.company, is_group: 0, root_type: "Liability" },
		}));

		// Each statutory category is read from one side of the payslip only, so
		// the component list is narrowed to that side - picking the wrong one
		// produces a row nothing ever reads. The table comes from the server so
		// the form and the calculator cannot drift apart.
		frappe.call("upande_payroll.kenya_statutory_gross_pay.category_component_type")
			.then((r) => {
				frm.__category_component_type = r.message || {};
			});

		frm.set_query("salary_component", "statutory_income_component_mapping", (doc, cdt, cdn) => {
			const type = component_type_for(frm, locals[cdt][cdn].category);
			return type ? { filters: { type: type } } : {};
		});
	},
});

frappe.ui.form.on("Statutory Income Component Mapping", {
	category(frm, cdt, cdn) {
		// Changing the category can move the row to the other side of the
		// payslip, leaving a component behind that the new category will never
		// be read against. Dropped rather than left to fail on save.
		const row = locals[cdt][cdn];
		const type = component_type_for(frm, row.category);
		if (!type || !row.salary_component) {
			return;
		}

		frappe.db.get_value("Salary Component", row.salary_component, "type").then((r) => {
			if (r.message && r.message.type && r.message.type !== type) {
				frappe.model.set_value(cdt, cdn, "salary_component", null);
				frappe.show_alert({
					message: __("{0} is read from the payslip's {1}s, so pick one of those.",
								[row.category, type.toLowerCase()]),
					indicator: "orange",
				});
			}
		});
	},
});

function component_type_for(frm, category) {
	return (category && (frm.__category_component_type || {})[category]) || null;
}

frappe.ui.form.on("Payroll Remittance Account", {
	salary_component(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.salary_component) {
			frappe.model.set_value(cdt, cdn, "liability_account", null);
			return;
		}
		if (!frm.doc.company) {
			frappe.msgprint(__("Set the Company first."));
			return;
		}

		frappe.call({
			method: "upande_payroll.liability_remittance.component_account",
			args: { company: frm.doc.company, salary_component: row.salary_component },
		}).then((r) => {
			if (!r.message) {
				frappe.msgprint(
					__("{0} has no Account set for {1}. Set it on the Salary Component, then pick it again here.",
					   [row.salary_component, frm.doc.company])
				);
				frappe.model.set_value(cdt, cdn, "salary_component", null);
				return;
			}

			// Said before saving rather than as a validation error afterwards:
			// several components share one account, so landing on one that is
			// already in the table is an ordinary thing to do by accident.
			const clash = (frm.doc.payroll_remittance_accounts || []).find(
				(other) => other.name !== row.name && other.liability_account === r.message
			);
			if (clash) {
				frappe.msgprint(
					__("{0} posts to {1}, which row {2} ({3}) already covers. One row settles that account for all of them.",
					   [row.salary_component, r.message, clash.idx,
						clash.salary_component || clash.liability_account])
				);
				frappe.model.set_value(cdt, cdn, "salary_component", null);
				return;
			}

			frappe.model.set_value(cdt, cdn, "liability_account", r.message);
		});
	},
});

function bank_filter(frm) {
	return {
		filters: {
			company: frm.doc.company,
			is_group: 0,
			account_type: ["in", ["Bank", "Cash"]],
		},
	};
}
