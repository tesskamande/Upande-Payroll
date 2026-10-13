frappe.ui.form.on("Bulk Salary Review", {
	refresh(frm) {
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Get Employees"), () => get_employees(frm));
		}
	},
	apply_percent(frm) {
		if (!frm.doc.apply_percent) return;
		(frm.doc.employees || []).forEach((row) => {
			frappe.model.set_value(row.doctype, row.name, "increment_percent", frm.doc.apply_percent);
		});
	},
	employees_add(frm, cdt, cdn) {
		// A row added after Increment % for All was set - whether typed in
		// directly or added by Get Employees - still needs that same % on
		// it, since it had no chance to be caught by the loop above.
		if (!frm.doc.apply_percent) return;
		frappe.model.set_value(cdt, cdn, "increment_percent", frm.doc.apply_percent);
	},
});

frappe.ui.form.on("Bulk Salary Review Detail", {
	employee(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.employee) return;
		frappe.db.get_value("Employee", row.employee, "basic_pay").then(({ message }) => {
			frappe.model.set_value(cdt, cdn, "current_basic_pay", message ? message.basic_pay : 0).then(() => {
				// current_basic_pay just arrived - if a uniform % reached this
				// row before its basic pay was known, the amount and proposed
				// pay it implies could not be worked out until now.
				if (flt(row.increment_percent)) {
					recompute_row(frm, cdt, cdn, "increment_percent");
				}
			});
		});
	},
	proposed_basic_pay(frm, cdt, cdn) {
		recompute_row(frm, cdt, cdn, "proposed_basic_pay");
	},
	increment_amount(frm, cdt, cdn) {
		recompute_row(frm, cdt, cdn, "increment_amount");
	},
	increment_percent(frm, cdt, cdn) {
		recompute_row(frm, cdt, cdn, "increment_percent");
	},
});

function recompute_row(frm, cdt, cdn, source) {
	const row = locals[cdt][cdn];
	if (row.__computing) return;
	const current = flt(row.current_basic_pay);
	if (!current) return;

	row.__computing = true;
	try {
		if (source === "proposed_basic_pay") {
			const increment = flt(flt(row.proposed_basic_pay) - current, 2);
			frappe.model.set_value(cdt, cdn, "increment_amount", increment);
			frappe.model.set_value(cdt, cdn, "increment_percent", flt((increment / current) * 100, 2));
		} else if (source === "increment_amount") {
			const increment = flt(row.increment_amount);
			frappe.model.set_value(cdt, cdn, "proposed_basic_pay", flt(current + increment, 2));
			frappe.model.set_value(cdt, cdn, "increment_percent", flt((increment / current) * 100, 2));
		} else if (source === "increment_percent") {
			const increment = flt((current * flt(row.increment_percent)) / 100, 2);
			frappe.model.set_value(cdt, cdn, "increment_amount", increment);
			frappe.model.set_value(cdt, cdn, "proposed_basic_pay", flt(current + increment, 2));
		}
	} finally {
		row.__computing = false;
	}
}

function get_employees(frm) {
	if (!frm.doc.company) {
		frappe.msgprint(__("Set Company first."));
		return;
	}
	frm.call("get_employees").then((r) => {
		const employees = r.message || [];
		if (!employees.length) {
			frappe.msgprint(__("No matching employees found."));
			return;
		}
		employees.forEach((e) => {
			// add_child() builds the row directly, bypassing the grid's own
			// "add a row" action - employees_add above never sees it, so a
			// uniform % already set has to be applied here instead.
			const row = frm.add_child("employees");
			row.employee = e.employee;
			row.employee_name = e.employee_name;
			row.department = e.department;
			row.current_basic_pay = e.current_basic_pay;
			if (flt(frm.doc.apply_percent) && flt(row.current_basic_pay)) {
				const increment = flt((flt(row.current_basic_pay) * flt(frm.doc.apply_percent)) / 100, 2);
				row.increment_percent = frm.doc.apply_percent;
				row.increment_amount = increment;
				row.proposed_basic_pay = flt(flt(row.current_basic_pay) + increment, 2);
			}
		});
		frm.refresh_field("employees");
	});
}
