// Copyright (c) 2026, Teresia and contributors
// For license information, please see license.txt

frappe.ui.form.on("Leave Provision", {
	setup(frm) {
		// Only accounts that can actually take the posting. Frappe would happily
		// let Accounts Receivable be chosen as the leave expense otherwise.
		frm.set_query("expense_account", () => ({
			filters: { company: frm.doc.company, root_type: "Expense", is_group: 0 },
		}));
		frm.set_query("liability_account", () => ({
			filters: { company: frm.doc.company, root_type: "Liability", is_group: 0 },
		}));
		frm.set_query("basic_pay_component", () => ({
			filters: { type: "Earning" },
		}));
	},

	company(frm) {
		// Everything below comes from the company, so clear it and let the
		// server fill it in again rather than carrying the old company's setup.
		["basic_pay_component", "divisor", "expense_account",
		 "liability_account"].forEach((field) => frm.set_value(field, null));
		frm.clear_table("leave_types");
		frm.refresh_field("leave_types");
	},
});
