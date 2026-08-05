import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, flt, getdate

from upande_payroll.upande_payroll.doctype.company_payroll_settings.company_payroll_settings import (
	get_notice_days,
)


class TerminalDuesSettlement(Document):

	def validate(self):
		self._validate_employee_status()
		self._validate_payroll_period()
		self._calc_years_worked()

		# Auto-populate only when both tables are empty AND the internal flag
		# is not set (flag prevents double-run when fetch_dues calls self.save()).
		already_fetched = getattr(self, "_dues_fetched", False)
		has_data = bool(self.earnings) or bool(self.deductions)
		if (
			not already_fetched
			and not has_data
			and self.employee
			and self.relieving_date
			and self.payroll_period_start
		):
			self._fetch_all_dues()

		self._sync_days_worked_pay()
		self._sync_notice_pay()
		self._sync_asset_recovery()
		self._sync_paye()
		self._compute_totals()

	# ------------------------------------------------------------------
	# Validation guards
	# ------------------------------------------------------------------

	def _validate_employee_status(self):
		if not self.employee:
			return
		emp = frappe.db.get_value(
			"Employee", self.employee, ["status", "relieving_date"], as_dict=True
		)
		if not emp:
			return
		if not emp.relieving_date:
			frappe.throw(_(
				"Employee {0} does not have a Relieving Date set. "
				"Update the Employee record before processing terminal dues."
			).format(self.employee))
		if emp.status != "Left":
			frappe.throw(_(
				"Employee {0} must be marked as <b>Left</b> before processing terminal dues. "
				"Current status: <b>{1}</b>"
			).format(self.employee, emp.status))

	def _calc_years_worked(self):
		if self.date_of_joining and self.relieving_date:
			days = (getdate(self.relieving_date) - getdate(self.date_of_joining)).days
			self.years_worked = round(days / 365.25, 2)

	def _validate_payroll_period(self):
		if not self.payroll_period_start or not self.relieving_date:
			return
		if getdate(self.payroll_period_start) > getdate(self.relieving_date):
			frappe.throw(_(
				"Payroll Period Start ({0}) cannot be after the Relieving Date ({1})."
			).format(self.payroll_period_start, self.relieving_date))

	# ------------------------------------------------------------------
	# Full dues fetch
	# ------------------------------------------------------------------

	def _fetch_all_dues(self):
		self._dues_fetched = True
		self._fetch_attendance_days()
		self._fetch_gratuity()
		self._fetch_leave_pay()

	@frappe.whitelist()
	def fetch_dues(self):
		"""Re-fetch all source data. Keeps manually-added rows (no source_document)."""
		if not self.employee or not self.relieving_date or not self.payroll_period_start:
			frappe.throw(_("Set Employee, Relieving Date and Payroll Period Start first."))

		self.set("earnings", [])
		self.set("deductions", [
			r for r in (self.deductions or [])
			if not r.source_document and r.deduction_type != "Pay Deduction in Lieu of Notice"
		])

		self._fetch_all_dues()
		self._sync_days_worked_pay()
		self._sync_notice_pay()
		self._sync_asset_recovery()
		self._sync_paye()
		self._compute_totals()
		self.save()
		frappe.msgprint(_("Dues fetched and recalculated. Review each row and submit when ready."))

	# ------------------------------------------------------------------
	# Attendance days
	# ------------------------------------------------------------------

	def _fetch_attendance_days(self):
		start = self.payroll_period_start
		end = self.relieving_date

		present = flt(frappe.db.count("Attendance", {
			"employee": self.employee,
			"attendance_date": ["between", [start, end]],
			"status": ("in", ["Present", "Work From Home"]),
			"docstatus": 1,
		}))

		half_day = flt(frappe.db.count("Attendance", {
			"employee": self.employee,
			"attendance_date": ["between", [start, end]],
			"status": "Half Day",
			"docstatus": 1,
		}))

		rest_days = flt(self._count_rest_days(start, end))
		self.days_worked_in_final_month = present + (half_day * 0.5) + rest_days

	def _count_rest_days(self, start, end):
		holiday_list = (
			frappe.db.get_value("Employee", self.employee, "holiday_list")
			or frappe.db.get_value("Company", self.company, "default_holiday_list")
		)
		if not holiday_list:
			return 0
		return frappe.db.count("Holiday", {
			"parent": holiday_list,
			"holiday_date": ["between", [start, end]],
			"weekly_off": 1,
		})

	# ------------------------------------------------------------------
	# Daily rate - driven by Company Payroll Settings, sourced from the
	# latest submitted Salary Slip (same pattern as gratuity_utils.py).
	# ------------------------------------------------------------------

	def _get_daily_rate(self):
		"""Return (monthly_basic, daily_rate) using Company Payroll Settings'
		Terminal Dues Basic Pay Component and Daily Rate Divisor."""
		settings = frappe.get_cached_doc("Company Payroll Settings", self.company)
		component = settings.terminal_dues_basic_pay_component
		divisor = settings.terminal_dues_divisor or 26
		if not component:
			return 0.0, 0.0

		salary_slip_name = frappe.db.get_value(
			"Salary Slip",
			{"employee": self.employee, "docstatus": 1},
			"name",
			order_by="start_date desc",
		)
		if not salary_slip_name:
			return 0.0, 0.0

		monthly = flt(frappe.db.get_value(
			"Salary Detail",
			{"parent": salary_slip_name, "parentfield": "earnings", "salary_component": component},
			"amount",
		))
		if not monthly:
			return 0.0, 0.0
		return monthly, monthly / divisor

	# ------------------------------------------------------------------
	# Earnings fetchers
	# ------------------------------------------------------------------

	def _sync_days_worked_pay(self):
		"""Remove the stale Days Worked Pay row and re-add it from the current
		Days Worked in Final Month value - runs on every save (like notice pay,
		asset recovery and PAYE), so editing that field and saving actually
		updates the amount instead of leaving the first-fetch value frozen."""
		settings = frappe.get_cached_doc("Company Payroll Settings", self.company)
		component = settings.terminal_dues_days_worked_component
		if not component:
			frappe.throw(_(
				"Set Days Worked Pay Component in Company Payroll Settings for {0}."
			).format(self.company))

		self.earnings = [
			r for r in (self.earnings or []) if r.earning_type != component
		]

		monthly, daily = self._get_daily_rate()
		if not daily:
			return

		days = flt(self.days_worked_in_final_month)
		amount = round(daily * days, 2)

		if days:
			description = f"Days Worked Pay - {days} days @ {monthly:,.2f}"
		else:
			description = (
				f"Days Worked Pay - no attendance records found. "
				f"Edit 'Days Worked in Final Month' above and save. "
				f"Daily rate: {daily:,.2f}"
			)

		self.append("earnings", {
			"earning_type": component,
			"description": description,
			"amount": amount,
			"is_taxable": 1,
			"is_gratuity": 0,
			"source_doctype": "Employee",
			"source_document": self.employee,
		})

	def _fetch_gratuity(self):
		"""Pull amount from the latest submitted Gratuity record for this employee."""
		gratuity = frappe.get_all(
			"Gratuity",
			filters={"employee": self.employee, "docstatus": 1},
			fields=["name", "amount", "salary_component"],
			order_by="modified desc",
			limit=1,
		)
		if not gratuity or not flt(gratuity[0].amount):
			return

		self.append("earnings", {
			"earning_type": gratuity[0].salary_component or "Gratuity",
			"description": f"Gratuity - {gratuity[0].name}",
			"amount": flt(gratuity[0].amount),
			"is_taxable": 0,
			"is_gratuity": 1,
			"source_doctype": "Gratuity",
			"source_document": gratuity[0].name,
		})

	def _fetch_leave_pay(self):
		"""Pull from the latest submitted Leave Encashment record for this employee."""
		encashments = frappe.get_all(
			"Leave Encashment",
			filters={"employee": self.employee, "docstatus": 1},
			fields=["name", "encashment_days", "encashment_amount", "leave_type"],
			order_by="modified desc",
			limit=1,
		)
		if not encashments:
			return
		enc = encashments[0]
		amount = flt(enc.encashment_amount)
		if not amount:
			return

		# Same source core itself uses for the Salary Slip path (create_additional_salary),
		# so the earning component is consistent regardless of which payment mode is used.
		earning_component = frappe.db.get_value("Leave Type", enc.leave_type, "earning_component")
		days = flt(enc.encashment_days)
		self.append("earnings", {
			"earning_type": earning_component or "Leave Encashment",
			"description": f"Leave Pay - {days:.1f} days ({enc.name})",
			"amount": round(amount, 2),
			"is_taxable": 1,
			"is_gratuity": 0,
			"source_doctype": "Leave Encashment",
			"source_document": enc.name,
		})

	# ------------------------------------------------------------------
	# Auto-rows: notice pay and PAYE
	# ------------------------------------------------------------------

	def _sync_notice_pay(self):
		"""Remove stale notice rows and re-add from current notice_direction/days."""
		settings = frappe.get_cached_doc("Company Payroll Settings", self.company)
		earning_component = settings.terminal_dues_notice_pay_earning_component
		deduction_component = settings.terminal_dues_notice_pay_deduction_component

		self.earnings = [
			r for r in (self.earnings or [])
			if r.earning_type != earning_component
		]
		self.deductions = [
			r for r in (self.deductions or [])
			if r.deduction_type != deduction_component
		]

		direction = (self.notice_direction or "").strip()
		if not direction:
			return

		# Required days are always derived from Company Payroll Settings' Notice
		# Period Rules based on tenure - not stored or editable on the document
		# itself. What gets paid/deducted is only the shortfall against however
		# much notice was actually served (Employment Act §38).
		required_days = flt(get_notice_days(self.company, self.years_worked))
		if not required_days:
			return

		served_days = flt(self.notice_days_served)
		days = max(0.0, required_days - served_days)
		if not days:
			return

		monthly, daily = self._get_daily_rate()
		if not daily:
			return

		amount = round(daily * days, 2)
		if direction == "Payable to Employee":
			if not earning_component:
				frappe.throw(_(
					"Set Notice Pay Earning Component in Company Payroll Settings for {0}."
				).format(self.company))
			self.append("earnings", {
				"earning_type": earning_component,
				"description": (
					f"Pay in Lieu of Notice - {days:.1f} of {required_days:.0f} days "
					f"unserved @ {monthly:,.2f}"
				),
				"amount": amount,
				"is_taxable": 1,
				"is_gratuity": 0,
				"source_doctype": "",
				"source_document": "",
			})
		else:
			if not deduction_component:
				frappe.throw(_(
					"Set Notice Pay Deduction Component in Company Payroll Settings for {0}."
				).format(self.company))
			self.append("deductions", {
				"deduction_type": deduction_component,
				"description": (
					f"Pay Deduction in Lieu of Notice - {days:.1f} of {required_days:.0f} days "
					f"unserved @ {monthly:,.2f}"
				),
				"amount": amount,
				"source_doctype": "",
				"source_document": "",
			})

	def _sync_asset_recovery(self):
		"""Remove the stale recovery row and re-add it from current Lost/Misplaced
		assets. One combined row, not one per asset - matches how notice pay and
		PAYE are each a single summarised deduction line."""
		settings = frappe.get_cached_doc("Company Payroll Settings", self.company)
		component = settings.terminal_dues_asset_recovery_component

		self.deductions = [
			r for r in (self.deductions or []) if r.deduction_type != component
		]

		lost_assets = [
			row for row in (self.assets_allocated or [])
			if row.status in ("Lost", "Misplaced") and flt(row.cost)
		]
		if not lost_assets:
			return

		total_cost = flt(sum(flt(row.cost) for row in lost_assets), 2)
		if not component:
			frappe.throw(_(
				"Set Asset Recovery Component in Company Payroll Settings for {0} - "
				"{1} has Lost/Misplaced assets with a recoverable value."
			).format(self.company, self.employee_name or self.employee))

		names = ", ".join(row.asset_description for row in lost_assets)
		self.append("deductions", {
			"deduction_type": component,
			"description": f"Asset recovery - {names}",
			"amount": total_cost,
			"source_doctype": "",
			"source_document": "",
		})

	def _sync_paye(self):
		"""PAYE on regular taxable earnings only (Days Worked + Leave + Notice).
		Gratuity is excluded - it carries its own PAYE from the Gratuity record."""
		settings = frappe.get_cached_doc("Company Payroll Settings", self.company)
		paye_component = settings.terminal_dues_paye_component

		self.deductions = [
			r for r in (self.deductions or []) if r.deduction_type != paye_component
		]

		taxable = sum(
			flt(r.amount) for r in (self.earnings or [])
			if r.is_taxable and not r.is_gratuity
		)
		if not taxable:
			return

		paye = self._calc_paye(taxable)
		if paye <= 0:
			return

		if not paye_component:
			frappe.throw(_(
				"Set PAYE Component in Company Payroll Settings for {0}."
			).format(self.company))

		self.append("deductions", {
			"deduction_type": paye_component,
			"description": f"PAYE on Gross Earnings {taxable:,.2f}",
			"amount": paye,
			"source_doctype": "Income Tax Slab",
			"source_document": "",
		})

	def _calc_paye(self, taxable_income):
		"""Progressive PAYE from the Income Tax Slab in effect for this company."""
		lookup_date = self.relieving_date or getdate()
		slab_name = frappe.db.get_value(
			"Income Tax Slab",
			{"company": self.company, "effective_from": ("<=", lookup_date), "disabled": 0},
			"name",
			order_by="effective_from desc",
		)
		if not slab_name:
			return 0.0

		slab = frappe.get_doc("Income Tax Slab", slab_name)
		monthly_relief = flt(slab.standard_tax_exemption_amount)

		gross_tax = 0.0
		for b in slab.slabs:
			b_from = flt(b.from_amount)
			b_to = flt(b.to_amount) if flt(b.to_amount) else taxable_income
			if taxable_income <= b_from:
				break
			band = min(taxable_income, b_to) - b_from
			if band > 0:
				gross_tax += band * flt(b.percent_deduction) / 100.0

		return max(0.0, round(gross_tax - monthly_relief, 2))

	# ------------------------------------------------------------------
	# Totals
	# ------------------------------------------------------------------

	def _compute_totals(self):
		self.total_earnings = round(sum(flt(r.amount) for r in (self.earnings or [])), 2)
		self.total_deductions = round(sum(flt(r.amount) for r in (self.deductions or [])), 2)
		self.net_payable = round(self.total_earnings - self.total_deductions, 2)

	# ------------------------------------------------------------------
	# Submit / Cancel
	# ------------------------------------------------------------------

	def on_submit(self):
		existing = frappe.db.get_value(
			"Terminal Dues Settlement",
			{"employee": self.employee, "docstatus": 1, "name": ("!=", self.name)},
			"name",
		)
		if existing:
			frappe.throw(_(
				"A submitted Terminal Dues Settlement {0} already exists for {1}. "
				"Cancel it before creating a new one."
			).format(existing, self.employee))

		self._compute_totals()
		if flt(self.net_payable) < 0:
			frappe.throw(_(
				"Net Payable is negative ({0}). Review deductions before submitting."
			).format(self.net_payable))
		frappe.db.set_value("Terminal Dues Settlement", self.name, "payment_status", "Submitted")
		self._mark_gratuity_paid()
		self._mark_leave_encashment_paid()
		je = self._create_journal_entry()
		if je:
			frappe.db.set_value("Terminal Dues Settlement", self.name, "journal_entry", je)

	def _mark_gratuity_paid(self):
		for row in (self.earnings or []):
			if row.is_gratuity and row.source_document:
				if frappe.db.exists("Gratuity", row.source_document):
					frappe.db.set_value("Gratuity", row.source_document, "status", "Paid")
					frappe.db.set_value(
						"Gratuity", row.source_document, "custom_terminal_dues_settlement", self.name
					)

	def _mark_leave_encashment_paid(self):
		for row in (self.earnings or []):
			if row.source_doctype == "Leave Encashment" and row.source_document:
				if frappe.db.exists("Leave Encashment", row.source_document):
					# paid_amount is set alongside status: core's own set_status() recomputes
					# status from encashment_amount vs paid_amount on every future save, so a
					# bare status="Paid" would get silently reverted to "Unpaid" otherwise.
					frappe.db.set_value("Leave Encashment", row.source_document, {
						"status": "Paid",
						"paid_amount": row.amount,
						"custom_terminal_dues_settlement": self.name,
					})

	def _resolve_accounts(self):
		"""Returns (salary_expense, payroll_payable, component_account_fn).
		salary_expense and payroll_payable use explicit fields on the doc first,
		falling back to:
		  - salary_expense  -> first earning component's account for this company
		  - payroll_payable -> Company.default_payroll_payable_account
		Every deduction component (PAYE, Asset Recovery, Notice Deduction, or
		any other) resolves its own account through component_account_fn - the
		same per-company Salary Component Account mapping core payroll uses -
		rather than each needing its own dedicated field on this doctype.
		"""
		def component_account(component_name):
			return frappe.db.get_value(
				"Salary Component Account",
				{"parent": component_name, "company": self.company},
				"account",
			)

		salary_exp = self.salary_expense_account
		if not salary_exp:
			for row in (self.earnings or []):
				acc = component_account(row.earning_type)
				if acc:
					salary_exp = acc
					break

		payroll_pay = self.payroll_payable_account
		if not payroll_pay:
			payroll_pay = frappe.db.get_value(
				"Company", self.company, "default_payroll_payable_account"
			)

		return salary_exp, payroll_pay, component_account

	def _create_journal_entry(self):
		"""Balanced JE:
		  Dr. salary_expense_account  = total_earnings (gross)
		  Cr. one line per deduction component (PAYE, Asset Recovery, Notice
		      Deduction, or any other) - each resolved from that component's
		      own per-company account
		  Cr. payroll_payable_account = net_payable (what's actually left for
		      the employee once every deduction line above is accounted for)
		"""
		settings = frappe.get_cached_doc("Company Payroll Settings", self.company)
		salary_exp, payroll_pay, component_account = self._resolve_accounts()

		deduction_totals = {}
		for row in (self.deductions or []):
			if flt(row.amount):
				deduction_totals[row.deduction_type] = (
					deduction_totals.get(row.deduction_type, 0.0) + flt(row.amount)
				)

		resolved = {}
		missing = []
		if not salary_exp:
			missing.append("Salary Expense Account")
		if not payroll_pay:
			missing.append("Payroll Payable Account")
		for component in deduction_totals:
			acc = self.paye_account if component == settings.terminal_dues_paye_component else None
			acc = acc or component_account(component)
			if not acc:
				missing.append(f"account for Salary Component '{component}'")
			else:
				resolved[component] = acc

		if missing:
			frappe.throw(
				_("Could not resolve the following accounts - set them on the salary "
				  "components or in the Accounting section: {0}").format(", ".join(missing))
			)

		accounts = [{
			"account": salary_exp,
			"debit_in_account_currency": flt(self.total_earnings),
		}]
		for component, amount in deduction_totals.items():
			accounts.append({
				"account": resolved[component],
				"credit_in_account_currency": amount,
			})
		accounts.append({
			"account": payroll_pay,
			"credit_in_account_currency": flt(self.net_payable),
			"party_type": "Employee",
			"party": self.employee,
		})

		ded_lines = ", ".join(f"{k} {v:,.2f}" for k, v in deduction_totals.items())

		je = frappe.get_doc({
			"doctype": "Journal Entry",
			"voucher_type": "Journal Entry",
			"posting_date": self.relieving_date,
			"company": self.company,
			"user_remark": (
				f"Terminal Dues - {self.employee_name} ({self.employee}) | {self.name} | "
				f"Gross {flt(self.total_earnings):,.2f} | Deductions: {ded_lines}"
			),
			"accounts": [
				a for a in accounts
				if flt(a.get("debit_in_account_currency", 0))
				+ flt(a.get("credit_in_account_currency", 0)) > 0
			],
		})
		je.insert(ignore_permissions=True)
		je.submit()
		return je.name

	def on_cancel(self):
		frappe.db.set_value("Terminal Dues Settlement", self.name, "payment_status", "Pending")
		self._revert_gratuity()
		self._revert_leave_encashment()
		self._cancel_journal_entry()

	def _revert_gratuity(self):
		for row in (self.earnings or []):
			if row.is_gratuity and row.source_document:
				if frappe.db.exists("Gratuity", row.source_document):
					frappe.db.set_value("Gratuity", row.source_document, "status", "Unpaid")
					frappe.db.set_value("Gratuity", row.source_document, "custom_terminal_dues_settlement", None)

	def _revert_leave_encashment(self):
		for row in (self.earnings or []):
			if row.source_doctype == "Leave Encashment" and row.source_document:
				if frappe.db.exists("Leave Encashment", row.source_document):
					frappe.db.set_value("Leave Encashment", row.source_document, {
						"status": "Unpaid",
						"paid_amount": 0,
						"custom_terminal_dues_settlement": None,
					})

	def _cancel_journal_entry(self):
		je_name = frappe.db.get_value("Terminal Dues Settlement", self.name, "journal_entry")
		if not je_name:
			return
		je = frappe.get_doc("Journal Entry", je_name)
		if je.docstatus == 1:
			je.cancel()
		frappe.db.set_value("Terminal Dues Settlement", self.name, "journal_entry", None)

	# ------------------------------------------------------------------
	# Whitelisted actions (called from form buttons)
	# ------------------------------------------------------------------

	@frappe.whitelist()
	def create_journal_entry(self):
		if self.docstatus != 1:
			frappe.throw(_("Only submitted documents can generate a Journal Entry."))
		existing = frappe.db.get_value("Terminal Dues Settlement", self.name, "journal_entry")
		if existing:
			frappe.throw(_("Journal Entry {0} already exists for this settlement.").format(existing))
		je = self._create_journal_entry()
		if je:
			frappe.db.set_value("Terminal Dues Settlement", self.name, "journal_entry", je)
		return je


# ------------------------------------------------------------------
# Module-level helpers (callable from JS on unsaved docs)
# ------------------------------------------------------------------

@frappe.whitelist()
def get_suggested_payroll_start(employee):
	"""Return the day after the last submitted salary slip end_date for this employee."""
	last_end = frappe.db.get_value(
		"Salary Slip",
		{"employee": employee, "docstatus": 1},
		"end_date",
		order_by="end_date desc",
	)
	if last_end:
		return str(add_days(last_end, 1))
	return None
