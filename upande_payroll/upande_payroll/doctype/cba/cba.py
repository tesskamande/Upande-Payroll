# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

import math

from frappe.model.document import Document
from frappe.utils import flt


class CBA(Document):
	def validate(self):
		for row in self.table_dqro:
			current_basic_pay = flt(row.current_basic_pay)
			percentage_increase = flt(row.percentage_increase)
			increase_amount = math.ceil(current_basic_pay * percentage_increase / 100)
			row.increase_amount = increase_amount
			row.new_basic_pay = current_basic_pay + increase_amount
