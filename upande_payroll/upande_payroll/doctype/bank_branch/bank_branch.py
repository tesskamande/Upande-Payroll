# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import _format_autoname


class BankBranch(Document):
	def validate(self):
		self._trim()
		self._validate_code_is_unique_within_bank()

	def on_update(self):
		self._keep_name_in_step()

	def _trim(self):
		"""Codes arrive pasted from bank circulars and spreadsheets.

		A trailing space is invisible on the form and fatal in a fixed-width
		advice file, and it also defeats the uniqueness check below, which
		compares strings.
		"""
		for field in ("branch_name", "branch_code"):
			value = (self.get(field) or "").strip()
			if not value:
				frappe.throw(
					_("{0} cannot be blank.").format(_(self.meta.get_label(field))),
					title=_("Missing Branch Detail"),
				)
			self.set(field, value)

	def _validate_code_is_unique_within_bank(self):
		"""One code per bank, not one code overall.

		Branch codes are only unique inside a bank - two banks numbering a
		branch 001 is normal - so the constraint cannot be a unique index on
		the field.
		"""
		clash = frappe.db.get_value(
			"Bank Branch",
			{
				"bank": self.bank,
				"branch_code": self.branch_code,
				"name": ("!=", self.name),
			},
			["name", "branch_name"],
			as_dict=True,
		)
		if clash:
			frappe.throw(
				_("Branch code {0} already belongs to {1} at {2}.").format(
					frappe.bold(self.branch_code),
					frappe.bold(clash.branch_name),
					frappe.bold(self.bank),
				),
				title=_("Duplicate Branch Code"),
			)

	def _keep_name_in_step(self):
		"""Rename when the bank or the branch name is edited.

		The name is composed from both by `autoname`, but a `format:` autoname
		is applied once, at insert. Correct a misspelled branch name later and
		the record would keep announcing the misspelling to every Employee link
		that points at it. Renaming through the framework carries those links
		across, which is the reason to do it here rather than block the edit.
		"""
		if self.flags.in_bank_branch_rename:
			return

		expected = _format_autoname(self.meta.autoname, self)
		if expected == self.name:
			return

		self.flags.in_bank_branch_rename = True
		try:
			self.rename(expected)
		finally:
			self.flags.in_bank_branch_rename = False
