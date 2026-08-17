# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

import math

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class CBA(Document):
	def validate(self):
		self.carry_forward_previous_rates()
		for row in self.table_dqro:
			current_basic_pay = flt(row.current_basic_pay)
			percentage_increase = flt(row.percentage_increase)
			increase_amount = math.ceil(current_basic_pay * percentage_increase / 100)
			row.increase_amount = increase_amount
			row.new_basic_pay = current_basic_pay + increase_amount

	def carry_forward_previous_rates(self):
		"""Start a new agreement from where the last one left off.

		Only when the pay table is still empty, so it fills a fresh document and
		never overwrites rates somebody has entered or amended.

		Each category starts at the rate the previous agreement moved it to -
		its New Basic Pay, which is what people are actually on now. All that is
		left to enter is this round's percentage; the amount and the new rate
		compute from there.
		"""
		if self.table_dqro or not self.company:
			return

		rows = previous_rates(
			self.company, before=self.effective_start_date, exclude=self.name
		)
		if not rows:
			return

		for row in rows:
			self.append("table_dqro", {
				"job_category": row["job_category"],
				"current_basic_pay": row["current_basic_pay"],
			})

		frappe.msgprint(
			_("Rates carried forward from {0}, effective {1}. Enter this round's "
			  "percentage increase against each category.")
			.format(rows[0]["source"], frappe.format_value(
				rows[0]["source_from"], {"fieldtype": "Date"})),
			indicator="blue", alert=True,
		)


@frappe.whitelist()
def previous_rates(company, before=None, exclude=None):
	"""Each job category and the rate it stands at, from the agreement that ran
	before this one.

	``before`` is the new agreement's own start date, and it matters: taking
	simply the latest agreement would hand a backdated round the rates of one
	that came after it. What is wanted is whichever agreement was in force
	immediately before this one starts. With no date set yet, the most recent
	submitted agreement is the best guess available.

	Returned rather than written, so the form can offer the rates as soon as the
	company and date are known, and validate can fall back on the same figures
	if it was never asked.
	"""
	if not company:
		return []

	filters = {"company": company, "docstatus": 1}
	if before:
		filters["effective_start_date"] = ("<", before)
	if exclude:
		filters["name"] = ("!=", exclude)

	source = frappe.db.get_value(
		"CBA", filters, ["name", "effective_start_date"], as_dict=True,
		# creation breaks a tie between two agreements starting the same day:
		# the one entered later is the one that supersedes.
		order_by="effective_start_date desc, creation desc",
	)
	if not source:
		return []

	rows = frappe.get_all(
		"CBA Pay Table",
		filters={"parent": source.name, "parenttype": "CBA"},
		fields=["job_category", "current_basic_pay", "new_basic_pay"],
		order_by="idx",
	)

	return [
		{
			"job_category": row.job_category,
			# Where the last agreement left them. If its new rate was never
			# worked out, what they were on when it was signed.
			"current_basic_pay": flt(row.new_basic_pay) or flt(row.current_basic_pay),
			"source": source.name,
			"source_from": source.effective_start_date,
		}
		for row in rows
		if row.job_category
	]
