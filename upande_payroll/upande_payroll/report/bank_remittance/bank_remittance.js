// Copyright (c) 2026, Teresia and contributors
// For license information, please see license.txt

frappe.query_reports["Bank Remittance"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(),
			reqd: 1,
		},
		{
			fieldname: "docstatus",
			label: __("Document Status"),
			fieldtype: "Select",
			options: ["Submitted", "Draft", "Cancelled"],
			default: "Submitted",
		},
		{
			fieldname: "payroll_entry",
			label: __("Payroll Entry"),
			fieldtype: "Link",
			options: "Payroll Entry",
			get_query: () => ({
				filters: { company: frappe.query_report.get_filter_value("company") },
			}),
			// Pick a run and the period comes with it, so the report opened from a
			// Payroll Entry is scoped to that run rather than to whatever month
			// the date filters happened to default to.
			on_change: () => {
				const run = frappe.query_report.get_filter_value("payroll_entry");
				if (!run) return;
				frappe.db
					.get_value("Payroll Entry", run, ["company", "start_date", "end_date"])
					.then(({ message }) => {
						if (!message) return;
						frappe.query_report.set_filter_value({
							company: message.company,
							from_date: message.start_date,
							to_date: message.end_date,
						});
					});
			},
		},
		{
			fieldname: "bank",
			label: __("Bank"),
			fieldtype: "Link",
			options: "Bank",
			// One bank at a time is how an advice file is usually sent.
		},
		{
			fieldname: "salary_mode",
			label: __("Salary Mode"),
			fieldtype: "Select",
			options: ["", "Bank", "Cash", "Cheque", "M-Pesa"],
			// Left blank on purpose. Salary Mode is unset on most employees, and
			// defaulting to Bank would show an empty report on a site that has
			// simply never filled the field in, which reads as no one to pay.
			default: "",
		},
		{
			fieldname: "farm",
			label: __("Farm"),
			fieldtype: "Link",
			options: "Farm",
		},
		{
			fieldname: "employee",
			label: __("Employee"),
			fieldtype: "Link",
			options: "Employee",
			get_query: () => ({
				filters: { company: frappe.query_report.get_filter_value("company") },
			}),
		},
		{
			fieldname: "department",
			label: __("Department"),
			fieldtype: "Link",
			options: "Department",
			get_query: () => ({
				filters: { company: frappe.query_report.get_filter_value("company") },
			}),
		},
		{
			fieldname: "remarks",
			label: __("Remarks"),
			fieldtype: "Data",
			// Overrides the narration on every line. Blank leaves each line
			// reading "Salary <month>" off its own slip.
		},
	],

	onload(report) {
		report.page.add_inner_button(__("Download Bank File"), () => {
			const filters = report.get_values();
			if (!filters.company) {
				frappe.msgprint(__("Pick a Company first."));
				return;
			}
			frappe.call({
				method: "upande_payroll.bank_file.formats_for",
				args: { company: filters.company, bank: filters.bank },
				callback: ({ message }) => {
					const formats = message || [];
					if (!formats.length) {
						// Said plainly, because the fix is a record and not a
						// setting on this report.
						frappe.msgprint({
							title: __("No Bank File Format"),
							message: filters.bank
								? __("No Bank File Format is set up for {0} at {1}.", [
										filters.bank,
										filters.company,
								  ])
								: __(
										"No Bank File Format is set up for {0}. Set one up, or choose a Bank to narrow it down.",
										[filters.company]
								  ),
							indicator: "orange",
						});
						return;
					}
					if (formats.length === 1) {
						download(formats[0].name, filters);
						return;
					}
					// More than one, which happens when no Bank is chosen: a
					// file is per bank, so the choice cannot be guessed.
					const dialog = new frappe.ui.Dialog({
						title: __("Download Bank File"),
						fields: [
							{
								fieldname: "bank_file_format",
								label: __("Format"),
								fieldtype: "Select",
								reqd: 1,
								options: formats.map((f) => ({
									label: `${f.name} (${f.bank})`,
									value: f.name,
								})),
							},
						],
						primary_action_label: __("Download"),
						primary_action: (values) => {
							dialog.hide();
							download(values.bank_file_format, filters);
						},
					});
					dialog.show();
				},
			});
		});

		function download(bank_file_format, filters) {
			// Show it before sending it. The dialog is the file, not a summary of
			// it, so a wrong clearing code or a short line count is visible here
			// rather than after the bank rejects the upload.
			frappe.call({
				method: "upande_payroll.bank_file.preview",
				args: { bank_file_format: bank_file_format, filters: JSON.stringify(filters) },
				freeze: true,
				freeze_message: __("Building the file..."),
				callback: ({ message }) => {
					if (!message) return;
					show_preview(bank_file_format, filters, message);
				},
			});
		}

		function show_preview(bank_file_format, filters, p) {
			const cell = (v) => frappe.utils.escape_html(v === null || v === undefined ? "" : String(v));
			const body = p.data
				.map((r, i) => {
					// The first line of a table is its title and the second its
					// headings; both are part of the file, so they are shown as
					// they will be written and only marked apart.
					const heading = p.layout === "Flat Table" && i <= 1;
					const tag = heading ? "th" : "td";
					const cells = r.map((c) => `<${tag} style="padding:3px 8px;white-space:nowrap">${cell(c)}</${tag}>`).join("");
					return `<tr>${cells}</tr>`;
				})
				.join("");

			const more =
				p.included > p.shown
					? `<div class="text-muted" style="margin-top:6px">${__("Showing {0} of {1} lines. All {1} go into the file.", [p.shown, p.included])}</div>`
					: "";

			const left_out = p.left_out.length
				? `<div style="margin-top:14px">
						<b>${__("Left out of the file ({0})", [p.left_out.length])}</b>
						<div class="text-muted" style="font-size:var(--text-sm);margin-bottom:4px">${__(
							"These are on the payroll but cannot be sent to a bank."
						)}</div>
						${p.left_out
							.map(
								(r) =>
									`<div style="font-size:var(--text-sm)">${cell(r.employee_name)} &mdash; <span style="color:var(--red-600)">${cell(
										r.reason
									)}</span></div>`
							)
							.join("")}
					</div>`
				: "";

			const dialog = new frappe.ui.Dialog({
				title: __("Bank File Preview"),
				size: "extra-large",
				fields: [
					{
						fieldtype: "HTML",
						options: `
							<div style="margin-bottom:10px">
								<div><b>${cell(p.file_name)}</b></div>
								<div class="text-muted" style="font-size:var(--text-sm)">
									${__("{0} lines, {1}", [
										p.included,
										format_currency(p.total, frappe.defaults.get_default("currency")),
									])}
								</div>
							</div>
							<div style="overflow:auto;max-height:45vh;border:1px solid var(--border-color);border-radius:var(--border-radius)">
								<table style="font-family:var(--font-stack-monospace);font-size:var(--text-sm);border-collapse:collapse">${body}</table>
							</div>
							${more}
							${left_out}`,
					},
				],
				primary_action_label: __("Download"),
				primary_action: () => {
					dialog.hide();
					// A form post rather than frappe.call: the endpoint answers
					// with the workbook itself, and frappe.call would try to read
					// it as JSON.
					open_url_post("/api/method/upande_payroll.bank_file.download", {
						bank_file_format: bank_file_format,
						filters: JSON.stringify(filters),
					});
				},
			});
			dialog.show();
		}
	},

	formatter(value, row, column, data, default_formatter) {
		// Payroll number and name both open the employee. Neither can be a Link
		// column: a Link renders its own value as the docname, and these hold a
		// payroll number and a person's name, so the link would point at an
		// Employee called "1017". get_form_link keeps the text and supplies the
		// docname separately, which is carried on the row without a column.
		if (data && data.employee && ["payroll_number", "employee_name"].includes(column.fieldname)) {
			const text = default_formatter(value, row, column, data);
			if (value) {
				return frappe.utils.get_form_link("Employee", data.employee, true, text);
			}
		}

		// A gap in the routing is the whole reason to read this report before
		// sending anything, so it is marked where it is missing rather than only
		// summarised in the Cannot Pay column.
		// Marked only where the field is actually needed. A bank transfer needs
		// its codes; a mobile payment needs a number and nothing else, so an
		// empty Bank Code on an M-Pesa line is not a gap.
		if (data && !value) {
			const mode = data.salary_mode || "Bank";
			const needed =
				mode === "M-Pesa"
					? ["mpesa_number"]
					: mode === "Cash" || mode === "Cheque"
					? []
					: ["bank_code", "branch_code", "account_number"];
			if (needed.includes(column.fieldname)) {
				return `<span style="color: var(--red-500)">${__("missing")}</span>`;
			}
		}

		value = default_formatter(value, row, column, data);

		// A line that cannot be sent is shown, not hidden, so the amount is
		// marked where the eye already is rather than in a column of its own.
		if (column.fieldname === "amount" && data && data.cannot_pay) {
			value = `<span style="color: var(--red-600)">${value}</span>`;
		}

		// A withheld slip is still owed to the employee but must not be paid out
		// with the rest of the run.
		if (column.fieldname === "employee_name" && data && data.status === "Withheld") {
			value = `<span style="color: var(--orange-600); font-weight: 500">${value}</span>`;
		}
		return value;
	},
};
