// Copyright (c) 2026, Teresia and contributors
// For license information, please see license.txt

frappe.ui.form.on("Payroll Entry", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1) {
			return;
		}

		// The provision values leave as it stands at the end of the payroll
		// period, so the two belong together. Starting it from here means the
		// dates come off the payroll run rather than being typed again, which
		// is where a period gets mistyped and the wrong month provisioned.
		frm.add_custom_button(
			__("Leave Provision"),
			() => open_leave_provision(frm),
			__("Create")
		);

	},

	/*
	 * Reuses HRMS's own Release Withheld Salaries button rather than adding
	 * another one.
	 *
	 * add_context_buttons() calls frm.events.add_bank_entry_button(frm), and
	 * frappe.ui.form.on assigns the LAST registered handler to frm.events. A
	 * doctype's own script is loaded first and doctype_js hooks are appended
	 * after it (frappe/desk/form/meta.py:95 then :111), so this definition wins
	 * and the button is built here.
	 *
	 * Make Bank Entry is left exactly as HRMS has it. Only the withheld path
	 * changes, and only to ask who is being released before writing a journal
	 * that pays them.
	 */
	add_bank_entry_button(frm) {
		frm.call("has_bank_entries").then((r) => {
			if (!r.message) return;

			if (!r.message.has_bank_entries) {
				frm.add_custom_button(__("Make Bank Entry"), () =>
					frm.events.upande_make_bank_entry(frm, 0)
				).addClass("btn-primary");
			} else if (!r.message.has_bank_entries_for_withheld_salaries) {
				frm.add_custom_button(__("Release Withheld Salaries"), () =>
					open_release_dialog(frm)
				).addClass("btn-primary");
			}
		});
	},

	// HRMS keeps its own make_bank_entry as a module-local function, so it
	// cannot be called from here. This is the same call it makes.
	upande_make_bank_entry(frm, for_withheld_salaries) {
		if (!frm.doc.payment_account) {
			frappe.msgprint(__("Payment Account is mandatory"));
			frm.scroll_to_field("payment_account");
			return;
		}
		return frappe.call({
			method: "run_doc_method",
			args: {
				method: "make_bank_entry",
				dt: "Payroll Entry",
				dn: frm.doc.name,
				args: { for_withheld_salaries: for_withheld_salaries },
			},
			freeze: true,
			freeze_message: __("Creating Payment Entries......"),
			callback: () => {
				frappe.set_route("List", "Journal Entry", {
					"Journal Entry Account.reference_name": frm.doc.name,
				});
			},
		});
	},
});

function open_release_dialog(frm) {
	frm.call({ method: "withheld_employees", doc: frm.doc }).then((r) => {
		const rows = r.message || [];
		if (!rows.length) {
			frappe.msgprint({
				message: __("No withheld salaries left to release on this run."),
				indicator: "green",
			});
			return;
		}

		const dialog = new frappe.ui.Dialog({
			title: __("Release Withheld Salaries"),
			size: "large",
			fields: [
				{
					fieldname: "employees",
					fieldtype: "Table",
					label: __("Withheld this period"),
					cannot_add_rows: true,
					cannot_delete_rows: true,
					in_place_edit: false,
					data: rows.map((row) => ({
						release: 1,
						employee: row.employee,
						employee_name: row.employee_name,
						net_pay: row.net_pay,
					})),
					get_data: () => dialog.fields_dict.employees.grid.data,
					fields: [
						{ fieldname: "release", fieldtype: "Check", label: __("Release"),
						  in_list_view: 1, columns: 1, default: 1 },
						{ fieldname: "employee", fieldtype: "Data", label: __("ID"),
						  in_list_view: 1, columns: 2, read_only: 1 },
						{ fieldname: "employee_name", fieldtype: "Data", label: __("Employee"),
						  in_list_view: 1, columns: 5, read_only: 1 },
						{ fieldname: "net_pay", fieldtype: "Currency", label: __("Net Pay"),
						  in_list_view: 1, columns: 3, read_only: 1 },
					],
				},
			],
			primary_action_label: __("Create Bank Entry"),
			primary_action() {
				const picked = (dialog.fields_dict.employees.grid.get_data() || [])
					.filter((row) => row.release)
					.map((row) => row.employee);

				if (!picked.length) {
					frappe.msgprint(__("Tick at least one employee to release."));
					return;
				}

				dialog.hide();
				frm.call({
					method: "release_withheld_salaries",
					doc: frm.doc,
					args: { employees: JSON.stringify(picked) },
					freeze: true,
					freeze_message: __("Creating the bank entry..."),
				}).then((res) => {
					if (!res.message) return;
					frappe.show_alert({
						message: __("{0} created for {1} employee(s). Submit it to release the salaries.",
									[res.message, picked.length]),
						indicator: "green",
					}, 10);
					frappe.set_route("Form", "Journal Entry", res.message);
				});
			},
		});
		dialog.show();
	});
}

function open_leave_provision(frm) {
	frappe.db
		.get_list("Leave Provision", {
			filters: {
				company: frm.doc.company,
				from_date: frm.doc.start_date,
				to_date: frm.doc.end_date,
				docstatus: ["<", 2],
			},
			fields: ["name", "docstatus"],
			limit: 1,
		})
		.then((existing) => {
			if (existing && existing.length) {
				// One already covers this period. Open it rather than starting a
				// second that would only be refused for overlapping.
				frappe.set_route("Form", "Leave Provision", existing[0].name);
				frappe.show_alert({
					message: __("This period already has a leave provision."),
					indicator: "blue",
				});
				return;
			}

			frappe.new_doc("Leave Provision", {
				company: frm.doc.company,
				from_date: frm.doc.start_date,
				to_date: frm.doc.end_date,
			});
		});
}

// The advanced filter box, the same one the Bulk Salary Structure Assignment
// tool uses. Payroll Entry ships fixed filters for branch, department,
// designation and grade; this covers everything else the Employee record
// carries, for the runs those four cannot describe.
frappe.ui.form.on("Payroll Entry", {
	setup(frm) {
		setup_advanced_filters(frm);
	},
});

function setup_advanced_filters(frm) {
	const wrapper = frm.fields_dict.filter_list?.$wrapper;
	if (!wrapper) return;
	wrapper.empty();

	frappe.model.with_doctype("Employee", () => {
		frm.employee_filter_group = new frappe.ui.FilterGroup({
			parent: wrapper,
			doctype: "Employee",
			on_change: () => {
				// [doctype, fieldname, condition, value] - the server wants the
				// last three. A row still being built has no value yet, so it is
				// left out rather than sent as a condition matching nothing.
				const filters = frm.employee_filter_group
					.get_filters()
					.filter((row) => row[3])
					.map((row) => row.slice(1, 4));

				frm.set_value(
					"advanced_employee_filters",
					filters.length ? JSON.stringify(filters) : ""
				);
				// Same refresh the built-in filters use, so the employee list
				// and the count stay in step with the box.
				frm.trigger("get_employee_details");
			},
		});
	});
}
