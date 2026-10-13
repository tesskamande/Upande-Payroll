import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

TOLERANCE = 0.01


class BulkSalaryReview(Document):
	"""One HR Manager approval, applied to many employees at once.

	Submitting this does not itself raise anyone's pay - it creates one
	Salary Review per row, pre-filled from that row, and submits each in
	turn. Salary Review is still the document of record for an individual's
	pay change; this is only how a batch of them gets created together,
	with one shared reason and effective date.

	Cancelling cascades the same way: every Salary Review this created is
	cancelled too, which - exactly as it would if cancelled one at a time -
	puts Employee.basic_pay back where auto_update_basic_pay says to.
	"""

	def validate(self):
		if not self.employees:
			frappe.throw(_("Add at least one employee."))
		errors = []
		for row in self.employees:
			live_basic_pay = flt(frappe.db.get_value("Employee", row.employee, "basic_pay"))
			if abs(live_basic_pay - flt(row.current_basic_pay)) > TOLERANCE:
				errors.append(
					_("Row {0} ({1}): Current Basic Pay here is {2}, but the employee is now on {3} - "
					  "something else changed their pay since this row was added. Remove the row and "
					  "add them again (Get Employees, or pick them fresh) to pick up the current figure.")
					.format(row.idx, row.employee_name or row.employee, row.current_basic_pay, live_basic_pay)
				)
				continue
			_compute_row_pay(row)
			if flt(row.proposed_basic_pay) <= 0:
				errors.append(
					_("Row {0} ({1}): give a Proposed Basic Pay, Increment Amount, or Increment % that comes to more than zero.")
					.format(row.idx, row.employee_name or row.employee)
				)
		if errors:
			frappe.throw("<br>".join(errors), title=_("Fix These Rows"))

	@frappe.whitelist()
	def get_employees(self):
		filters = {"status": "Active", "company": self.company}
		for field in ("department", "designation", "branch", "grade"):
			if self.get(field):
				filters[field] = self.get(field)
		existing = {row.employee for row in self.employees}
		employees = frappe.get_all(
			"Employee",
			filters=filters,
			fields=["name as employee", "employee_name", "department", "basic_pay as current_basic_pay"],
		)
		return [e for e in employees if e.employee not in existing]

	def on_submit(self):
		for row in self.employees:
			review = frappe.get_doc({
				"doctype": "Salary Review",
				"employee": row.employee,
				"company": self.company,
				"review_type": self.review_type,
				"effective_date": self.effective_date,
				"auto_update_basic_pay": self.auto_update_basic_pay,
				"current_basic_pay": row.current_basic_pay,
				"proposed_basic_pay": row.proposed_basic_pay,
				"increment_amount": row.increment_amount,
				"increment_percent": row.increment_percent,
				"reason": self.reason,
				"bulk_salary_review": self.name,
			})
			review.insert(ignore_permissions=True)
			review.submit()
			row.db_set("salary_review", review.name, update_modified=False)

	def on_cancel(self):
		for row in self.employees:
			if not row.salary_review:
				continue
			if not frappe.db.exists("Salary Review", row.salary_review):
				continue
			doc = frappe.get_doc("Salary Review", row.salary_review)
			if doc.docstatus == 1:
				doc.cancel()


def _compute_row_pay(row):
	"""Fill in whichever of the three pay fields a row's own JS hasn't.

	The form keeps Proposed Basic Pay, Increment Amount and Increment % in
	step with each other as they're typed - this is the same computation,
	run again here so a row built by the API or a Data Import, with no
	client script to do it, still ends up consistent before the Salary
	Review it becomes is created from it.
	"""
	current = flt(row.current_basic_pay)
	if not current:
		return
	if flt(row.proposed_basic_pay):
		increment = flt(flt(row.proposed_basic_pay) - current, 2)
		row.increment_amount = increment
		row.increment_percent = flt((increment / current) * 100, 2)
	elif flt(row.increment_amount):
		increment = flt(row.increment_amount)
		row.proposed_basic_pay = flt(current + increment, 2)
		row.increment_percent = flt((increment / current) * 100, 2)
	elif flt(row.increment_percent):
		increment = flt(current * flt(row.increment_percent) / 100.0, 2)
		row.increment_amount = increment
		row.proposed_basic_pay = flt(current + increment, 2)
