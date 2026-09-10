# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

"""Turn the Bank Remittance report into the file a particular bank accepts.

Every bank wants the same facts in a different shape. KCB takes a flat table
with a title line above it and the bank and branch codes glued together as one
clearing code. Absa takes no table at all, but a sequence of record types -
one file header, one batch line carrying the paying account, one line per
employee, one trailer with the count and the total. Equity takes a flat table
again, with different headings and a different order.

None of that shape is in this file. It is read from a Bank File Format record,
so a bank revising its template is an edit to a record and a new client's bank
is a new record. What this module knows is how to turn such a record plus the
report's rows into a workbook.

A line the report marked as unsendable is left out. That is the difference
between the report and the file: the report shows a missing account number so
somebody fixes it, the file must not carry it to the bank at all.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, formatdate, getdate, today

from upande_payroll.upande_payroll.report.bank_remittance.bank_remittance import (
	execute as run_bank_remittance,
)


@frappe.whitelist()
def formats_for(company, bank=None):
	"""Formats the Bank Remittance report may offer for these filters."""
	filters = {"company": company, "disabled": 0}
	if bank:
		filters["bank"] = bank
	return frappe.get_all(
		"Bank File Format",
		filters=filters,
		fields=["name", "bank", "layout"],
		order_by="bank, name",
	)


@frappe.whitelist()
def preview(bank_file_format, filters, limit=25):
	"""The file as it will be written, before anybody sends it anywhere.

	The lines left out come back too. A file that is quietly shorter than the
	payroll is the thing that goes wrong here, and the answer to "why is my
	file six lines when I paid eight" belongs next to the file, not in another
	report.

	The trailer is built from every sendable line even when the shown lines are
	cut short, so a previewed count and total are the real ones rather than the
	arithmetic of the first page.
	"""
	fmt, filters = _resolve(bank_file_format, filters)
	rows, skipped = _sendable_rows(filters)
	context = _context(fmt, filters, rows)

	limit = cint(limit) or 25
	shown = rows[:limit]

	return {
		"file_name": _render(fmt.file_name_pattern or "{bank} Salary Upload", context) + ".xlsx",
		"bank": fmt.bank,
		"layout": fmt.layout,
		"data": build(fmt, shown, context),
		"included": len(rows),
		"shown": len(shown),
		"total": context["total"],
		"left_out": [
			{"employee_name": r.get("employee_name"), "reason": r.get("cannot_pay"),
			 "amount": flt(r.get("amount"))}
			for r in skipped
		],
	}


@frappe.whitelist()
def download(bank_file_format, filters):
	"""Build the bank's file from the report and hand it back as a download."""
	from frappe.desk.utils import provide_binary_file
	from frappe.utils.xlsxutils import make_xlsx

	fmt, filters = _resolve(bank_file_format, filters)
	rows, skipped = _sendable_rows(filters)
	if not rows:
		frappe.throw(
			_("No line in this run can be sent to {0}. {1}").format(
				frappe.bold(fmt.bank),
				_("{0} were left out for missing details or nothing to pay.").format(len(skipped))
				if skipped
				else "",
			),
			title=_("Nothing To Send"),
		)

	context = _context(fmt, filters, rows)
	data = build(fmt, rows, context)

	provide_binary_file(
		_render(fmt.file_name_pattern or "{bank} Salary Upload", context),
		"xlsx",
		make_xlsx(data, fmt.bank[:31]).getvalue(),
	)


def _resolve(bank_file_format, filters):
	"""The format, and the filters narrowed to what that format may send."""
	fmt = frappe.get_doc("Bank File Format", bank_file_format)
	fmt.check_permission("read")
	frappe.has_permission("Salary Slip", "read", throw=True)

	filters = frappe._dict(frappe.parse_json(filters) or {})
	# The format decides who is in the file, not the filter bar. Downloading a
	# KCB format while the report happens to be showing every bank would put
	# other banks' employees in KCB's file.
	filters.company = fmt.company

	mode = fmt.applies_to_salary_mode
	if mode:
		filters.salary_mode = mode
	# The Bank on the format is the company's - the one being instructed. For an
	# ordinary salary file that is also where the employee holds their account,
	# so it narrows the beneficiaries too. For a mobile wallet it is not: the
	# beneficiary has a phone and often no bank at all, and narrowing by it
	# emptied the file.
	filters.bank = fmt.bank if mode in (None, "", "Bank") else None
	return fmt, filters


def _sendable_rows(filters):
	"""The report's own rows, minus the ones it marked as unsendable."""
	_columns, report_rows = run_bank_remittance(filters)
	lines = [r for r in report_rows if r and not r.get("is_total")]
	sendable = [r for r in lines if not r.get("cannot_pay")]
	return sendable, [r for r in lines if r.get("cannot_pay")]


def _context(fmt, filters, rows):
	"""Values a Constant column may refer to by name."""
	account = frappe._dict()
	if fmt.debit_bank_account:
		account = frappe.db.get_value(
			"Bank Account",
			fmt.debit_bank_account,
			["bank_account_no", "branch_code", "bank"],
			as_dict=True,
		) or frappe._dict()

	period = ""
	if filters.get("to_date"):
		period = formatdate(getdate(filters.get("to_date")), "MMM yyyy")

	return {
		"bank": fmt.bank,
		"company": fmt.company,
		# Read off the company rather than typed into the record. Every bank
		# file here carries a currency column, and a company that pays in
		# anything but its own would otherwise be labelling every line wrongly
		# with no sign of it.
		"currency": frappe.get_cached_value("Company", fmt.company, "default_currency") or "",
		"period": period,
		"from_date": filters.get("from_date") or "",
		"to_date": filters.get("to_date") or "",
		"today": today(),
		# Absa's records date themselves 28052025, with no separators. A bank
		# reading a date it did not ask for rejects the whole file, so the form
		# is chosen in the record rather than left to whatever ISO happens to be.
		"from_date_ddmmyyyy": _ddmmyyyy(filters.get("from_date")),
		"to_date_ddmmyyyy": _ddmmyyyy(filters.get("to_date")),
		"today_ddmmyyyy": _ddmmyyyy(today()),
		"debit_account": account.get("bank_account_no") or "",
		"debit_branch_code": account.get("branch_code") or "",
		"debit_bank_code": frappe.db.get_value("Bank", fmt.bank, "custom_bank_code") or "",
		"swift": frappe.db.get_value("Bank", fmt.bank, "swift_number") or "",
		"count": len(rows),
		"total": flt(sum(flt(r.get("amount")) for r in rows), 2),
	}


def _ddmmyyyy(value):
	return getdate(value).strftime("%d%m%Y") if value else ""


def _render(text, context):
	"""Substitute {name} for a context value, leaving anything unknown alone."""
	out = text or ""
	for key, value in context.items():
		out = out.replace("{" + key + "}", str(value))
	return out


def build(fmt, rows, context):
	"""The workbook as a list of rows, in the order the bank reads them."""
	header = _by_row([c for c in fmt.columns if c.section == "Header"])
	detail = _by_row([c for c in fmt.columns if c.section == "Detail"])
	trailer = _by_row([c for c in fmt.columns if c.section == "Trailer"])

	data = []
	if fmt.title_row:
		data.append([_render(fmt.title_row, context)])
	for group in header:
		data.append([_cell(c, None, context) for c in group])
	# Only a table has headings, and they come off the first line of the detail
	# because that is the line they sit over. A record type file that grew a
	# heading row would be rejected on its first line.
	if fmt.include_header_row and fmt.layout == "Flat Table" and detail:
		data.append([c.column_label or "" for c in detail[0]])
	for row in rows:
		for group in detail:
			data.append([_cell(c, row, context) for c in group])
	for group in trailer:
		data.append([_cell(c, None, context) for c in group])
	return data


def _by_row(columns):
	"""Columns gathered into the lines they belong to, lines in order.

	A section is not always one line. Absa opens with two header records, and
	some banks write two lines per employee; both are the same thing said with
	the Row number.
	"""
	lines = {}
	for column in columns:
		lines.setdefault(cint(column.row) or 1, []).append(column)
	return [lines[key] for key in sorted(lines)]


def _cell(column, row, context):
	source = column.source
	value = (column.value or "").strip()

	if source == "Blank":
		return None
	if source == "Row Count":
		return context["count"]
	if source == "Amount Total":
		return context["total"]
	if source == "Constant":
		return _render(value, context)
	if source == "Composed":
		# Bank code and branch code are held apart because each belongs to a
		# different record, and joined here because a clearing code is how the
		# banks ask for them.
		parts = []
		for fieldname in value.split("+"):
			fieldname = fieldname.strip()
			parts.append(str(_field(fieldname, row, context)))
		return "".join(parts)
	return _field(value, row, context)


def _field(fieldname, row, context):
	"""A report column on a Detail row, or a context value anywhere else."""
	if row is not None and fieldname in row:
		got = row.get(fieldname)
		return "" if got is None else got
	if fieldname in context:
		return context[fieldname]
	return ""
