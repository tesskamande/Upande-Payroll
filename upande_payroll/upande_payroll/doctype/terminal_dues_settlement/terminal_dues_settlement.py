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
		self._sync_salary_advance_recovery()
		# Statutory before PAYE: SHIF, the Housing Levy and NSSF all come off
		# taxable pay, so the tax cannot be worked out until they are known.
		self._sync_statutory()
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
		self._sync_salary_advance_recovery()
		# Statutory before PAYE: SHIF, the Housing Levy and NSSF all come off
		# taxable pay, so the tax cannot be worked out until they are known.
		self._sync_statutory()
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
		divisor = flt(settings.terminal_dues_divisor)
		if not component:
			return 0.0, 0.0

		# No quiet fallback. Guessing 26 here would hand back a daily rate that
		# looks right and is not, and every figure derived from it - days
		# worked, pay in lieu of notice - would be wrong without anything
		# showing it. Better to stop and say the setting is missing.
		if divisor <= 0:
			frappe.throw(
				_("Set a Daily Rate Divisor in Company Payroll Settings for {0} "
				  "before working out terminal dues. It is what monthly basic pay "
				  "is divided by to get a day's pay.").format(self.company)
			)

		salary_slip_name = frappe.db.get_value(
			"Salary Slip",
			{"employee": self.employee, "docstatus": 1},
			"name",
			order_by="start_date desc",
		)

		monthly = 0.0
		if salary_slip_name:
			monthly = flt(frappe.db.get_value(
				"Salary Detail",
				{"parent": salary_slip_name, "parentfield": "earnings",
				 "salary_component": component},
				"amount",
			))

		# Nothing on a payslip, so fall back to what the employee is on. Somebody
		# who leaves before their first payroll runs, or who was never put
		# through one, still has a rate on their record - and returning zero
		# meant their days worked and notice pay silently came to nothing rather
		# than saying anything was wrong.
		if not monthly:
			monthly = flt(frappe.db.get_value("Employee", self.employee, "basic_pay"))

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

	def _sync_salary_advance_recovery(self):
		"""Recover whatever the employee still owes on a salary advance.

		The last payslip already takes everything it can, but the two thirds rule
		caps it, and an employee can leave owing more than one month's pay could
		cover. This is the only document left that can collect the rest, so it
		claims the full outstanding balance of every advance still running.

		One row per advance rather than one combined line - unlike asset recovery,
		each of these has a single source document worth pointing at, and somebody
		querying why a leaver was deducted should land on the advance itself.

		The component comes from the advance's own type, which already names the
		one its instalments are deducted as. Nothing new to configure, and the
		settlement deducts it under the same name the payslips did.
		"""
		self.deductions = [
			row for row in (self.deductions or [])
			if row.source_doctype != "Employee Salary Advance"
		]

		advances = frappe.get_all(
			"Employee Salary Advance",
			filters={
				"employee": self.employee,
				"company": self.company,
				"docstatus": 1,
				"status": ("in", ("Unpaid", "Partially Repaid")),
			},
			fields=["name", "salary_component", "outstanding_amount"],
			order_by="posting_date asc, creation asc",
		)

		for advance in advances:
			outstanding = flt(advance.outstanding_amount, 2)
			if outstanding <= 0.01:
				continue

			if not advance.salary_component:
				frappe.throw(_(
					"Advance {0} has no salary component, so {1} cannot be deducted "
					"from the settlement. Set one on its advance type."
				).format(advance.name, frappe.bold(outstanding)))

			self.append("deductions", {
				"deduction_type": advance.salary_component,
				"description": f"Salary advance recovery - {advance.name}",
				"amount": outstanding,
				"source_doctype": "Employee Salary Advance",
				"source_document": advance.name,
			})

	def _statutory_base(self):
		"""Taxable terminal earnings, gratuity aside.

		Gratuity carries its own PAYE from the Gratuity record, spread over the
		years it was earned in, so taxing it again here would double up. It is
		also a terminal benefit rather than pensionable pay, which is why the
		levies leave it alone too.
		"""
		return flt(sum(
			flt(row.amount) for row in (self.earnings or [])
			if row.is_taxable and not row.is_gratuity
		), 2)

	def _sync_statutory(self):
		"""NSSF, SHIF and the Housing Levy, where the company deducts them.

		Whether final dues attract the levies is not settled the same way
		everywhere - notice pay in lieu in particular is treated as wages by some
		employers and as compensation by others - so it is a per-company choice
		rather than something decided here. When it is on, the figures come from
		the same functions payroll uses, so the two can never drift apart.
		"""
		from upande_payroll.kenya_statutory_calculator import (
			compute_housing_levy,
			compute_nssf,
			compute_shif,
			get_statutory_components,
		)

		components = get_statutory_components()
		managed = {
			components.nssf_tier1_employee, components.nssf_tier2_employee,
			components.shif, components.housing_levy_employee,
		}
		self.deductions = [
			row for row in (self.deductions or []) if row.deduction_type not in managed
		]

		settings = frappe.get_cached_doc("Company Payroll Settings", self.company)
		if settings.terminal_dues_statutory_deductions != "All Statutory":
			return

		kenya = frappe.get_cached_doc("Kenya Payroll Settings")
		if not kenya.enabled:
			return

		base = self._statutory_base()
		if base <= 0:
			return

		nssf = compute_nssf(base, kenya)
		amounts = [
			(components.nssf_tier1_employee, nssf.tier1_employee),
			(components.nssf_tier2_employee, nssf.tier2_employee),
			(components.shif, compute_shif(base, kenya, "Monthly")),
			(components.housing_levy_employee, compute_housing_levy(base, kenya).employee),
		]

		for component, amount in amounts:
			if not component or flt(amount) <= 0:
				continue
			self.append("deductions", {
				"deduction_type": component,
				"description": _("On terminal earnings of {0}").format(f"{base:,.2f}"),
				"amount": flt(amount, 2),
				"source_doctype": "Kenya Payroll Settings",
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

		taxable = self._statutory_base()
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
		"""PAYE on the terminal earnings, from Kenya Payroll Settings.

		It used to read the company's Income Tax Slab. On a site set up for
		gratuity that slab is the annual one - bands running to 288,000 and a
		relief of 28,800 - and applying it to a month's dues gave nothing:
		zero tax on settlements up to 200,000, because a whole year's relief was
		taken off one month's tax. The bands live in Kenya Payroll Settings now,
		the same ones payroll uses, so a settlement and a payslip in the same
		month cannot tax the same money differently.
		"""
		from upande_payroll.kenya_statutory_calculator import (
			compute_gross_paye,
			get_statutory_components,
		)

		kenya = frappe.get_cached_doc("Kenya Payroll Settings")
		if not kenya.enabled:
			return 0.0

		# SHIF and the Housing Levy come off taxable pay in full; NSSF is
		# relieved with pension against the combined monthly cap. Only what was
		# actually deducted may relieve, so a company on PAYE Only relieves
		# nothing - it did not take the levies.
		components = get_statutory_components()
		deducted = {}
		for row in (self.deductions or []):
			deducted[row.deduction_type] = deducted.get(row.deduction_type, 0.0) + flt(row.amount)

		levies = (
			flt(deducted.get(components.shif))
			+ flt(deducted.get(components.housing_levy_employee))
		)
		nssf = (
			flt(deducted.get(components.nssf_tier1_employee))
			+ flt(deducted.get(components.nssf_tier2_employee))
		)
		cap = flt(kenya.retirement_relief_cap)
		retirement = min(nssf, cap) if cap else nssf

		chargeable = max(flt(taxable_income) - levies - retirement, 0.0)
		gross_tax = compute_gross_paye(chargeable, kenya)
		return max(0.0, round(gross_tax - flt(kenya.monthly_personal_relief), 2))

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
		self._recover_salary_advances()
		je = self._create_journal_entry()
		if je:
			frappe.db.set_value("Terminal Dues Settlement", self.name, "journal_entry", je)

	def _recover_salary_advances(self):
		"""Credit the settlement's advance deductions against the advances.

		Without this the money is taken and the advance still says it is owed, so
		the employee is deducted and the ledger disagrees. No date bound is passed:
		a settlement clears the whole balance, including periods the calendar has
		not reached.
		"""
		for row in (self.deductions or []):
			if row.source_doctype != "Employee Salary Advance" or not row.source_document:
				continue
			if not frappe.db.exists("Employee Salary Advance", row.source_document):
				continue

			frappe.get_doc(
				"Employee Salary Advance", row.source_document
			).apply_recovery(
				self.doctype, self.name,
				self.relieving_date or frappe.utils.nowdate(),
				flt(row.amount),
			)

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

		# Nothing to post, so post nothing. A settlement can legitimately come to
		# zero - somebody who left with no days worked, no notice and no accrued
		# anything - and there is no accounting entry to make for it.
		#
		# It also has to be caught here rather than left to the Journal Entry:
		# ERPNext's get_stock_accounts() reads an empty list of rows as "check
		# every stock account in the company", so a JE with all its lines
		# filtered out came back complaining about Stock In Hand, which had
		# nothing to do with payroll.
		if not flt(self.total_earnings) and not flt(self.net_payable) and not any(
			flt(row.amount) for row in (self.deductions or [])
		):
			return None

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
		self._revert_salary_advances()
		self._cancel_journal_entry()

	def _revert_salary_advances(self):
		"""Put back what this settlement collected against the advances."""
		for row in (self.deductions or []):
			if row.source_doctype != "Employee Salary Advance" or not row.source_document:
				continue
			if not frappe.db.exists("Employee Salary Advance", row.source_document):
				continue

			frappe.get_doc(
				"Employee Salary Advance", row.source_document
			).reverse_recovery(self.doctype, self.name)

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
