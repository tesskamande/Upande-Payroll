# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, flt, getdate, today

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

	def on_submit(self):
		if not self.lines():
			frappe.throw(_("Nothing to provide for - no employee has a leave balance."))
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

		if not self.company:
			return

		if not frappe.db.exists("Company Payroll Settings", self.company):
			frappe.throw(_(
				"{0} has no Company Payroll Settings, so there is nothing to "
				"provide leave against. Set them up first."
			).format(self.company))

		settings = frappe.get_cached_doc("Company Payroll Settings", self.company)

		# Said out loud rather than returned quietly. The other features in this
		# app are gated inside hooks on somebody else's document, where silence is
		# the right answer - a payslip with no overtime looks like a payslip. This
		# document exists for nothing else, so somebody who created one and
		# expected numbers needs telling why there are none.
		if not settings.enable_leave_provision:
			frappe.throw(_(
				"Leave Provision is switched off for {0}. Tick Enable Leave "
				"Provision under Leave Provision in Company Payroll Settings, "
				"fill in the settings below it, and try again."
			).format(self.company))

		self.basic_pay_component = settings.leave_provision_basic_pay_component
		self.divisor = flt(settings.leave_provision_divisor)
		if self.divisor <= 0:
			frappe.throw(
				_("Set Daily Rate Divisor under Leave Provision in Company Payroll "
				  "Settings for {0}.").format(self.company)
			)
		self.expense_account = settings.leave_provision_expense_account
		self.liability_account = settings.leave_provision_liability_account

		self.set("leave_types", [])
		for row in settings.leave_provision_leave_types or []:
			self.append("leave_types", {"leave_type": row.leave_type})

		# Say which setting is missing, by its name on the settings form. These
		# fields are read-only here, so a blank one cannot be filled in on this
		# document - without this the run would stop on "value missing" against a
		# field nobody can type into.
		missing = [
			label for value, label in (
				(self.basic_pay_component, "Leave Provision Basic Pay Component"),
				(self.divisor, "Leave Provision Divisor"),
				(self.expense_account, "Leave Provision Expense Account"),
				(self.liability_account, "Leave Provision Liability Account"),
				(self.leave_types, "Leave Provision Leave Types"),
			) if not value
		]
		if missing:
			frappe.throw(_(
				"Set the following in Company Payroll Settings for {0}, then try "
				"again: {1}."
			).format(self.company, ", ".join(missing)))

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

		lines = []
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
				lines.append(frappe._dict({
					"employee": employee.name,
					"employee_name": employee.employee_name,
					"grouping": (employee.get(group_field) if group_field else None)
								or _("Unassigned"),
					"leave_type": leave_type,
					"basic_pay": basic,
					"daily_rate": daily_rate,
					"leave_balance": balance,
					"liability": liability,
				}))

		self._lines = lines
		self.total_liability = flt(total, 2)
		return lines

	def lines(self):
		"""The per-employee working, worked out rather than stored.

		It used to be a grid on the document. Kept out of the way now: it is the
		same figure the Leave Liability report shows, employee by employee, so
		the substantiation an auditor wants is still there without a hundred rows
		sitting on every provision.
		"""
		if getattr(self, "_lines", None) is None:
			self.build_lines()
		return self._lines

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

	def group_totals(self):
		grouped = {}
		for row in self.lines():
			key = row.grouping or _("Unassigned")
			grouped.setdefault(key, {"amount": 0.0, "people": 0})
			grouped[key]["amount"] += flt(row.liability)
			grouped[key]["people"] += 1
		return grouped

	def create_journal_entry(self):
		"""Post the whole liability, then reverse it on the first day after.

		The reversing method: each period states the full obligation rather than
		the change since last time, and the entry is backed out at the start of
		the next period so the following provision can state its own full figure
		without doubling up. Reverse plus re-state nets to the same closing
		liability, and the same charge to the P&L, as posting only the movement -
		what it changes is that every period's journal reads as the liability in
		its own right.

		One expense line per group, so the departmental split is on the face of
		the journal instead of only in a report.
		"""
		groups = self.group_totals()
		total = flt(self.total_liability, 2)
		if not total:
			frappe.throw(_("The liability comes to nil, so there is nothing to post."))

		accounts = []
		for group in sorted(groups):
			amount = flt(groups[group]["amount"], 2)
			if not amount:
				continue
			accounts.append({
				"account": self.expense_account,
				"debit_in_account_currency": amount,
				"credit_in_account_currency": 0,
				"user_remark": _("{0} - {1} employees").format(
					group, groups[group]["people"]),
			})

		accounts.append({
			"account": self.liability_account,
			"debit_in_account_currency": 0,
			"credit_in_account_currency": total,
			"user_remark": _("Leave provision {0} to {1}")
						   .format(self.from_date, self.to_date),
		})

		remark = self.journal_remark(groups)

		provision = self.post_journal(self.posting_date, accounts, remark)
		self.db_set("journal_entry", provision)

		# The day after the provision was posted, not the day after the period.
		# Those are the same date when a provision is raised on time, but a July
		# provision posted in August would otherwise reverse on 1 August - before
		# the entry it reverses, which reads as nonsense in the ledger and leaves
		# the liability standing. Following the posting date keeps the reversal
		# after the provision whenever it is actually raised.
		reversal = self.post_journal(
			add_days(getdate(self.posting_date), 1),
			[self.mirror(row) for row in accounts],
			_("Reversal of {0}").format(remark),
		)
		self.db_set("reversal_journal_entry", reversal)

	@staticmethod
	def mirror(row):
		"""The same line the other way round."""
		flipped = dict(row)
		flipped["debit_in_account_currency"] = row["credit_in_account_currency"]
		flipped["credit_in_account_currency"] = row["debit_in_account_currency"]
		return flipped

	def journal_remark(self, groups):
		"""Name the period and what each group carries.

		The departments belong on the remark: whoever opens the journal in the
		general ledger months later sees where the liability sits without going
		back to the provision that raised it.
		"""
		split = ", ".join(
			"{0} {1:,.2f}".format(group, flt(groups[group]["amount"], 2))
			for group in sorted(groups) if flt(groups[group]["amount"], 2)
		)
		return _("Leave provision {0} | {1} to {2} | {3}").format(
			self.name, self.from_date, self.to_date, split or _("no split"))

	def post_journal(self, posting_date, accounts, remark):
		journal = frappe.get_doc({
			"doctype": "Journal Entry",
			"voucher_type": "Journal Entry",
			"company": self.company,
			"posting_date": posting_date,
			"user_remark": remark,
			"accounts": accounts,
		})
		journal.flags.ignore_permissions = True
		journal.insert()
		journal.submit()
		return journal.name

	def cancel_journal_entry(self):
		"""Cancel both entries. The reversal goes first: leaving it behind while
		the provision it reverses is gone would credit the expense account for a
		charge that no longer exists."""
		for fieldname in ("reversal_journal_entry", "journal_entry"):
			name = self.get(fieldname)
			if not name or not frappe.db.exists("Journal Entry", name):
				continue
			journal = frappe.get_doc("Journal Entry", name)
			if journal.docstatus == 1:
				journal.flags.ignore_permissions = True
				journal.cancel()
