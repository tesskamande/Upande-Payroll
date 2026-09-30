frappe.provide("upande_payroll.blocks.payroll_command_center");

upande_payroll.blocks.payroll_command_center.mount = function (root_element) {
	const company_select = root_element.querySelector("[data-ppc-company]");
	const employee_select = root_element.querySelector("[data-ppc-employee]");
	const chart_el = root_element.querySelector("[data-ppc-chart]");
	const breakdown_wrap = root_element.querySelector("[data-ppc-breakdown]");
	const breakdown_title = root_element.querySelector("[data-ppc-breakdown-title]");
	const breakdown_body = root_element.querySelector("[data-ppc-breakdown-body]");
	const breakdown_close = root_element.querySelector("[data-ppc-breakdown-close]");
	if (!company_select || !chart_el) return;

	let state = { company: "", employee: "" };

	function money(v) {
		return format_currency(flt(v), null, 0);
	}

	function set_options(select, rows, value_field, label_field, selected) {
		select.innerHTML = select === employee_select ? '<option value="">All Employees</option>' : "";
		rows.forEach((r) => {
			const opt = document.createElement("option");
			opt.value = r[value_field];
			opt.textContent = r[label_field];
			if (r[value_field] === selected) opt.selected = true;
			select.appendChild(opt);
		});
	}

	function load_employees(company) {
		frappe.call({
			method: "upande_payroll.upande_payroll.payroll_dashboard.employees_for_company",
			args: { company },
			callback(r) {
				set_options(employee_select, r.message || [], "name", "employee_name", "");
			},
		});
	}

	function render_chart(rows) {
		chart_el.innerHTML = "";
		if (!rows.length) {
			chart_el.innerHTML = '<div class="ppc-empty">No salary slips in this window.</div>';
			return;
		}
		const labels = rows.map((r) => frappe.datetime.str_to_user(r.period).replace(/ \d{1,2},/, ","));
		new frappe.Chart(chart_el, {
			data: {
				labels,
				datasets: [
					{ name: "Gross Pay", values: rows.map((r) => r.gross) },
					{ name: "Net Pay", values: rows.map((r) => r.net) },
				],
			},
			type: "line",
			height: 260,
			colors: ["#5e64ff", "#28a745"],
			axisOptions: { xAxisMode: "tick" },
		});
		chart_el.dataset.periods = JSON.stringify(rows.map((r) => r.period));
		chart_el.onclick = function (e) {
			const target = e.target.closest("[data-point-index]");
			if (!target) return;
			const idx = parseInt(target.dataset.pointIndex, 10);
			const periods = JSON.parse(chart_el.dataset.periods || "[]");
			if (periods[idx]) show_breakdown(periods[idx]);
		};
	}

	function show_breakdown(period) {
		frappe.call({
			method: "upande_payroll.upande_payroll.payroll_dashboard.drill",
			args: { period, company: state.company, employee: state.employee },
			freeze: true,
			callback(r) {
				const d = r.message;
				if (!d) return;
				breakdown_title.textContent =
					frappe.datetime.str_to_user(d.period).replace(/ \d{1,2},/, ",") +
					" — " + money(d.total_gross) + " gross, " + money(d.total_net) + " net";
				breakdown_body.innerHTML = (d.rows || [])
					.map(
						(row) => `<tr>
							<td><a href="/app/salary-slip/${encodeURIComponent(row.name)}">${frappe.utils.escape_html(row.employee_name || row.employee)}</a></td>
							<td>${frappe.utils.escape_html(row.department || "")}</td>
							<td class="ppc-num">${money(row.gross_pay)}</td>
							<td class="ppc-num">${money(row.net_pay)}</td>
						</tr>`
					)
					.join("");
				breakdown_wrap.hidden = false;
			},
		});
	}

	function load_trend() {
		frappe.call({
			method: "upande_payroll.upande_payroll.payroll_dashboard.trend",
			args: { company: state.company, employee: state.employee },
			callback(r) {
				render_chart((r.message && r.message.rows) || []);
			},
		});
	}

	company_select.addEventListener("change", () => {
		state.company = company_select.value;
		state.employee = "";
		breakdown_wrap.hidden = true;
		load_employees(state.company);
		load_trend();
	});

	employee_select.addEventListener("change", () => {
		state.employee = employee_select.value;
		breakdown_wrap.hidden = true;
		load_trend();
	});

	if (breakdown_close) {
		breakdown_close.addEventListener("click", () => (breakdown_wrap.hidden = true));
	}

	frappe.call({
		method: "upande_payroll.upande_payroll.payroll_dashboard.meta",
		callback(r) {
			const m = r.message;
			if (!m) return;
			set_options(company_select, m.companies, "name", "name", m.default_company);
			state.company = m.default_company;
			set_options(employee_select, m.employees || [], "name", "employee_name", "");
			load_trend();
		},
	});
};
