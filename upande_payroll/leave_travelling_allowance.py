import frappe
from frappe import _
from frappe.utils import flt


def create_lta(doc, method=None):
	"""Pay a Leave Travelling Allowance once an employee has taken enough
	qualifying leave in a leave period.

	Hooked on Leave Application ``on_submit``. Everything that varies between
	companies - the leave type, the component, the amount and the qualifying
	length - comes from Company Payroll Settings, so this is the same code for
	every client rather than a per-company dict.

	Days are counted across the whole leave allocation period, not per
	application. Somebody who takes their annual leave as two short breaks has
	still been away for the qualifying stretch, so the allowance falls due on
	whichever application takes the running total over the minimum. It is still
	paid once per period.
	"""
	cfg = _config(doc.company)
	if not cfg:
		return
	if doc.leave_type != cfg.lta_leave_type:
		return

	grade, department = _employee_grade_department(doc.employee)
	if _is_blocked(cfg, grade, department):
		return

	allocation = _covering_allocation(doc, cfg)
	if not allocation:
		return

	amount, minimum = _lta_terms(cfg, grade, department)
	taken = _days_taken(doc, cfg, allocation)
	if taken < minimum:
		return

	blocker = _already_paid(doc, cfg, allocation, minimum)
	if blocker:
		frappe.msgprint(blocker, title=_("Leave Travelling Allowance Not Created"),
						indicator="orange")
		return

	lta = frappe.get_doc({
		"doctype": "Additional Salary",
		"employee": doc.employee,
		"company": doc.company,
		"salary_component": cfg.lta_salary_component,
		"amount": amount,
		"payroll_date": doc.posting_date,
		"overwrite_salary_structure_amount": 0,
		"ref_doctype": "Leave Application",
		# Additional Salary carries no notes field, so the reasoning goes to the
		# user in the message below rather than into a key the document quietly
		# discards. ref_docname is the durable trail: it points at the leave
		# application that reached the minimum.
		"ref_docname": doc.name,
	})
	lta.insert(ignore_permissions=True)
	lta.submit()

	frappe.msgprint(
		_("{0} of {1} created for {2}, payable {3}. {4} of {5} qualifying days taken.").format(
			cfg.lta_salary_component,
			frappe.format_value(amount, {"fieldtype": "Currency"}),
			doc.employee_name, doc.posting_date, taken, minimum),
		title=_("Leave Travelling Allowance Created"), indicator="green")


def cancel_lta(doc, method=None):
	"""Cancel the allowance if its leave application is cancelled.

	The original Server Script only ran After Submit, so cancelling the leave
	left the Additional Salary standing and the employee kept an allowance for
	leave they never took. It also blocked any future claim, because the
	orphaned record still counted as "already paid" for that period.

	Because days are counted across the period, an application that never
	raised an allowance itself may still have been part of what qualified one.
	Cancelling it has to put the running total back and withdraw the allowance
	if the employee no longer reaches the minimum.
	"""
	withdrawn = _cancel_allowances({
		"ref_doctype": "Leave Application", "ref_docname": doc.name, "docstatus": 1,
	})
	if withdrawn:
		return

	cfg = _config(doc.company)
	if not cfg or doc.leave_type != cfg.lta_leave_type:
		return

	allocation = _covering_allocation(doc, cfg)
	if not allocation:
		return

	# on_cancel runs with docstatus already 2, so this application is out of the
	# count; excluding it by name as well keeps that independent of ordering.
	grade, department = _employee_grade_department(doc.employee)
	_, minimum = _lta_terms(cfg, grade, department)
	if _days_taken(doc, cfg, allocation, include_self=False) >= minimum:
		return

	siblings = [row.name for row in _period_applications(doc, cfg, allocation, exclude=doc.name)]
	if not siblings:
		return

	_cancel_allowances({
		"employee": doc.employee,
		"salary_component": cfg.lta_salary_component,
		"docstatus": 1,
		"ref_doctype": "Leave Application",
		"ref_docname": ("in", siblings),
	})


# ----------------------------------------------------------------------

def _config(company):
	if not company or not frappe.db.exists("Company Payroll Settings", company):
		return None
	settings = frappe.get_cached_doc("Company Payroll Settings", company)
	if not settings.enable_leave_travelling_allowance:
		return None
	if not (settings.lta_leave_type and settings.lta_salary_component):
		return None
	return settings


def _employee_grade_department(employee):
	"""Fetched once per Leave Application event and passed down, rather than
	re-queried by each of _is_blocked() and _lta_terms() separately - neither
	can change mid-event, so a second and third lookup for the same event
	would just be repeat traffic for data already in hand."""
	return frappe.db.get_value("Employee", employee, ["grade", "department"]) or (None, None)


def _matching_row(rows, grade, department, label):
	"""The one row (if any) whose Grade or Department matches this employee.

	Each row names exactly one of the two (company_payroll_settings.py's
	_validate_lta_table enforces that, the same shape as Deduction Priority
	Detail's Salary Component/Loan Product pair) - but nothing stops a
	Grade-keyed row and a Department-keyed row from both naming this same
	employee, one by each field, since that can only be known once it is
	checked against a real employee rather than at save time. Raised here
	rather than resolved by picking one, because picking one silently is
	exactly the bug this used to have."""
	matches = [
		row for row in (rows or [])
		if (row.employee_grade and row.employee_grade == grade)
		or (row.department and row.department == department)
	]
	if len(matches) > 1:
		frappe.throw(_(
			"{0} has more than one matching row in {1} - one by Grade, one by "
			"Department. Remove one of them before this can be resolved."
		).format(grade or department, label))
	return matches[0] if matches else None


def _is_blocked(cfg, grade, department):
	"""Whether this employee's Grade or Department is named in LTA Not
	Available To - checked before anything else, so a blocked employee's
	leave is never even weighed against the qualifying minimum."""
	return _matching_row(cfg.lta_blocked_grades_departments, grade, department, _("LTA Not Available To")) is not None


def _lta_terms(cfg, grade, department):
	"""The Amount and Minimum Leave Days this employee's LTA actually runs on -
	their own Grade/Department override if Company Payroll Settings has one
	for them, the company-wide figures otherwise. A matching row's own
	Minimum Leave Days still falls back to the company-wide figure when left
	blank - only the Amount is mandatory on a row.
	"""
	best = _matching_row(cfg.lta_amount_overrides, grade, department, _("LTA Amount by Grade or Department"))
	amount = flt(best.amount) if best else flt(cfg.lta_amount)
	minimum = flt(best.minimum_days) if best and best.minimum_days else flt(cfg.lta_minimum_days)
	return amount, minimum


def _covering_allocation(doc, cfg):
	"""The Leave Allocation whose period contains this leave. That period, not
	the calendar year, is what the allowance is paid once against."""
	rows = frappe.get_all(
		"Leave Allocation",
		filters={
			"employee": doc.employee, "leave_type": cfg.lta_leave_type, "docstatus": 1,
			"from_date": ("<=", doc.from_date), "to_date": (">=", doc.from_date),
		},
		fields=["name", "from_date", "to_date"],
		order_by="from_date desc", limit=1,
	)
	return rows[0] if rows else None


def _period_applications(doc, cfg, allocation, exclude=None):
	"""Every submitted application for the qualifying leave type that starts
	inside this allocation period."""
	filters = {
		"employee": doc.employee, "leave_type": cfg.lta_leave_type, "docstatus": 1,
		"from_date": ("between", [allocation.from_date, allocation.to_date]),
	}
	if exclude:
		filters["name"] = ("!=", exclude)
	return frappe.get_all(
		"Leave Application", filters=filters,
		fields=["name", "from_date", "total_leave_days"], order_by="from_date",
	)


def _days_taken(doc, cfg, allocation, include_self=True):
	"""Qualifying leave days for the period. The application in hand is added
	on rather than read back, so the answer does not depend on whether the
	database already carries its new docstatus."""
	total = 0.0
	for row in _period_applications(doc, cfg, allocation, exclude=doc.name):
		total += flt(row.total_leave_days)
	if include_self:
		total += flt(doc.total_leave_days)
	return flt(total, 2)


def _already_paid(doc, cfg, allocation, minimum):
	"""Returns a message if this period is already accounted for, else None.

	``minimum`` is the caller's own already-computed _lta_terms() result,
	passed in rather than recomputed here - create_lta() has it in hand the
	moment this is called, and a second lookup for the same employee in the
	same event would only be repeat traffic.

	An allowance this code raised is matched by the leave application it points
	at, not by its payroll date: the payroll date follows the leave
	application's posting date, so leave taken at the end of a period is paid
	in the next one and a date match would look in the wrong period both ways.
	A payroll date match still catches an allowance entered by hand, which has
	nothing to point at.
	"""
	period = _period_applications(doc, cfg, allocation, exclude=doc.name)
	names = [row.name for row in period]

	if names:
		linked = frappe.get_all(
			"Additional Salary",
			filters={
				"employee": doc.employee, "salary_component": cfg.lta_salary_component,
				"docstatus": ("!=", 2),
				"ref_doctype": "Leave Application", "ref_docname": ("in", names),
			},
			fields=["name", "ref_docname"], limit=1,
		)
		if linked:
			return _("{0} already has {1} for leave period {2} to {3} - {4}, raised on {5}.").format(
				doc.employee_name, cfg.lta_salary_component,
				allocation.from_date, allocation.to_date,
				linked[0].name, linked[0].ref_docname)

	# The entitlement, not just the payment. The days already taken in this
	# period are what was earned against, and they stay taken whatever happens
	# to the Additional Salary afterwards - so somebody who cancels the
	# allowance by hand cannot hand the employee a second one by approving more
	# leave. Only submitted applications count, so cancelling the leave itself
	# does release the entitlement, which is the one case that should.
	taken = flt(sum(flt(row.total_leave_days) for row in period), 2)
	if taken >= minimum:
		return _("{0} has already taken {1} of {2} qualifying days in {3} to {4}, "
				 "so the allowance for this period has been earned.").format(
			doc.employee_name, taken, minimum,
			allocation.from_date, allocation.to_date)

	own = set(names)
	own.add(doc.name)
	for row in frappe.get_all(
		"Additional Salary",
		filters={
			"employee": doc.employee, "salary_component": cfg.lta_salary_component,
			"docstatus": ("!=", 2),
			"payroll_date": ("between", [allocation.from_date, allocation.to_date]),
		},
		fields=["name", "ref_doctype", "ref_docname"],
	):
		if row.ref_doctype == "Leave Application" and row.ref_docname not in own:
			# Belongs to a neighbouring period and only its payroll date landed
			# in this one. Its own period already blocks a second claim there.
			continue
		return _("{0} already has {1} dated inside {2} to {3} ({4}).").format(
			doc.employee_name, cfg.lta_salary_component,
			allocation.from_date, allocation.to_date, row.name)

	return None


def _cancel_allowances(filters):
	"""Cancel every Additional Salary matching ``filters``. Returns the names."""
	cancelled = []
	for name in frappe.get_all("Additional Salary", filters=filters, pluck="name"):
		frappe.get_doc("Additional Salary", name).cancel()
		cancelled.append(name)
	if cancelled:
		frappe.msgprint(
			_("Cancelled Leave Travelling Allowance {0}.").format(", ".join(cancelled)),
			indicator="orange")
	return cancelled
