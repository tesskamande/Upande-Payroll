# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_months, cint, flt, fmt_money, get_last_day, getdate

# Currency comparisons at 2dp; anything under this is rounding noise, not a
# real difference between what is planned and what is owed.
TOLERANCE = 0.01

# Inputs that decide the shape of the plan. Change any of them and the schedule
# is rebuilt; touch anything else and HR's own arrangement of it is left alone.
PLAN_INPUTS = ("advance_type", "advance_amount", "repayment_periods", "repayment_frequency",
			   "repayment_start_date")

# Row states the parent must not overwrite. Both are decisions somebody made
# about that period, not conclusions drawn from what was collected.
MANUAL_ROW_STATUS = ("Deferred", "Waived")


PERIODS_PER_YEAR = {"Monthly": 12, "Weekly": 52}

# Weekly periods are anchored to the month rather than to a fixed weekday: the
# 7th, the 14th, the 21st, then whatever is left of the month. Four to a month,
# and the sequence restarts on the 1st, so a weekly schedule can never drift out
# of step with a payroll that closes its books at the month end - which is the
# whole reason the last period of a month runs long.
WEEK_ANCHORS = (7, 14, 21)


def _due_date(start, index, frequency):
	"""When the index-th period (0-based) falls due."""
	if (frequency or "Monthly") != "Weekly":
		return get_last_day(add_months(start, index))
	return _weekly_due(start, index)


def _weekly_due(start, index):
	start = getdate(start)
	month = start.replace(day=1)
	ends = []
	while len(ends) <= index:
		last = get_last_day(month)
		for day in WEEK_ANCHORS:
			anchor = month.replace(day=day)
			if start <= anchor < last:
				ends.append(anchor)
		if last >= start:
			ends.append(last)
		month = add_months(month, 1)
	return ends[index]


class EmployeeSalaryAdvance(Document):
	"""An advance paid to an employee outside payroll, recovered from salary.

	Independent of the lending module by design. A client may run payroll with
	no lending app installed and still give advances, so nothing here imports
	or reads a Loan doctype; where a client does have lending, its own Loan
	handling in deduction_cap.py continues to apply and the two never meet.

	The document holds a fixed sum and a plan for collecting it. Interest, if
	any, is simple interest for the term - principal x rate x months / 12 -
	fixed when the advance is granted and spread evenly across the instalments.
	That is what makes the plan safe to edit: money can move between periods
	without the total ever changing, so HR reshaping a schedule can never alter
	what is owed. Writing part of an advance off is a separate, explicit act,
	not something that can happen by mistyping an instalment.
	"""

	# ==================================================================
	# Document lifecycle
	# ==================================================================

	def validate(self):
		settings = self._settings()
		self._validate_dates()
		self._validate_amounts(settings)
		self._apply_policy(settings)
		self._validate_limits(settings)

		# A draft's schedule is rebuilt whenever the inputs that shape it move,
		# and otherwise left as it is - so an arrangement HR made before
		# submitting survives an unrelated edit to, say, the posting date.
		if self._plan_is_stale():
			self._build_schedule()
		else:
			self._check_schedule_adds_up()
			self._derive_rows()

		self._roll_up()

	def before_submit(self):
		if not self.repayment_schedule:
			frappe.throw(_("There is no repayment schedule to collect."))

	def before_update_after_submit(self):
		"""Check a reshaped schedule before any of it reaches the database.

		validate() does not run on a submitted document, and the schedule is
		editable there on purpose, so the checks that keep the plan honest have to
		be repeated - but they have to run here rather than in
		on_update_after_submit, which fires only after the rows have already been
		written. Refusing at that point relies on something else rolling the
		transaction back; refusing here stops the write from happening at all.
		"""
		self._check_schedule_adds_up()

	def on_update_after_submit(self):
		"""Re-derive what the reshaped plan implies, now that it is accepted."""
		self._derive_rows()
		self._roll_up()
		self._persist()

	def on_cancel(self):
		if flt(self.total_paid) > TOLERANCE:
			frappe.throw(
				_("{0} has already been recovered against this advance. Cancel "
				  "the salary slips that collected it first, or close the "
				  "advance instead of cancelling it.").format(
					self._money(self.total_paid)
				)
			)
		self.status = "Cancelled"

	# ==================================================================
	# Recovery
	# ==================================================================

	def apply_recovery(self, reference_doctype, reference_name, posting_date,
					   available, as_at=None):
		"""Credit what a document collected, oldest period first.

		The caller says how much it collected; this decides which periods that
		money settles. Oldest first, because the longest standing debt is the one
		to clear - and because crediting a later period while an earlier one still
		owes would leave the schedule saying arrears exist when they have been paid.

		``as_at`` bounds it to periods already due, which is what an ordinary
		payslip wants. Left out, every remaining period is fair game - a leaver's
		last payslip and a terminal dues settlement both collect the whole balance,
		so both need to be able to credit periods the calendar has not reached.

		Returns what it managed to place, which may be less than it was offered if
		the advance simply does not owe that much.
		"""
		available = flt(available, 2)
		if available <= TOLERANCE:
			return 0.0

		spent = 0.0
		for row in self.repayment_schedule:
			if available - spent <= TOLERANCE:
				break
			if row.status == "Waived":
				continue
			if as_at and getdate(row.due_date) > getdate(as_at):
				continue

			owing = flt(flt(row.instalment_amount) - flt(row.paid_amount), 2)
			if owing <= TOLERANCE:
				continue

			paid = flt(min(owing, available - spent), 2)
			self.append("recoveries", {
				"reference_doctype": reference_doctype,
				"reference_name": reference_name,
				"recovered_on": posting_date,
				"period_no": row.period_no,
				"schedule_row": row.name,
				"amount": paid,
			})
			spent = flt(spent + paid, 2)

		if spent > TOLERANCE:
			self.save(ignore_permissions=True)

		return spent

	def reverse_recovery(self, reference_doctype, reference_name):
		"""Drop everything one document collected, and re-derive from what is left.

		The whole reversal, because the paid figures are a view of the log rather
		than a running total: take the entries away and the periods go back to
		owing what they owed.
		"""
		kept = [
			entry for entry in (self.recoveries or [])
			if not (entry.reference_doctype == reference_doctype
					and entry.reference_name == reference_name)
		]
		if len(kept) == len(self.recoveries or []):
			return

		self.set("recoveries", kept)
		self.save(ignore_permissions=True)

	# ==================================================================
	# Policy
	# ==================================================================

	def _settings(self):
		settings = frappe.get_cached_doc(
			"Employee Salary Advance Type", self.advance_type
		)
		if settings.disabled:
			frappe.throw(
				_("Advance type {0} is disabled.").format(frappe.bold(self.advance_type))
			)
		if settings.company != self.company:
			frappe.throw(
				_("Advance type {0} belongs to {1}, but this advance is for {2}.").format(
					frappe.bold(self.advance_type), settings.company, self.company
				)
			)
		return settings

	def _apply_policy(self, settings):
		"""Copy the rules onto the advance as they stood when it was granted.

		These fields are fetched for display, but they are also what the
		schedule was calculated from. Storing them means a later change to the
		advance type cannot silently restate the terms of an advance already
		running.
		"""
		self.interest_method = settings.interest_method
		self.interest_rate = settings.interest_rate
		self.salary_component = settings.salary_component

	def _validate_dates(self):
		if getdate(self.repayment_start_date) < getdate(self.posting_date):
			frappe.throw(
				_("Repayment starts {0}, before the advance is even granted on "
				  "{1}.").format(
					frappe.format(self.repayment_start_date, "Date"),
					frappe.format(self.posting_date, "Date"),
				)
			)

	def _validate_amounts(self, settings):
		if flt(self.advance_amount) <= 0:
			frappe.throw(_("Advance Amount must be more than zero."))
		if cint(self.repayment_periods) < 1:
			frappe.throw(_("Repayment Periods must be at least one."))

	def _validate_limits(self, settings):
		"""Apply whichever ceilings the advance type actually sets.

		A limit left blank is not enforced. The advance type warns about that
		when it is saved, so the gap is a stated position rather than an
		oversight, and nothing is invented here to fill it.
		"""
		if flt(settings.max_advance_amount) > 0 and (
			flt(self.advance_amount) > flt(settings.max_advance_amount) + TOLERANCE
		):
			frappe.throw(
				_("{0} is above the {1} allowed for {2}.").format(
					self._money(self.advance_amount),
					self._money(settings.max_advance_amount),
					frappe.bold(self.advance_type),
				)
			)

		if cint(settings.max_repayment_periods) > 0 and (
			cint(self.repayment_periods) > cint(settings.max_repayment_periods)
		):
			frappe.throw(
				_("{0} allows at most {1} repayment periods.").format(
					frappe.bold(self.advance_type),
					cint(settings.max_repayment_periods),
				)
			)

		running = self._other_active_advances()

		if cint(settings.max_active_advances) > 0 and (
			len(running) + 1 > cint(settings.max_active_advances)
		):
			frappe.throw(
				_("{0} already has {1} active advance(s) and {2} allows {3}. "
				  "Clear one before granting another.").format(
					frappe.bold(self.employee_name or self.employee),
					len(running),
					frappe.bold(self.advance_type),
					cint(settings.max_active_advances),
				)
			)

		if flt(settings.max_total_exposure) > 0:
			outstanding = flt(sum(flt(a.outstanding_amount) for a in running), 2)
			proposed = flt(outstanding + flt(self.advance_amount), 2)
			if proposed > flt(settings.max_total_exposure) + TOLERANCE:
				frappe.throw(
					_("This would take {0} to {1} outstanding, above the {2} "
					  "allowed by {3}.").format(
						frappe.bold(self.employee_name or self.employee),
						self._money(proposed),
						self._money(settings.max_total_exposure),
						frappe.bold(self.advance_type),
					)
				)

	def _other_active_advances(self):
		"""Every other advance of this employee's that is still being collected."""
		return frappe.get_all(
			"Employee Salary Advance",
			filters={
				"employee": self.employee,
				"docstatus": 1,
				"status": ("in", ("Unpaid", "Partially Repaid")),
				"name": ("!=", self.name or ""),
			},
			fields=["name", "outstanding_amount"],
		)

	# ==================================================================
	# The plan
	# ==================================================================

	def _total_interest(self, settings):
		"""Simple interest for the time the money is out.

		principal x rate x months / 12, and nothing else - the rate is per
		annum, so a six month advance is charged for six months, which is also
		what makes early settlement trivial: periods that never came due were
		never charged for.

		The method is read from the advance type and never assumed. An advance
		type that says nothing about interest is unconfigured, not interest
		free, and refusing here is what keeps that distinction visible.
		"""
		if not settings.interest_method:
			frappe.throw(
				_("Advance type {0} does not say whether interest is charged. "
				  "Set its Interest Method.").format(frappe.bold(self.advance_type))
			)

		if settings.interest_method == "Interest Free":
			return 0.0

		rate = flt(settings.interest_rate)
		if rate <= 0:
			frappe.throw(
				_("Advance type {0} charges interest per annum but has no rate. "
				  "Set the rate, or set the method to Interest Free.").format(
					frappe.bold(self.advance_type)
				)
			)

		# The rate is per annum, so the term has to be expressed in years - and
		# a weekly advance's periods are weeks, not months. Dividing 16 weekly
		# periods by 12 would charge a four month advance sixteen months of
		# interest.
		periods = cint(self.repayment_periods)
		# .get rather than a subscript: an advance saved before this field
		# existed carries nothing, and a term charged at a guessed frequency is
		# better than a payroll run that stops on a KeyError.
		per_year = PERIODS_PER_YEAR.get(self.repayment_frequency or "Monthly", 12)
		return flt(flt(self.advance_amount) * rate / 100.0 * periods / float(per_year), 2)

	def _totals(self):
		"""What is owed, and the even instalment it divides into."""
		settings = self._settings()
		principal = flt(self.advance_amount, 2)
		periods = cint(self.repayment_periods)
		interest = self._total_interest(settings)
		total = flt(principal + interest, 2)

		self.total_interest = interest
		self.total_repayable = total
		self.monthly_instalment = flt(total / periods, 2) if periods else 0.0

		return total, interest, periods

	def _plan_is_stale(self):
		"""Has anything the schedule was calculated from moved?"""
		if not self.repayment_schedule or self.is_new():
			return True

		before = self.get_doc_before_save()
		if not before:
			return True

		for fieldname in PLAN_INPUTS:
			old, new = before.get(fieldname), self.get(fieldname)
			if fieldname == "repayment_start_date":
				if getdate(old) != getdate(new):
					return True
			elif fieldname == "advance_amount":
				if abs(flt(old) - flt(new)) > TOLERANCE:
					return True
			elif fieldname == "repayment_periods":
				if cint(old) != cint(new):
					return True
			elif old != new:
				return True

		return len(self.repayment_schedule) != cint(self.repayment_periods)

	def _build_schedule(self):
		"""Spread the total evenly over the repayment periods.

		Instalments are equal, with the last one absorbing the rounding so the
		schedule sums to exactly what is owed rather than a cent either side of
		it. Only ever called on a draft whose inputs changed - once submitted,
		the plan is HR's, and rebuilding it would throw their edits away.
		"""
		total, interest, periods = self._totals()

		even = flt(total / periods, 2)
		even_interest = flt(interest / periods, 2)
		start = getdate(self.repayment_start_date)

		rows, billed, billed_interest = [], 0.0, 0.0
		for period in range(1, periods + 1):
			if period == periods:
				amount = flt(total - billed, 2)
				period_interest = flt(interest - billed_interest, 2)
			else:
				amount, period_interest = even, even_interest

			rows.append({
				"period_no": period,
				"due_date": _due_date(start, period - 1, self.repayment_frequency),
				"opening_balance": flt(total - billed, 2),
				"principal_amount": flt(amount - period_interest, 2),
				"interest_amount": period_interest,
				"instalment_amount": amount,
				"paid_amount": 0.0,
				"outstanding_amount": amount,
				"status": "Pending",
			})
			billed = flt(billed + amount, 2)
			billed_interest = flt(billed_interest + period_interest, 2)

		self.set("repayment_schedule", rows)

	def _check_schedule_adds_up(self):
		"""A reshaped plan must still collect exactly what is owed.

		HR can move money between periods - a lean month, a bonus month, a
		period skipped by setting its instalment to zero - but not change the
		total. Recovering less than the full amount is a decision about the
		debt, taken through the Deferred Deduction the payslip raises when it
		cannot collect, not something an edited row should be able to do.
		"""
		self._sync_paid_from_recoveries()

		total = self._totals()[0]
		planned = flt(
			sum(flt(row.instalment_amount) for row in (self.repayment_schedule or [])), 2
		)
		if abs(planned - total) > TOLERANCE:
			difference = flt(planned - total, 2)
			frappe.throw(
				_("The schedule collects {0} but {1} is owed - {2} {3}. Adjust "
				  "the instalments so they add up to {1}.").format(
					self._money(planned),
					self._money(total),
					self._money(abs(difference)),
					_("too much") if difference > 0 else _("short"),
				),
				title=_("Schedule does not add up"),
			)

		for row in (self.repayment_schedule or []):
			if flt(row.instalment_amount) < 0:
				frappe.throw(
					_("Row {0}: an instalment cannot be negative. Use zero to "
					  "skip a period.").format(row.idx)
				)

			# A period cannot owe less than it has already been paid. Allowing it
			# leaves the row saying one thing and the recovery log another, and the
			# periods after it claiming money that is no longer outstanding. Money
			# already collected is moved by reducing a period still to come.
			if flt(row.paid_amount) - flt(row.instalment_amount) > TOLERANCE:
				frappe.throw(
					_("Period {0} has already collected {1}, so its instalment cannot "
					  "be set to {2}. Reduce a period that is still outstanding "
					  "instead.").format(
						row.period_no or row.idx,
						self._money(row.paid_amount),
						self._money(row.instalment_amount),
					),
					title=_("Instalment below what was collected"),
				)

	def _sync_paid_from_recoveries(self):
		"""Derive each period's paid figure from the recovery log.

		The log is the record of what was collected; the paid figures on the
		schedule are a view of it. Deriving them rather than accumulating in place
		is what makes a cancelled salary slip reversible - its entries go and the
		schedule follows - and it lets two slips pay towards the same period
		without one overwriting the other.

		Entries are matched to the row they were collected against, not to a
		period number. Periods are renumbered whenever the plan is reshaped, so
		matching on position would quietly move a payment to a different month.
		"""
		collected = {}
		for entry in (self.recoveries or []):
			key = entry.schedule_row or str(entry.period_no)
			collected[key] = flt(collected.get(key, 0.0) + flt(entry.amount), 2)

		for row in (self.repayment_schedule or []):
			paid = collected.pop(row.name, None)
			if paid is None:
				paid = collected.pop(str(row.period_no), 0.0)
			row.paid_amount = flt(paid, 2)

		stranded = flt(sum(flt(v) for v in collected.values()), 2)
		if stranded > TOLERANCE:
			frappe.throw(
				_("{0} has already been collected against a period that is no longer "
				  "in the schedule. Restore that period, or cancel the salary slip "
				  "that collected it.").format(self._money(stranded)),
				title=_("Paid period removed"),
			)

	def _derive_rows(self):
		"""Recompute everything on the rows except the instalment itself.

		Periods are renumbered and due dates re-derived from the start date, so
		the plan stays a run of consecutive payroll months however HR has
		rearranged the amounts. Interest is apportioned in proportion to each
		instalment, which keeps the split meaningful after an edit without
		changing the total charged.
		"""
		self._sync_paid_from_recoveries()

		rows = self.repayment_schedule or []
		total, interest, _periods = self._totals()
		start = getdate(self.repayment_start_date)

		planned = flt(sum(flt(row.instalment_amount) for row in rows), 2)
		balance, apportioned = total, 0.0

		for index, row in enumerate(rows):
			amount = flt(row.instalment_amount, 2)

			if index == len(rows) - 1:
				row.interest_amount = flt(interest - apportioned, 2)
			else:
				share = (amount / planned) if planned else 0.0
				row.interest_amount = flt(interest * share, 2)
			apportioned = flt(apportioned + flt(row.interest_amount), 2)

			row.period_no = index + 1
			row.due_date = _due_date(start, index, self.repayment_frequency)
			row.opening_balance = flt(balance, 2)
			row.principal_amount = flt(amount - flt(row.interest_amount), 2)
			row.outstanding_amount = flt(max(amount - flt(row.paid_amount), 0.0), 2)
			balance = flt(balance - amount, 2)

			if row.status not in MANUAL_ROW_STATUS:
				row.status = self._row_status(row)

	def _row_status(self, row):
		paid, due = flt(row.paid_amount), flt(row.instalment_amount)
		if paid <= TOLERANCE:
			return "Pending"
		if paid + TOLERANCE < due:
			return "Partially Paid"
		return "Paid"

	def _roll_up(self):
		"""Total what has been collected, and say where the advance stands."""
		rows = self.repayment_schedule or []
		paid = flt(sum(flt(row.paid_amount) for row in rows), 2)
		total = flt(self.total_repayable, 2)

		self.total_paid = paid
		self.outstanding_amount = flt(max(total - paid, 0.0), 2)

		if self.docstatus == 2:
			self.status = "Cancelled"
		elif self.docstatus == 0:
			self.status = "Draft"
		elif self.outstanding_amount <= TOLERANCE:
			self.status = "Repaid"
		elif paid > TOLERANCE:
			self.status = "Partially Repaid"
		else:
			self.status = "Unpaid"

	def _persist(self):
		"""Write derived values back on a submitted document.

		Everything recomputed after submit is read-only, so it has to go to the
		database directly - the form did not send it and will not save it.
		"""
		for fieldname in ("total_interest", "total_repayable", "monthly_instalment",
						  "total_paid", "outstanding_amount", "status"):
			self.db_set(fieldname, self.get(fieldname), update_modified=False)

		for row in (self.repayment_schedule or []):
			row.db_update()

	# ==================================================================
	# Helpers
	# ==================================================================

	def _money(self, amount):
		return fmt_money(flt(amount), currency=self._currency())

	def _currency(self):
		if not self.get("__currency"):
			self.set("__currency", frappe.get_cached_value(
				"Company", self.company, "default_currency"
			))
		return self.get("__currency")
