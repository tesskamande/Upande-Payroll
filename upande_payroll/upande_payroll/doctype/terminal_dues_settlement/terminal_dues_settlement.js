frappe.ui.form.on("Terminal Dues Settlement", {

	refresh(frm) {
		frm.trigger("set_action_buttons");
	},

	set_action_buttons(frm) {
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Re-fetch Dues"), () => {
				if (!frm.doc.employee || !frm.doc.relieving_date || !frm.doc.payroll_period_start) {
					frappe.msgprint(__("Please set Employee, Relieving Date and Payroll Period Start first."));
					return;
				}
				frappe.confirm(
					__("This will clear and re-populate all earnings. PAYE and notice pay will also be recalculated. Continue?"),
					() => {
						const do_fetch = () => {
							frappe.call({
								method: "fetch_dues",
								doc: frm.doc,
								freeze: true,
								freeze_message: __("Fetching dues…"),
								callback(r) {
									if (!r.exc) frm.reload_doc();
								},
							});
						};
						if (frm.is_dirty() || frm.doc.__islocal) {
							frm.save("Save", do_fetch);
						} else {
							do_fetch();
						}
					}
				);
			}, __("Actions"));
		}

		if (frm.doc.docstatus === 1) {
			const jv_label = frm.doc.journal_entry ? __("View JV") : __("Create JV");
			frm.add_custom_button(jv_label, () => {
				if (frm.doc.journal_entry) {
					frappe.set_route("Form", "Journal Entry", frm.doc.journal_entry);
					return;
				}
				frappe.call({
					method: "create_journal_entry",
					doc: frm.doc,
					freeze: true,
					freeze_message: __("Creating Journal Entry…"),
					callback(r) {
						if (!r.exc && r.message) {
							frappe.show_alert({
								message: __("Journal Entry {0} created.", [r.message]),
								indicator: "green",
							});
							frm.reload_doc();
						}
					},
				});
			}, __("Actions"));
		}
	},

	employee(frm) {
		if (!frm.doc.employee) return;
		frm.set_value(
			"company",
			frappe.defaults.get_user_default("Company") ||
			frappe.defaults.get_global_default("company")
		);

		frappe.db.get_value("Employee", frm.doc.employee,
			["relieving_date", "employee_name"], (data) => {

			if (data && data.relieving_date) {
				frm.set_value("relieving_date", data.relieving_date);
				frm.trigger("suggest_payroll_start");
				return;
			}

			const d = new frappe.ui.Dialog({
				title: __("Set Relieving Date for {0}", [data && data.employee_name || frm.doc.employee]),
				fields: [
					{
						fieldname: "relieving_date",
						fieldtype: "Date",
						label: __("Relieving Date"),
						reqd: 1,
					},
					{
						fieldname: "note",
						fieldtype: "HTML",
						options: `<p class="text-muted small">This will update the Employee record.
							Make sure the employee's status is also set to <b>Left</b>.</p>`,
					},
				],
				primary_action_label: __("Save & Continue"),
				primary_action(values) {
					frappe.call({
						method: "frappe.client.set_value",
						args: {
							doctype: "Employee",
							name: frm.doc.employee,
							fieldname: "relieving_date",
							value: values.relieving_date,
						},
						callback(r) {
							if (!r.exc) {
								frm.set_value("relieving_date", values.relieving_date);
								frm.trigger("suggest_payroll_start");
								frappe.show_alert({
									message: __("Relieving date saved on Employee record."),
									indicator: "green",
								});
							}
						},
					});
					d.hide();
				},
			});
			d.show();
		});
	},

	relieving_date(frm) {
		frm.trigger("validate_period");
		frm.trigger("suggest_payroll_start");
		frm.trigger("compute_notice_days_served");
	},

	resignation_letter_date(frm) {
		frm.trigger("compute_notice_days_served");
	},

	compute_notice_days_served(frm) {
		if (!frm.doc.resignation_letter_date || !frm.doc.relieving_date) return;
		const days = frappe.datetime.get_diff(frm.doc.relieving_date, frm.doc.resignation_letter_date);
		if (days >= 0) {
			frm.set_value("notice_days_served", days);
		}
	},

	suggest_payroll_start(frm) {
		if (!frm.doc.employee) {
			if (frm.doc.relieving_date && !frm.doc.payroll_period_start) {
				const rd = new Date(frm.doc.relieving_date);
				frm.set_value("payroll_period_start",
					frappe.datetime.obj_to_str(new Date(rd.getFullYear(), rd.getMonth(), 1)));
			}
			return;
		}
		frappe.call({
			method: "upande_payroll.upande_payroll.doctype.terminal_dues_settlement.terminal_dues_settlement.get_suggested_payroll_start",
			args: { employee: frm.doc.employee },
			callback(r) {
				if (!r.exc && r.message) {
					frm.set_value("payroll_period_start", r.message);
				} else if (!frm.doc.payroll_period_start && frm.doc.relieving_date) {
					const rd = new Date(frm.doc.relieving_date);
					frm.set_value("payroll_period_start",
						frappe.datetime.obj_to_str(new Date(rd.getFullYear(), rd.getMonth(), 1)));
				}
			},
		});
	},

	payroll_period_start(frm) {
		frm.trigger("validate_period");
	},

	validate_period(frm) {
		if (!frm.doc.payroll_period_start || !frm.doc.relieving_date) return;
		if (frm.doc.payroll_period_start > frm.doc.relieving_date) {
			frappe.msgprint({
				title: __("Invalid Dates"),
				message: __("Payroll Period Start cannot be after the Relieving Date."),
				indicator: "red",
			});
			frm.set_value("payroll_period_start", "");
		}
	},

	notice_direction(frm) {
		frm.set_intro(
			frm.doc.notice_direction
				? __("Notice pay will be calculated on save as the shortfall between the required Notice Days (Company Payroll Settings, based on tenure) and Notice Days Served below.")
				: "",
			"blue"
		);
	},
});

// Live total recompute when rows change client-side
frappe.ui.form.on("Terminal Dues Earning", {
	amount(frm)          { _recompute_totals(frm); },
	earnings_remove(frm) { _recompute_totals(frm); },
});

frappe.ui.form.on("Terminal Dues Deduction", {
	amount(frm)             { _recompute_totals(frm); },
	deductions_remove(frm)  { _recompute_totals(frm); },
});

function _recompute_totals(frm) {
	const e = (frm.doc.earnings   || []).reduce((s, r) => s + (r.amount || 0), 0);
	const d = (frm.doc.deductions || []).reduce((s, r) => s + (r.amount || 0), 0);
	frm.set_value("total_earnings",   e);
	frm.set_value("total_deductions", d);
	frm.set_value("net_payable",      e - d);
}
