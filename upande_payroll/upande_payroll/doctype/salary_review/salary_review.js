// Copyright (c) 2026, Teresia and contributors
// For license information, please see license.txt

// Increment Amount, Increment %, and Proposed Basic Pay stay in step live, as
// soon as one changes - not just on save. Waiting for a save to reconcile them
// leaves the other two sitting there stale (and wrong) while HR is still
// typing, which is exactly what looked like a bug: type 10% and Proposed
// Basic Pay keeps showing whatever was left over from an earlier edit.
//
// The guard flag stops this from looping: setting the two derived fields
// below fires their own handlers too, and without it each would try to
// recompute the other pair right back.
frappe.ui.form.on("Salary Review", {
	proposed_basic_pay(frm) {
		recompute(frm, "proposed_basic_pay");
	},
	increment_amount(frm) {
		recompute(frm, "increment_amount");
	},
	increment_percent(frm) {
		recompute(frm, "increment_percent");
	},
});

async function recompute(frm, source) {
	if (frm.__salary_review_computing) {
		return;
	}
	const current = flt(frm.doc.current_basic_pay);
	if (!current) {
		return;
	}

	frm.__salary_review_computing = true;
	try {
		if (source === "proposed_basic_pay") {
			const increment = flt(flt(frm.doc.proposed_basic_pay) - current, 2);
			await frm.set_value("increment_amount", increment);
			await frm.set_value("increment_percent", flt((increment / current) * 100, 2));
		} else if (source === "increment_amount") {
			const increment = flt(frm.doc.increment_amount);
			await frm.set_value("proposed_basic_pay", flt(current + increment, 2));
			await frm.set_value("increment_percent", flt((increment / current) * 100, 2));
		} else if (source === "increment_percent") {
			const increment = flt((current * flt(frm.doc.increment_percent)) / 100, 2);
			await frm.set_value("increment_amount", increment);
			await frm.set_value("proposed_basic_pay", flt(current + increment, 2));
		}
	} finally {
		frm.__salary_review_computing = false;
	}
}
