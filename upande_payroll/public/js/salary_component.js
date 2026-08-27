// Copyright (c) 2026, Teresia and contributors
// For license information, please see license.txt

frappe.ui.form.on("Salary Component", {
	setup: function (frm) {
		// The same filter HRMS puts on the Account field beside it. Both fields
		// sit on one row of the accounts table, and that row names the company -
		// so an account from another company's chart is never a valid choice,
		// and offering it only invites a Journal Entry that will not post.
		frm.set_query("custom_employer_expense_account", "accounts", function (doc, cdt, cdn) {
			const row = locals[cdt][cdn];
			return {
				filters: {
					is_group: 0,
					company: row.company,
				},
			};
		});
	},
});
