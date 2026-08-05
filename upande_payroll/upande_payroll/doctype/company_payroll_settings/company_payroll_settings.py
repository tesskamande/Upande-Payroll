# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class CompanyPayrollSettings(Document):
	def validate(self):
		self.validate_overtime_department_working_hours()
		self.validate_terminal_dues_notice_period_rules()
		self.validate_statutory_income_component_mapping()

	def validate_overtime_department_working_hours(self):
		seen = set()
		for row in self.overtime_department_working_hours or []:
			if row.department in seen:
				frappe.throw(
					f"Department '{row.department}' appears more than once in "
					f"Department Working Hours Overrides (row {row.idx})."
				)
			seen.add(row.department)

	def validate_terminal_dues_notice_period_rules(self):
		rows = sorted(
			self.terminal_dues_notice_period_rules or [],
			key=lambda r: flt(r.minimum_years_of_service),
		)
		for i, row in enumerate(rows):
			lower = flt(row.minimum_years_of_service)
			upper = flt(row.maximum_years_of_service)
			is_top_tier = i == len(rows) - 1

			if upper and upper <= lower:
				frappe.throw(
					f"Notice Period Rules row {row.idx}: Maximum Years of Service ({upper}) "
					f"must be greater than Minimum Years of Service ({lower})."
				)
			if not upper and not is_top_tier:
				frappe.throw(
					f"Notice Period Rules row {row.idx}: Maximum Years of Service is required "
					f"unless this is the highest tier (open-ended)."
				)
			if i > 0:
				prev_upper = flt(rows[i - 1].maximum_years_of_service)
				if prev_upper != lower:
					frappe.throw(
						f"Notice Period Rules: row {rows[i - 1].idx} ends at {prev_upper} years "
						f"but row {row.idx} starts at {lower} years - ranges must be contiguous "
						f"with no gaps or overlaps."
					)

	def validate_statutory_income_component_mapping(self):
		seen = set()
		for row in self.statutory_income_component_mapping or []:
			if row.salary_component in seen:
				frappe.throw(
					f"Salary Component '{row.salary_component}' appears more than once in "
					f"Statutory Income Component Mapping (row {row.idx})."
				)
			seen.add(row.salary_component)


def get_monthly_working_hours(company, department=None):
	"""Return the effective monthly working hours for a department, falling back
	to the default configured on Company Payroll Settings for that company."""
	settings = frappe.get_cached_doc("Company Payroll Settings", company)

	if department:
		for row in settings.overtime_department_working_hours or []:
			if row.department == department:
				return row.monthly_working_hours

	return settings.default_monthly_working_hours


def get_notice_days(company, years_worked):
	"""Return notice days for the given tenure: the Notice Period Rule whose
	[Minimum, Maximum) Years of Service range contains years_worked. A blank
	Maximum Years of Service is the open-ended top tier."""
	settings = frappe.get_cached_doc("Company Payroll Settings", company)
	years_worked = flt(years_worked)

	for row in settings.terminal_dues_notice_period_rules or []:
		lower = flt(row.minimum_years_of_service)
		upper = flt(row.maximum_years_of_service)
		if years_worked >= lower and (not upper or years_worked < upper):
			return row.notice_days

	return 0
