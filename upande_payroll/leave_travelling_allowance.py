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

	allocation = _covering_allocation(doc, cfg)
	if not allocation:
		return

	minimum = flt(cfg.lta_minimum_days)
	taken = _days_taken(doc, cfg, allocation)
	if taken < minimum:
		return

	blocker = _already_paid(doc, cfg, allocation)
	if blocker:
		frappe.msgprint(blocker, title=_("Leave Travelling Allowance Not Created"),
						indicator="orange")
		return

	lta = frappe.get_doc({
		"doctype": "Additional Salary",
		"employee": doc.employee,
		"company": doc.company,
		"salary_component": cfg.lta_salary_component,
		"amount": flt(cfg.lta_amount),
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
			frappe.format_value(flt(cfg.lta_amount), {"fieldtype": "Currency"}),
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
	if _days_taken(doc, cfg, allocation, include_self=False) >= flt(cfg.lta_minimum_days):
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


def _already_paid(doc, cfg, allocation):
	"""Returns a message if this period is already accounted for, else None.

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
	if taken >= flt(cfg.lta_minimum_days):
		return _("{0} has already taken {1} of {2} qualifying days in {3} to {4}, "
				 "so the allowance for this period has been earned.").format(
			doc.employee_name, taken, flt(cfg.lta_minimum_days),
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
