# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

# Row Count, Amount Total and Blank compute themselves.
SOURCES_NEEDING_A_VALUE = ("Field", "Constant", "Composed")


class BankFileFormat(Document):
	def validate(self):
		self._validate_has_detail_rows()
		self._validate_values_present()

	def _validate_has_detail_rows(self):
		if not [c for c in self.columns if c.section == "Detail"]:
			frappe.throw(
				_("Add at least one Detail column. Those are the rows the employees go on."),
				title=_("Nothing To Write"),
			)

	def _validate_values_present(self):
		"""Row Count and Amount Total compute themselves; the rest need telling.

		A Field column with no value would write a column of blanks into a file
		the bank rejects, and the rejection would name the bank's own column,
		not this row.
		"""
		for column in self.columns:
			if column.source in SOURCES_NEEDING_A_VALUE and not (column.value or "").strip():
				frappe.throw(
					_("Row {0}: {1} needs a Value.").format(column.idx, _(column.source)),
					title=_("Incomplete Column"),
				)
