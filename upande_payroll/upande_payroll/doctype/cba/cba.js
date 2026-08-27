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
			frm.add_custom_button(__("Apply CBA to Employees"), () => confirm_apply(frm), __("Actions"));
		}

		// Once it has been applied there is a record of what that did, and the
		// only way to reach it was to know the report existed. Offered from the
		// agreement itself, already filtered to it.
		if (frm.doc.applied_on) {
			frm.add_custom_button(
				__("Application Log"),
				() => frappe.set_route("query-report", "CBA Application Log", { cba: frm.doc.name }),
				__("View")
			);
		}
	},
});

// Count first, then ask. "This will update all employees" is not something
// anyone can reasonably agree to without knowing who that is, so the count and
// the per-category breakdown go in the question itself.
function confirm_apply(frm) {
	frappe.call({
		method: "upande_payroll.cba_utils.preview_cba_impact",
		args: { cba_name: frm.doc.name },
		freeze: true,
		freeze_message: __("Checking who this affects..."),
		callback: (r) => {
			if (!r.message) return;
			const impact = r.message;

			if (!impact.total) {
				frappe.msgprint({
					title: __("Nobody to apply to"),
					message: __(
						"No employee in {0} has a Job Category from this pay table. Set Job Category on the employee records first.",
						[`<b>${frappe.utils.escape_html(impact.company)}</b>`]
					),
					indicator: "orange",
				});
				return;
			}

			frappe.confirm(impact_html(impact), () => run_apply(frm));
		},
	});
}

function impact_html(impact) {
	const rows = impact.categories
		.map((c) => {
			// Nobody in the category - said plainly rather than left as a bare 0,
			// since it nearly always means Job Category is unset on the records.
			const who = c.count
				? `${c.count} ${c.count === 1 ? __("employee") : __("employees")}`
				: `<span class="text-muted">${__("nobody")}</span>`;
			const notes = [];
			if (c.lifted_to_scale) {
				notes.push(__("{0} below scale, brought up to the agreed rate", [c.lifted_to_scale]));
			}
			// Suspended and Inactive staff are raised too - the rate belongs to
			// the job. Said here so the payroll total holds no surprises.
			if (c.not_active) {
				notes.push(__("{0} not currently active", [c.not_active]));
			}
			const lifted = notes.length
				? `<div class="text-muted small">${notes.join(" &middot; ")}</div>`
				: "";
			return `<tr>
				<td>${frappe.utils.escape_html(c.job_category)}${lifted}</td>
				<td class="text-right">${who}</td>
				<td class="text-right">+${format_currency(c.increase_amount)}</td>
				<td class="text-right">${format_currency(c.agreed_rate)}</td>
			</tr>`;
		})
		.join("");

	return `
		<p>${__("This will raise the Basic Pay of <b>{0}</b> {1} in <b>{2}</b>.", [
			impact.total,
			impact.total === 1 ? __("employee") : __("employees"),
			frappe.utils.escape_html(impact.company),
		])}${
			impact.not_active
				? ` <span class="text-muted">${__(
						"{0} of them are suspended or inactive - the agreed rate applies to them too.",
						[impact.not_active]
				  )}</span>`
				: ""
		}</p>
		<table class="table table-bordered table-sm">
			<thead>
				<tr>
					<th>${__("Job Category")}</th>
					<th class="text-right">${__("Affected")}</th>
					<th class="text-right">${__("Increase")}</th>
					<th class="text-right">${__("Agreed Rate")}</th>
				</tr>
			</thead>
			<tbody>${rows}</tbody>
		</table>
		<p class="text-muted small">${__("This can only be done once. Continue?")}</p>
	`;
}

function run_apply(frm) {
	frappe.show_alert({ message: __("Applying CBA to employees..."), indicator: "blue" });
	frappe.call({
		method: "upande_payroll.cba_utils.apply_cba_to_employees",
		args: { cba_name: frm.doc.name },
		freeze: true,
		freeze_message: __("Updating employees..."),
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
