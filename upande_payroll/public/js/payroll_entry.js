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
});

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
