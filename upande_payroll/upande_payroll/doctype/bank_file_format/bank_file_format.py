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
		self._validate_bank_account_matches_bank()

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

	def _validate_bank_account_matches_bank(self):
		"""The account paid from has to be held at the bank being paid through.

		Picking another bank's account here produces a file that balances on
		paper and is refused at upload, with a message about the debit account
		that gives no hint the format is pointing at the wrong bank.
		"""
		if not self.debit_bank_account:
			return
		held_at = frappe.db.get_value("Bank Account", self.debit_bank_account, "bank")
		if held_at and held_at != self.bank:
			frappe.throw(
				_("{0} is held at {1}, but this format sends to {2}.").format(
					frappe.bold(self.debit_bank_account), frappe.bold(held_at), frappe.bold(self.bank)
				),
				title=_("Wrong Bank"),
			)
