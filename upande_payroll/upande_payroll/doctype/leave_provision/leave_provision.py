# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, today

GROUP_FIELDS = {
	"Department": "department",
	"Branch": "branch",
	"Cost Center": "payroll_cost_center",
}


class LeaveProvision(Document):
	"""What the company owes in leave people have earned but not taken.

	Leave earned is a cost of the month it was earned in, not of the month it is
	eventually taken or paid out in. Without a provision the whole bill lands on
	whichever month someone finally goes on leave, and the accounts show a
	liability the company has been carrying all along as if it appeared overnight.

	Kept as a document rather than a dialog that fires off a Journal Entry: the
	working is worth keeping. Every employee's balance, daily rate and share of
	the total stays attached to the provision, so a figure in the accounts can be
	traced back to the people it came from, and cancelling reverses the journal
	rather than leaving it stranded.
	"""

	def validate(self):
		self.set_defaults()
		self.validate_period()
		self.validate_accounts()
		self.validate_no_overlap()
		self.build_lines()
		self.measure_movement()

	def on_submit(self):
		if not self.employees:
			frappe.throw(_("Nothing to provide for - no employee has a leave balance."))
		if not flt(self.movement):
			# Nothing has changed since the last provision, so there is nothing
			# to post. Writing a zero journal would only add noise to the ledger.
			frappe.msgprint(
				_("The liability is unchanged at {0}, so no journal was needed.")
				.format(frappe.format_value(flt(self.total_liability),
											{"fieldtype": "Currency"})),
				indicator="blue",
			)
		else:
			self.create_journal_entry()
		self.db_set("status", "Submitted")

	def on_cancel(self):
		self.cancel_journal_entry()
		self.db_set("status", "Cancelled")

	# ------------------------------------------------------------------

	def set_defaults(self):
		"""Fill everything in from Company Payroll Settings.

		A provision is run every period by whoever does payroll, and none of the
		basis changes between runs. Asking for the component, the divisor, the
		leave types and both accounts each time is how the wrong account ends up
		on one month's journal and nobody notices until the year end.

		The component and divisor are the provision's own rather than borrowed
		from terminal dues or encashment. They usually hold the same values, but
		they answer different questions, and a company that changes how it pays
		out terminal dues should not silently change what it provides for.

		Keeping the divisor equal to the encashment one is still the right
		default: leave should be provided for at the rate it would actually be
		paid out at. Providing at basic/30 while encashing at basic/26
		under-states the liability by 13%.
		"""
		if not self.posting_date:
			self.posting_date = today()

		if not self.company or not frappe.db.exists("Company Payroll Settings", self.company):
			return
		settings = frappe.get_cached_doc("Company Payroll Settings", self.company)

		if not self.basic_pay_component:
			self.basic_pay_component = settings.leave_provision_basic_pay_component
		if not self.divisor:
			self.divisor = settings.leave_provision_divisor
		if not self.expense_account:
			self.expense_account = settings.leave_provision_expense_account
		if not self.liability_account:
			self.liability_account = settings.leave_provision_liability_account

		if not self.leave_types:
			for row in settings.leave_provision_leave_types or []:
				self.append("leave_types", {"leave_type": row.leave_type})

	def validate_accounts(self):
		"""An expense account for the expense, a liability for the liability.

		Nothing in Frappe stops Accounts Receivable being picked as the leave
		expense - it is just another Account - and the journal balances either
		way, so a wrong choice shows up as a strange balance sheet months later
		rather than as an error now.
		"""
		for field, wanted in (("expense_account", "Expense"),
							  ("liability_account", "Liability")):
			account = self.get(field)
			if not account:
				continue
			root_type, is_group = frappe.db.get_value(
				"Account", account, ["root_type", "is_group"])
			if is_group:
				frappe.throw(_("{0} is a group account, so nothing can be posted to it.")
							 .format(account))
			if root_type != wanted:
				frappe.throw(
					_("{0} is {1}, not {2}. Pick {2} account for {3}.")
					.format(account, root_type, wanted,
							self.meta.get_label(field))
				)

	def validate_period(self):
		if getdate(self.from_date) > getdate(self.to_date):
			frappe.throw(_("From Date cannot be after To Date."))
		if flt(self.divisor) <= 0:
			frappe.throw(_("Set a Daily Rate Divisor. It is what basic pay is "
						   "divided by to get a day's pay."))
		if not self.basic_pay_component:
			frappe.throw(_("Set which Salary Component is basic pay."))

	def validate_no_overlap(self):
		"""One provision per period, checked against real dates.

		Not against the journal's remark text: a remark can be edited or
		translated, and then the same month is provided for twice without
		anything objecting.
		"""
		clash = frappe.db.sql(
			"""
			SELECT name FROM `tabLeave Provision`
			WHERE company = %(company)s AND docstatus = 1 AND name != %(name)s
				AND from_date <= %(to_date)s AND to_date >= %(from_date)s
			LIMIT 1
			""",
			{"company": self.company, "name": self.name or "",
			 "from_date": self.from_date, "to_date": self.to_date},
		)
		if clash:
			frappe.throw(
				_("{0} already provides for a period overlapping {1} to {2}.")
				.format(clash[0][0], self.from_date, self.to_date)
			)

	# ------------------------------------------------------------------

	def build_lines(self):
		from hrms.hr.doctype.leave_application.leave_application import get_leave_balance_on

		leave_types = [row.leave_type for row in (self.leave_types or []) if row.leave_type]
		if not leave_types:
			frappe.throw(_("Choose at least one leave type to provide for."))

		group_field = GROUP_FIELDS.get(self.group_by)
		fields = ["name", "employee_name"] + ([group_field] if group_field else [])
		employees = frappe.get_all(
			"Employee",
			filters={"company": self.company, "status": "Active"},
			fields=fields,
			order_by="name",
		)

		self.set("employees", [])
		total = 0.0
		as_at = getdate(self.to_date)

		for employee in employees:
			basic = self.basic_pay_for(employee.name)
			if basic <= 0:
				continue
			daily_rate = flt(basic / flt(self.divisor), 2)

			for leave_type in leave_types:
				balance = flt(get_leave_balance_on(employee.name, leave_type, as_at))
				if balance <= 0:
					# Someone who has taken more than they earned is not an asset;
					# a negative balance must not net off other people's liability.
					continue

				liability = flt(daily_rate * balance, 2)
				total += liability
				self.append("employees", {
					"employee": employee.name,
					"employee_name": employee.employee_name,
					"grouping": (employee.get(group_field) if group_field else None)
								or _("Unassigned"),
					"leave_type": leave_type,
					"basic_pay": basic,
					"daily_rate": daily_rate,
					"leave_balance": balance,
					"liability": liability,
				})

		self.total_liability = flt(total, 2)

	def basic_pay_for(self, employee):
		"""Basic pay from the last payslip on or before the provision date.

		Looking only inside the period would mean a provision run before that
		month's payroll finds nothing and values the employee at zero. Drafts
		count, since a provision is often prepared alongside a payroll that has
		not been submitted yet.
		"""
		row = frappe.db.sql(
			"""
			SELECT sd.amount
			FROM `tabSalary Slip` ss
			INNER JOIN `tabSalary Detail` sd
				ON sd.parent = ss.name AND sd.parentfield = 'earnings'
				AND sd.salary_component = %(component)s
			WHERE ss.docstatus < 2 AND ss.employee = %(employee)s
				AND ss.company = %(company)s AND ss.start_date <= %(to_date)s
			ORDER BY ss.start_date DESC, ss.docstatus ASC
			LIMIT 1
			""",
			{"component": self.basic_pay_component, "employee": employee,
			 "company": self.company, "to_date": self.to_date},
		)
		if row:
			return flt(row[0][0])

		# No payslip yet - a new joiner, or a company still setting up. The
		# assignment's base is the next best statement of what they are on.
		base = frappe.db.get_value(
			"Salary Structure Assignment",
			{"employee": employee, "docstatus": 1,
			 "from_date": ("<=", self.to_date)},
			"base",
			order_by="from_date desc",
		)
		return flt(base)

	# ------------------------------------------------------------------

	def measure_movement(self):
		"""What has changed since the last provision.

		The total liability is what the company would have to find if nobody
		took their leave - a standing figure, not something that accumulates.
		Post it again in full each period and the provision account grows to a
		multiple of the real liability. So the journal carries the movement, and
		the account is left holding the current total.
		"""
		previous = frappe.db.sql(
			"""
			SELECT name, total_liability
			FROM `tabLeave Provision`
			WHERE company = %(company)s AND docstatus = 1 AND name != %(name)s
				AND to_date < %(to_date)s
			ORDER BY to_date DESC, creation DESC
			LIMIT 1
			""",
			{"company": self.company, "name": self.name or "", "to_date": self.to_date},
			as_dict=True,
		)

		self.previous_provision = previous[0].name if previous else None
		self.previous_liability = flt(previous[0].total_liability) if previous else 0.0
		self.movement = flt(flt(self.total_liability) - flt(self.previous_liability), 2)

	def group_totals(self):
		grouped = {}
		for row in self.employees:
			key = row.grouping or _("Unassigned")
			grouped.setdefault(key, {"amount": 0.0, "people": 0})
			grouped[key]["amount"] += flt(row.liability)
			grouped[key]["people"] += 1
		return grouped

	def previous_group_totals(self):
		if not self.previous_provision:
			return {}
		rows = frappe.get_all(
			"Leave Provision Detail",
			filters={"parent": self.previous_provision, "parenttype": "Leave Provision"},
			fields=["grouping", "liability"],
		)
		totals = {}
		for row in rows:
			key = row.grouping or _("Unassigned")
			totals[key] = totals.get(key, 0.0) + flt(row.liability)
		return totals

	def create_journal_entry(self):
		"""Post the change per group, and the net against the provision account.

		A group whose liability fell gets a credit rather than being left out:
		leave that was taken has to come back off the provision, or the account
		keeps carrying people who have already been paid.
		"""
		current = self.group_totals()
		previous = self.previous_group_totals()

		accounts = []
		for group in sorted(set(current) | set(previous)):
			now = flt(current.get(group, {}).get("amount", 0.0), 2)
			before = flt(previous.get(group, 0.0), 2)
			change = flt(now - before, 2)
			if not change:
				continue

			people = current.get(group, {}).get("people", 0)
			accounts.append({
				"account": self.expense_account,
				"debit_in_account_currency": change if change > 0 else 0,
				"credit_in_account_currency": -change if change < 0 else 0,
				"user_remark": (
					_("{0} - leave earned but not taken ({1} employees)")
					.format(group, people) if change > 0
					else _("{0} - leave taken, provision released").format(group)
				),
			})

		net = flt(self.movement, 2)
		accounts.append({
			"account": self.liability_account,
			"debit_in_account_currency": -net if net < 0 else 0,
			"credit_in_account_currency": net if net > 0 else 0,
			"user_remark": _("Leave provision {0} to {1}")
						   .format(self.from_date, self.to_date),
		})

		journal = frappe.get_doc({
			"doctype": "Journal Entry",
			"voucher_type": "Journal Entry",
			"company": self.company,
			"posting_date": self.posting_date,
			"user_remark": _("Leave provision {0}, {1} to {2}")
						   .format(self.name, self.from_date, self.to_date),
			"accounts": accounts,
		})
		journal.flags.ignore_permissions = True
		journal.insert()
		journal.submit()
		self.db_set("journal_entry", journal.name)

	def cancel_journal_entry(self):
		if not self.journal_entry:
			return
		if not frappe.db.exists("Journal Entry", self.journal_entry):
			return
		journal = frappe.get_doc("Journal Entry", self.journal_entry)
		if journal.docstatus == 1:
			journal.flags.ignore_permissions = True
			journal.cancel()
