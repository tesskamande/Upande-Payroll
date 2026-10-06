// Copyright (c) 2026, Teresia and contributors
// For license information, please see license.txt

frappe.ui.form.on("Payroll Entry", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1) {
			return;
		}

		// Only offer it where the company actually books leave provision -
		// otherwise this button leads to a Leave Provision that has nowhere
		// configured to post its accrual.
		frappe.db.get_value(
			"Company Payroll Settings",
			frm.doc.company,
			"enable_leave_provision"
		).then(({ message }) => {
			if (!message || !message.enable_leave_provision) {
				return;
			}
			// The provision values leave as it stands at the end of the payroll
			// period, so the two belong together. Starting it from here means
			// the dates come off the payroll run rather than being typed
			// again, which is where a period gets mistyped and the wrong
			// month provisioned.
			frm.add_custom_button(
				__("Leave Provision"),
				() => open_leave_provision(frm),
				__("Create")
			);
		});

		// Only where the app is even installed, and only for the companies
		// that actually use it - most companies running payroll here don't,
		// and the button would otherwise lead to an empty queue every time.
		if (frappe.boot.versions && frappe.boot.versions.work_management) {
			frappe.db.get_value(
				"Company Payroll Settings",
				frm.doc.company,
				"enable_work_management_link"
			).then(({ message }) => {
				if (!message || !message.enable_work_management_link) {
					return;
				}
				frm.add_custom_button(__("Awaiting Payroll (Work Mgmt)"), () => {
					window.open("/work-payment#accounts", "_blank");
				});
			});
		}
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

	/*
	 * validate_attendance is a real field-change event, not a direct
	 * frm.events.x(frm) call like add_bank_entry_button above - the script
	 * manager runs every registered handler for it in turn (script_manager.js
	 * trigger()), hrms's own first since it loads first, this one after. So
	 * both do run; this one simply re-renders the same result a second time,
	 * replacing hrms's table with one whose link goes to the Missing
	 * Attendance report (the exact missing employee+day pairs) instead of the
	 * Monthly Attendance Sheet (a full grid with no missing-day highlight,
	 * ../../hrms/public/js/templates/employees_with_unmarked_attendance.html).
	 * The extra get_employees_with_unmarked_attendance call this causes is the
	 * cost of not touching hrms's own file to make the swap.
	 */
	validate_attendance(frm) {
		if (!frm.doc.validate_attendance || !(frm.doc.employees || []).length) {
			return;
		}
		frappe.call({
			method: "get_employees_with_unmarked_attendance",
			args: {},
			doc: frm.doc,
			freeze: true,
			freeze_message: __("Validating Employee Attendance..."),
			callback(r) {
				render_missing_attendance(frm, r.message);
			},
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

/*
 * attendance_detail_html is never saved - it's only ever set as a side
 * effect of the validate_attendance event firing (a checkbox toggle, or
 * get_employee_details() re-running it). So a Payroll Entry with Validate
 * Attendance already checked loses the box the moment the form is loaded
 * fresh rather than toggled - following the Missing Attendance report link
 * out and back is exactly that: the form reloads from the server, and the
 * checkbox's own saved value doesn't re-trigger anything on its own.
 *
 * Registered as its own frappe.ui.form.on call (not added into the refresh
 * above) because that one returns early for anything not docstatus 1, and
 * this needs to run on a Draft just as much - Validate Attendance only
 * matters before a Payroll Entry is submitted. script_manager.js runs every
 * registered handler for an event, so a second "refresh" registration here
 * runs alongside, not instead of, the one above.
 */
frappe.ui.form.on("Payroll Entry", "refresh", function (frm) {
	if (frm.is_new() || !frm.doc.validate_attendance || !(frm.doc.employees || []).length) {
		return;
	}
	const box = frm.fields_dict.attendance_detail_html;
	if (!box || (box.$wrapper.html() || "").trim()) {
		return;
	}
	frm.trigger("validate_attendance");
});

function render_missing_attendance(frm, data) {
	if (!data || !data.length) {
		frm.fields_dict.attendance_detail_html.html(
			`<div class="form-message green"><div>${__(
				"Attendance has been marked for all the employees between the selected payroll dates."
			)}</div></div>`
		);
		return;
	}

	const report_url =
		"/app/query-report/Missing%20Attendance?payroll_entry=" + encodeURIComponent(frm.doc.name);
	const link = `<a href="${report_url}">${__("Missing Attendance")}</a>`;
	const rows = data
		.map(
			(d) => `
		<tr>
			<td class="text-left">${frappe.utils.escape_html(d.employee)}</td>
			<td class="text-left">${frappe.utils.escape_html(d.employee_name)}</td>
			<td class="text-left">${d.unmarked_days}</td>
		</tr>`
		)
		.join("");

	frm.fields_dict.attendance_detail_html.html(`
		<div class="form-message yellow">
			<div>${__(
				"Attendance is pending for these employees between the selected payroll dates. Mark attendance to proceed. Refer {0} for the exact missing days.",
				[link]
			)}</div>
		</div>
		<table class="table table-bordered small">
			<thead>
				<tr>
					<th style="width: 14%" class="text-left">${__("Employee")}</th>
					<th style="width: 16%" class="text-left">${__("Employee Name")}</th>
					<th style="width: 12%" class="text-left">${__("Unmarked Days")}</th>
				</tr>
			</thead>
			<tbody>${rows}</tbody>
		</table>
	`);
}

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
