import frappe
from frappe import _

# Employee fields Payroll Entry can narrow a run by, on top of the branch,
# department, designation and grade HRMS already offers.
#
# Empty on purpose: the Advanced Filters box covers any Employee field, so a
# dedicated one has to earn the space on the form. Add a fieldname here and the
# form field beside it in setup.py if one ever does.
EXTRA_FILTERS = []


class PayrollEntryMixin:
	"""Carry the extra filters into the employee query.

	make_filters() builds the dict HRMS hands to the employee query. It only
	knows about its own fields, so anything added to the form has to be put in
	here or it never reaches the query.
	"""

	def get_salary_slip_details(self, for_withheld_salaries=False):
		"""The rows the bank entry is worked out from, without the employer's
		own contributions.

		make_bank_entry() adds up every earning on the slips and takes off every
		deduction, and an employer contribution is carried as a deduction so it
		gets taken off too - money the employee never bore, subtracted from what
		the bank pays them. A run of four came to 161,523.95 against a net pay of
		179,373.95, short by exactly the employer NSSF and Housing Levy.

		Filtered here rather than by flagging the components, because the two
		flags that would exclude them from this also exclude them from the
		accrual (payroll_entry.py:394), where they belong - expense debited,
		liability to the fund credited. This is the only caller of this method,
		so nothing else is affected.
		"""
		rows = super().get_salary_slip_details(for_withheld_salaries)

		# Releasing a chosen few rather than everyone withheld this period.
		# release_withheld_salaries() puts the choice here because this is the
		# one place the bank entry decides whose money it is paying - the same
		# rows also decide which withholding cycles get stamped and released.
		chosen = getattr(self, "_release_employees", None)
		if for_withheld_salaries and chosen:
			rows = [row for row in rows if row.employee in chosen]

		# Narrowed to one bank's staff while make_bank_entry works through the
		# groups. Applies to withheld releases too, so releasing a withheld
		# batch still pays each person out of their own bank.
		only = getattr(self, "_only_employees", None)
		if only is not None:
			rows = [row for row in rows if row.employee in only]

		employer_side = employer_contribution_components()
		if not employer_side:
			return rows
		return [row for row in rows if row.salary_component not in employer_side]

	@frappe.whitelist()
	def has_bank_entries(self) -> dict[str, bool]:
		"""Whether the two bank-entry buttons should be offered.

		HRMS answers the withheld half with "is nobody flagged withheld", and
		that flag only clears once the withheld journal is SUBMITTED. So between
		raising the journal and posting it - which is exactly where someone sits
		while they check the figures - the button stays live, and every further
		click writes another draft for the same money. Nothing links the drafts
		and each one is submittable.

		Asking for the journal itself instead closes that window.

		The ordinary half now has to be re-asked rather than taken from HRMS.
		Its query counts ANY Bank Entry referencing this run as proof the staff
		have been paid, and the liability remittances reference the run too so
		they show up under Connections. Left alone, remitting KRA would take
		away the button that pays the wages. Remittances are discounted here -
		they settle a statutory account, not the payroll payable.
		"""
		result = super().has_bank_entries()

		if result.get("has_bank_entries"):
			result["has_bank_entries"] = bool(self.non_remittance_bank_entries())

		if not result.get("has_bank_entries_for_withheld_salaries"):
			if self.pending_withheld_bank_entry():
				result["has_bank_entries_for_withheld_salaries"] = True
		return result

	def non_remittance_bank_entries(self):
		"""Bank entries for this run that actually pay the staff.

		The same query HRMS runs (payroll_entry.py:897), minus anything the
		liability remittance raised.
		"""
		return frappe.db.sql_list(
			"""
			SELECT DISTINCT je.name
			FROM `tabJournal Entry` je
			INNER JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
			WHERE je.voucher_type IN ('Bank Entry', 'Cash Entry')
				AND jea.reference_type = 'Payroll Entry'
				AND jea.reference_name = %s
				AND IFNULL(je.custom_source_payroll_entry, '') = ''
			LIMIT 1
			""",
			self.name,
		)

	@frappe.whitelist()
	def make_bank_entry(self, for_withheld_salaries=False):
		"""Guard the same window on the server, for anything not going through
		the button - a repost, an integration, a second browser tab."""
		if for_withheld_salaries:
			pending = self.pending_withheld_bank_entry()
			if pending:
				frappe.throw(
					_("{0} already covers the withheld salaries on this run and has not "
					  "been submitted. Submit or delete it before raising another.").format(
						frappe.utils.get_link_to_form("Journal Entry", pending)
					),
					title=_("Bank Entry Already Raised"),
				)
		groups = self.salary_bank_groups(for_withheld_salaries)
		if not groups:
			return super().make_bank_entry(for_withheld_salaries=for_withheld_salaries)

		# payment_account is what core credits (payroll_entry.py,
		# set_accounting_entries_for_bank_entry), so each pass is the stock
		# routine with a different bank and a different slice of the run. The
		# document is never saved, so the swap lives only for this call.
		# bank_account is cleared with it: it names a Bank Account record for
		# the run's usual account, and leaving it stamped on another bank's
		# entry would label the payment with the wrong one.
		original_account, original_bank = self.payment_account, self.bank_account
		created = []
		try:
			for account, employees, label in groups:
				self._only_employees = employees
				self.payment_account = account
				self.bank_account = None
				entry = super().make_bank_entry(for_withheld_salaries=for_withheld_salaries)
				if entry:
					created.append((label, entry))
		finally:
			self._only_employees = None
			self.payment_account = original_account
			self.bank_account = original_bank

		if not created:
			return None
		self.announce_bank_split(created)
		# Core hands back one document. The caller only routes to the journal
		# list filtered by this run, which shows all of them.
		return created[0][1]

	def salary_bank_groups(self, for_withheld_salaries=False):
		"""Who is paid from which of the company's accounts, or [] to leave it alone.

		Empty when the company has not switched the split on, so the stock
		single entry is used and nothing about the existing behaviour changes.
		"""
		if not frappe.db.exists("Company Payroll Settings", self.company):
			return []
		settings = frappe.get_cached_doc("Company Payroll Settings", self.company)
		if not settings.get("enable_salary_bank_split"):
			return []

		accounts = {
			row.bank: row.payment_account
			for row in (settings.get("salary_bank_accounts") or [])
			if row.bank and row.payment_account
		}
		if not accounts:
			return []

		# The same rows the entry would have been built from, before any bank
		# filter is applied - so leavers, withheld staff and anyone else core
		# excludes are excluded here too rather than reasoned about again.
		self._only_employees = None
		employees = {row.employee for row in self.get_salary_slip_details(for_withheld_salaries)}
		if not employees:
			return []

		banks = frappe.get_all(
			"Employee", filters={"name": ("in", list(employees))},
			fields=["name", "bank_name"],
		)

		by_account, unmapped = {}, set()
		for employee in banks:
			account = accounts.get(employee.bank_name) if employee.bank_name else None
			if not account:
				# No bank on the record, or a bank nobody has given an account
				# to. Paid from the run's own Payment Account exactly as before,
				# rather than held back - somebody must still be paid.
				unmapped.add(employee.name)
				continue
			by_account.setdefault((account, employee.bank_name), set()).add(employee.name)

		groups = [
			(account, staff, bank)
			for (account, bank), staff in sorted(by_account.items(), key=lambda kv: kv[0][1])
		]

		# Nobody matched a configured bank, so there is nothing this can do that
		# the stock single entry does not already do better.
		if not groups:
			return []

		if unmapped:
			if not self.payment_account:
				frappe.throw(
					_("{0} employee(s) on this run have no bank set, or bank at one with no "
					  "company account against it, and the Payroll Entry has no Payment "
					  "Account to fall back on.").format(len(unmapped))
				)
			groups.append((self.payment_account, unmapped, _("no bank set")))

		# One group is still returned rather than shortcut away. If everybody
		# banks at KCB and KCB has been given the company's KCB account, that
		# account is what should pay them - falling back to the stock entry here
		# would quietly pay them all from the run's Payment Account instead and
		# ignore the configuration.
		return groups

	def announce_bank_split(self, created):
		rows = "".join(
			"<tr><td>{0}</td><td>{1}</td><td style='text-align:right'>{2}</td></tr>".format(
				frappe.utils.escape_html(str(label)),
				frappe.utils.get_link_to_form("Journal Entry", entry.name),
				frappe.format_value(entry.total_credit, {"fieldtype": "Currency"}),
			)
			for label, entry in created
		)
		frappe.msgprint(
			_("Net pay has been split across the staff's own banks. Each entry still "
			  "needs its Reference No and Date before it can be submitted."
			  "<br><br><table class='table table-bordered'>"
			  "<thead><tr><th>Bank</th><th>Entry</th>"
			  "<th style='text-align:right'>Amount</th></tr></thead>"
			  "<tbody>{0}</tbody></table>").format(rows),
			title=_("Paid From {0} Accounts").format(len(created)),
			indicator="green",
		)

	def pending_withheld_bank_entry(self):
		"""An unsubmitted withheld bank entry for this run, if there is one.

		Told apart from an ordinary bank entry by the withholding cycle it is
		stamped on rather than by its remark, which is translated and would stop
		matching in any other language.
		"""
		found = frappe.db.sql_list(
			"""
			SELECT DISTINCT je.name
			FROM `tabJournal Entry` je
			INNER JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
			INNER JOIN `tabSalary Withholding Cycle` c ON c.journal_entry = je.name
			WHERE je.docstatus = 0
				AND jea.reference_type = 'Payroll Entry'
				AND jea.reference_name = %s
			LIMIT 1
			""",
			self.name,
		)
		return found[0] if found else None

	@frappe.whitelist()
	def release_withheld_salaries(self, employees=None):
		"""Raise the withheld bank entry for the employees chosen, not for all.

		HRMS releases everyone withheld in the run at once. Passing nothing here
		keeps that behaviour, so the stock button still works.
		"""
		self.check_permission("write")
		# None means "release everyone", which is what the stock button does.
		# An empty list is a different thing - somebody chose nobody - and must
		# not fall through to paying the whole run.
		if employees is None:
			chosen = None
		else:
			chosen = [e for e in (frappe.parse_json(employees) or []) if e]
			if not chosen:
				frappe.throw(_("Choose at least one employee to release."))

		if chosen:
			self._release_employees = set(chosen)
		try:
			entry = self.make_bank_entry(for_withheld_salaries=True)
		finally:
			self._release_employees = None

		if not entry:
			frappe.throw(_("Nothing to release for the employees chosen."))
		return entry.name

	@frappe.whitelist()
	def withheld_employees(self):
		"""Who is still withheld on this run, for the release dialog."""
		return frappe.db.sql(
			"""
			SELECT ss.employee, ss.employee_name, ss.net_pay, ss.salary_withholding
			FROM `tabSalary Slip` ss
			WHERE ss.payroll_entry = %s AND ss.docstatus = 1 AND ss.status = 'Withheld'
			ORDER BY ss.employee_name
			""",
			self.name,
			as_dict=True,
		)

	def make_filters(self):
		filters = super().make_filters()
		for fieldname in EXTRA_FILTERS:
			value = self.get(fieldname)
			if value:
				filters[fieldname] = value

		# Whatever was built in the Advanced Filters box, carried as JSON on the
		# form. Get Employees posts the whole document, so it arrives here
		# without needing to intercept the button.
		if self.get("custom_only_with_additional_salary"):
			filters["custom_only_with_additional_salary"] = 1

		advanced = self.get("advanced_employee_filters")
		if advanced:
			try:
				parsed = frappe.parse_json(advanced) or []
			except Exception:
				parsed = []
			if parsed:
				filters["advanced_employee_filters"] = parsed

		return filters



def _employees_with_additional_salary(company, start_date, end_date):
	"""Who has an Additional Salary earning that lands in this period.

	The date conditions are HRMS's own, from get_additional_salaries() - a
	one-off counts when its payroll date falls inside the period, a recurring
	one when the period ends inside its window. Written as a set query rather
	than asked per employee, but deliberately the same test: a filter that
	pulled somebody the payslip then had nothing for, or left out somebody it
	would have paid, would be worse than no filter.

	Earnings only. Somebody whose one Additional Salary is a deduction - a
	standing sacco contribution, say - has nothing being paid to them, and
	pulling them in produces a payslip that deducts from wages that are not
	there.
	"""
	if not (company and start_date and end_date):
		return []

	rows = frappe.db.sql(
		"""
		select distinct employee
		from `tabAdditional Salary`
		where docstatus = 1
			and ifnull(disabled, 0) = 0
			and type = 'Earning'
			and company = %(company)s
			and (
				(ifnull(is_recurring, 0) = 0
					and payroll_date between %(start_date)s and %(end_date)s)
				or (is_recurring = 1
					and from_date <= %(end_date)s
					and to_date >= %(end_date)s)
			)
		""",
		{"company": company, "start_date": start_date, "end_date": end_date},
		pluck=True,
	)
	return rows or []


def _excludes_leavers(company):
	"""Whether this company keeps mid-period leavers out of the payroll run.

	Off unless the company has said so: Frappe's own behaviour is to include
	them, and silently dropping people from a payroll run is not something to
	infer. The setting lives beside Terminal Dues because that is the
	arrangement it belongs to - the leaver's final days are paid on the
	settlement instead of on a payslip.
	"""
	if not company or not frappe.db.exists("Company Payroll Settings", company):
		return False
	choice = frappe.db.get_value("Company Payroll Settings", company, "leaver_payroll_policy")
	return choice == "Exclude from Payroll"


def patch_filter_conditions():
	"""Teach HRMS's employee query to apply the extra filters.

	set_filter_conditions() walks a hardcoded list of fieldnames, so a filter
	the form collects and make_filters() passes along is still dropped on the
	floor - the run would quietly include everybody, which is the worst way for
	a payroll filter to fail. Wrapping the function is the smallest change that
	leaves the rest of HRMS's query building alone.

	Guarded so repeated imports do not wrap the wrapper, and so an HRMS release
	that moves or renames this function leaves the filters visibly not working
	rather than breaking payroll outright.
	"""
	try:
		from hrms.payroll.doctype.payroll_entry import payroll_entry as core
	except ImportError:
		return

	original = getattr(core, "set_filter_conditions", None)
	if original is None or getattr(original, "_upande_wrapped", False):
		return

	def wrapped(query, filters, qb_object):
		query = original(query, filters, qb_object)
		for fieldname in EXTRA_FILTERS:
			if filters.get(fieldname):
				query = query.where(qb_object[fieldname] == filters[fieldname])

		if filters.get("custom_only_with_additional_salary"):
			paid = _employees_with_additional_salary(
				filters.get("company"), filters.get("start_date"), filters.get("end_date")
			)
			# Same rule as above: nothing matched has to mean nobody.
			query = query.where(qb_object.name.isin(paid or [""]))

		if _excludes_leavers(filters.get("company")):
			# Core keeps anybody whose leaving date falls on or after the period
			# start, so a mid-period leaver is pulled in and paid pro-rata. A
			# company settling leavers through a Terminal Dues Settlement pays
			# those same days there, so leaving them in the run pays the month
			# twice. Only somebody still employed past the end of the period, or
			# with no leaving date at all, survives this.
			query = query.where(
				qb_object.relieving_date.isnull()
				| (qb_object.relieving_date > filters.get("end_date"))
			)

		advanced = filters.get("advanced_employee_filters")
		if advanced:
			# Resolved through Frappe's own filter engine rather than translated
			# into query-builder conditions here. The box can produce like, in,
			# between, is set and the rest, and a hand-written translation of
			# those is where a payroll filter starts quietly matching the wrong
			# people.
			scoped = _combine_same_field(advanced)
			if filters.get("company"):
				scoped.append(["company", "=", filters["company"]])

			allowed = frappe.get_all("Employee", filters=scoped, pluck="name")
			# An empty IN list is not valid SQL, and "nothing matched" has to
			# mean nobody - never everybody.
			query = query.where(qb_object.name.isin(allowed or [""]))

		return query

	wrapped._upande_wrapped = True
	core.set_filter_conditions = wrapped


patch_filter_conditions()


def employer_contribution_components():
	"""Components the employer pays on top of wages, rather than out of them."""
	return {
		row.name
		for row in frappe.get_all(
			"Salary Component",
			filters={"custom_is_employer_contribution": 1},
			fields=["name"],
		)
	}


def _combine_same_field(conditions):
	"""Turn repeated equals on one field into a single "any of these".

	Conditions are ANDed, which is right across different fields - grade AND
	department narrows, as it should. On the SAME field it is never what anyone
	means: two rows reading Employment Type equals Contract and Employment Type
	equals Apprentice asks for somebody who is both, and matches nobody. Read as
	"either", which is what was intended, it returns both groups.

	Only plain equals is folded. like, between, greater than and the rest keep
	their AND, because combining those changes what they ask rather than
	clarifying it.
	"""
	equals, others, order = {}, [], []

	for condition in conditions:
		fieldname, operator, value = condition[0], condition[1], condition[2]
		if str(operator).strip().lower() not in ("=", "equals"):
			others.append(condition)
			continue
		if fieldname not in equals:
			equals[fieldname] = []
			order.append(fieldname)
		if value not in equals[fieldname]:
			equals[fieldname].append(value)

	combined = []
	for fieldname in order:
		values = equals[fieldname]
		combined.append(
			[fieldname, "=", values[0]] if len(values) == 1
			else [fieldname, "in", values]
		)

	return combined + others
