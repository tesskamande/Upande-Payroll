import frappe
from frappe import _
from frappe.utils import flt


def create_lta(doc, method=None):
	"""Pay a Leave Travelling Allowance when an employee takes a long enough
	single stretch of qualifying leave.

	Hooked on Leave Application ``on_submit``. Everything that varies between
	companies - the leave type, the component, the amount and the qualifying
	length - comes from Company Payroll Settings, so this is the same code for
	every client rather than a per-company dict.

	The rule is one allowance per leave allocation period, not per application.
	An employee who splits their annual leave still gets paid once.
	"""
	cfg = _config(doc.company)
	if not cfg:
		return
	if doc.leave_type != cfg.lta_leave_type:
		return
	if flt(doc.total_leave_days) < flt(cfg.lta_minimum_days):
		return

	allocation = _covering_allocation(doc, cfg)
	if not allocation:
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
		"ref_docname": doc.name,
		"remarks": _(
			"Leave Travelling Allowance for {0}. {1} days of {2} from {3}. "
			"Leave period {4} to {5}. Ref: {6}"
		).format(doc.employee_name, flt(doc.total_leave_days), doc.leave_type,
				 doc.from_date, allocation.from_date, allocation.to_date, doc.name),
	})
	lta.insert(ignore_permissions=True)
	lta.submit()

	frappe.msgprint(
		_("{0} of {1} created for {2}, payable {3}.").format(
			cfg.lta_salary_component,
			frappe.format_value(flt(cfg.lta_amount), {"fieldtype": "Currency"}),
			doc.employee_name, doc.posting_date),
		title=_("Leave Travelling Allowance Created"), indicator="green")


def cancel_lta(doc, method=None):
	"""Cancel the allowance if its leave application is cancelled.

	The original Server Script only ran After Submit, so cancelling the leave
	left the Additional Salary standing and the employee kept an allowance for
	leave they never took. It also blocked any future claim, because the
	orphaned record still counted as "already paid" for that period.
	"""
	for name in frappe.get_all(
		"Additional Salary",
		filters={"ref_doctype": "Leave Application", "ref_docname": doc.name, "docstatus": 1},
		pluck="name",
	):
		frappe.get_doc("Additional Salary", name).cancel()
		frappe.msgprint(_("Cancelled Leave Travelling Allowance {0}.").format(name),
						indicator="orange")


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


def _already_paid(doc, cfg, allocation):
	"""Returns a message if this period is already accounted for, else None."""
	existing = frappe.get_all(
		"Additional Salary",
		filters={
			"employee": doc.employee, "salary_component": cfg.lta_salary_component,
			"docstatus": ("!=", 2),
			"payroll_date": ("between", [allocation.from_date, allocation.to_date]),
		},
		pluck="name", limit=1,
	)
	if existing:
		return _("{0} already has {1} for {2} to {3} ({4}).").format(
			doc.employee_name, cfg.lta_salary_component,
			allocation.from_date, allocation.to_date, existing[0])

	prior = frappe.get_all(
		"Leave Application",
		filters={
			"employee": doc.employee, "leave_type": cfg.lta_leave_type, "docstatus": 1,
			"name": ("!=", doc.name),
			"from_date": ("between", [allocation.from_date, allocation.to_date]),
			"total_leave_days": (">=", flt(cfg.lta_minimum_days)),
		},
		fields=["name", "from_date", "total_leave_days"], limit=1,
	)
	if prior:
		p = prior[0]
		return _("{0} already took qualifying leave in {1} to {2} - {3}, {4} days from {5}.").format(
			doc.employee_name, allocation.from_date, allocation.to_date,
			p.name, flt(p.total_leave_days), p.from_date)

	return None
