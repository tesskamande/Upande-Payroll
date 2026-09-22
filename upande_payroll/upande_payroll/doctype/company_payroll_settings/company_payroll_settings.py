# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class CompanyPayrollSettings(Document):
	def validate(self):
		self._validate_salary_bank_split()
		self.validate_overtime_department_working_hours()
		self.validate_terminal_dues_notice_period_rules()
		self.validate_statutory_income_component_mapping()
		self.validate_payroll_dimension_source()
		self.validate_payroll_remittance_accounts()

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
		"""One row per component, and each one on the side its category is read from.

		The calculator looks for the benefit categories in the payslip's
		earnings and the absence and relief categories in its deductions. A row
		pairing a category with a component from the other side is therefore
		never reached: the company has described the component, the figure looks
		mapped in the table, and nothing about the payslip changes. Caught here
		because there is no later point at which it can be noticed.
		"""
		from upande_payroll.kenya_statutory_gross_pay import CATEGORY_COMPONENT_TYPE

		seen = set()
		for row in self.statutory_income_component_mapping or []:
			if row.salary_component in seen:
				frappe.throw(
					f"Salary Component '{row.salary_component}' appears more than once in "
					f"Statutory Income Component Mapping (row {row.idx})."
				)
			seen.add(row.salary_component)

			# .get, not indexing: a category outside the Select - an older
			# record, an import - is left alone rather than raising here.
			expected = CATEGORY_COMPONENT_TYPE.get(row.category)
			if not expected or not row.salary_component:
				continue

			actual = frappe.get_cached_value("Salary Component", row.salary_component, "type")
			if actual and actual != expected:
				frappe.throw(
					_("Row {0}: {1} is {2} category, which is read from the payslip's "
					  "{3}s - but '{4}' is {5}. Mapped this way the row is never read "
					  "and the component is treated as ordinary pay.").format(
						row.idx, frappe.bold(row.category), expected.lower(),
						expected.lower(), row.salary_component, actual.lower()),
					title=_("Component On The Wrong Side"),
				)

	def validate_payroll_dimension_source(self):
		"""Tagging the journal needs an Accounting Dimension to tag it with.

		The journal asks Accounting Dimension for the GL column to write the
		Farm or Business Unit into. Where the company has never created that
		dimension there is no column, so the setting is honoured by posting
		nothing - the journal balances, every figure is right, and the tag the
		company asked for is simply absent from every line. Said here, on the
		form where the pick is made, rather than left to be noticed in the GL.
		"""
		source = self.get("payroll_dimension_source")
		if not source:
			return

		dimension = frappe.db.get_value(
			"Accounting Dimension", {"document_type": source}, ["name", "disabled"], as_dict=True
		)
		if dimension and not dimension.disabled:
			return

		frappe.msgprint(
			_("There is no {0} Accounting Dimension{1}, so nothing can carry {0} onto "
			  "the journal lines. The payroll journal will post correctly but untagged "
			  "until one is created.").format(
				frappe.bold(source), _(" in use") if dimension else ""),
			title=_("Journal Will Not Be Tagged"),
			indicator="orange",
		)
	def validate_payroll_remittance_accounts(self):
		"""Resolve each row to an account, then allow only one row per account.

		A row is usually picked by salary component, because that is the name on
		the payslip. The account behind it is what the journal credits and what
		the payment clears, so it is resolved here as well as in the form - a row
		written by an import or the API has never been near the client script.

		Two rows landing on the same account disagree about where it is paid from
		or whether it is paid at all, and nothing can choose between them. That
		is easy to do by accident: all four NSSF tiers share one account, so
		picking two of them names the same account twice.
		"""
		from upande_payroll.liability_remittance import component_account

		seen = {}
		for row in self.payroll_remittance_accounts or []:
			if row.salary_component:
				resolved = component_account(self.company, row.salary_component)
				if not resolved:
					frappe.throw(
						f"Row {row.idx}: '{row.salary_component}' has no Account set for "
						f"{self.company}. Set it on the Salary Component first."
					)
				row.liability_account = resolved
			elif not row.liability_account:
				frappe.throw(
					f"Row {row.idx}: pick a Salary Component, or set a Liability Account "
					f"directly for something with no component behind it."
				)

			if row.liability_account in seen:
				first = seen[row.liability_account]
				frappe.throw(
					f"Rows {first} and {row.idx} both come down to "
					f"'{row.liability_account}', so they disagree about how it is paid. "
					f"Components that share an account need one row between them."
				)
			seen[row.liability_account] = row.idx

	def _validate_salary_bank_split(self):
		"""Every row must name the thing the split is keyed on.

		The three columns look alike in the grid and only one of them is read.
		A Farm split with the Bank column filled in matches nobody, so every
		employee falls through to the run's Payment Account and the whole point
		of the split is silently lost.
		"""
		if not self.get("enable_salary_bank_split"):
			return

		from upande_payroll.payroll_entry_utils import SPLIT_DIMENSIONS

		dimension = self.get("salary_bank_split_by") or "Employee Bank"
		# .get rather than indexing: a value outside the Select - an old record,
		# an import - should not raise a KeyError out of validate.
		key_column, _employee_field, label = SPLIT_DIMENSIONS.get(
			dimension, SPLIT_DIMENSIONS["Employee Bank"]
		)

		for row in (self.get("salary_bank_accounts") or []):
			if not row.get(key_column):
				frappe.throw(
					_("Row {0}: the split is by {1}, so that row needs a {1}.").format(
						row.idx, _(label).title()
					),
					title=_("Incomplete Account Mapping"),
				)





def payroll_settings(company):
	"""This company's Company Payroll Settings, or None where it has none.

	Every feature in this app is opted into per company, on this record. A
	company with no record has opted into none of them, which is the ordinary
	state of things on a site that installed the app for one doctype and runs
	its payroll the stock way.

	Loading the record unguarded turned that into a crash. Salary Slip goes
	through this app's regional override whatever the site installed it for, so
	on such a site the first payroll run died with "Company Payroll Settings
	<company> not found" and no slips could be made at all - for a company that
	had never asked this app to calculate anything.

	So: no record means every rule here stands down and core is left to do what
	it did before the app arrived. Callers treat None the same way they treat
	an enable flag that is switched off.
	"""
	if not company or not frappe.db.exists("Company Payroll Settings", company):
		return None
	return frappe.get_cached_doc("Company Payroll Settings", company)


def get_monthly_working_hours(company, department=None):
	"""Return the effective monthly working hours for a department, falling back
	to the default configured on Company Payroll Settings for that company."""
	settings = payroll_settings(company)
	if not settings:
		return None

	if department:
		for row in settings.overtime_department_working_hours or []:
			if row.department == department:
				return row.monthly_working_hours

	return settings.default_monthly_working_hours


def get_notice_days(company, years_worked):
	"""Return notice days for the given tenure: the Notice Period Rule whose
	[Minimum, Maximum) Years of Service range contains years_worked. A blank
	Maximum Years of Service is the open-ended top tier."""
	settings = payroll_settings(company)
	if not settings:
		return 0
	years_worked = flt(years_worked)

	for row in settings.terminal_dues_notice_period_rules or []:
		lower = flt(row.minimum_years_of_service)
		upper = flt(row.maximum_years_of_service)
		if years_worked >= lower and (not upper or years_worked < upper):
			return row.notice_days

	return 0
