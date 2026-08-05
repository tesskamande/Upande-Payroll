import frappe
from frappe.utils import flt

from upande_payroll.upande_payroll.doctype.company_payroll_settings.company_payroll_settings import (
	get_monthly_working_hours,
)


class OvertimeSlipMixin:
	"""Replaces core HRMS's Overtime Slip amount calculation with a config-driven
	one: the hourly rate comes from Company Payroll Settings (Basic Pay divided
	by Default/Department Monthly Working Hours) instead of the employee's
	Salary Structure or a fixed rate on Overtime Type. The multiplier and
	Salary Component come straight from whichever Overtime Type is picked per
	row - no date-based weekend/public-holiday detection, since a rest day
	worked is paid the same as a normal overtime day (1.5x) unless it happens
	to be an actual local holiday (2.0x), and that distinction is made by
	whoever enters the row, not inferred from the Holiday List (which would
	otherwise treat every rest day as a holiday).

	Overrides on_submit() entirely (not just supplemented) so core's own
	process_overtime_slip() never runs alongside this - it would otherwise
	silently create a second Additional Salary the moment Overtime Type's own
	Hourly Rate / Applicable Salary Component fields are filled in.
	"""

	def on_submit(self):
		settings = frappe.get_cached_doc("Company Payroll Settings", self.company)
		if not settings.enable_overtime_calculation:
			return super().on_submit()
		self.process_overtime_slip(settings)

	def process_overtime_slip(self, settings):
		component_totals = self.get_overtime_component_amounts(settings)
		self._replace_additional_salary(component_totals)

	def get_overtime_component_amounts(self, settings):
		if not self.overtime_details:
			return {}

		basic_pay = flt(frappe.db.get_value("Employee", self.employee, "basic_pay"))
		monthly_hours = get_monthly_working_hours(self.company, self.department)
		hourly_rate = flt(basic_pay / monthly_hours, 4) if monthly_hours else 0.0

		overtime_types = self._bulk_load_overtime_types()

		component_totals = {}
		for row in self.overtime_details:
			ot_type = overtime_types.get(row.overtime_type)
			if not ot_type:
				frappe.throw(f"Row {row.idx}: Overtime Type '{row.overtime_type}' not found.")

			salary_component = ot_type.overtime_salary_component
			if not salary_component:
				frappe.throw(
					f"Row {row.idx}: Overtime Type '{row.overtime_type}' has no Overtime Salary "
					f"Component set."
				)

			multiplier = flt(ot_type.standard_multiplier) or 1.0
			row_amount = flt(hourly_rate * multiplier * flt(row.overtime_duration), 2)

			component_totals[salary_component] = component_totals.get(salary_component, 0.0) + row_amount

		return component_totals

	def _bulk_load_overtime_types(self):
		names = {row.overtime_type for row in self.overtime_details if row.overtime_type}
		if not names:
			return {}
		rows = frappe.get_all(
			"Overtime Type",
			filters={"name": ["in", list(names)]},
			fields=["name", "overtime_salary_component", "standard_multiplier"],
		)
		return {row.name: row for row in rows}

	def _replace_additional_salary(self, component_totals):
		# Re-processing (e.g. amend) shouldn't leave stale entries behind.
		existing = frappe.get_all(
			"Additional Salary",
			filters={"ref_docname": self.name, "docstatus": ("!=", 2)},
			pluck="name",
		)
		for name in existing:
			ad = frappe.get_doc("Additional Salary", name)
			if ad.docstatus == 1:
				ad.cancel()
			ad.delete()

		for salary_component, total_amount in component_totals.items():
			if total_amount <= 0:
				continue
			additional_salary = frappe.get_doc({
				"doctype": "Additional Salary",
				"company": self.company,
				"employee": self.employee,
				"salary_component": salary_component,
				"amount": total_amount,
				"payroll_date": self.end_date,
				"overwrite_salary_structure_amount": 0,
				"ref_doctype": "Overtime Slip",
				"ref_docname": self.name,
			})
			additional_salary.submit()
			frappe.msgprint(
				f"{salary_component}: Additional Salary of {total_amount:,.2f} created for {self.employee_name}.",
				indicator="green",
			)
