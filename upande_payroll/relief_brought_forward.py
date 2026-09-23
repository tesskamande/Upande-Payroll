# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

"""Bulk-set Personal Relief Brought Forward on a payroll run's Draft slips.

kenya_statutory_calculator.py's Carry Forward relief chains each slip from
the one before it - but a client migrating onto this app mid-year has no
prior slip here to chain from at all, only real payroll history that ran
somewhere else entirely. Their first run here has to be seeded by hand with
what each employee actually carried in from that other system. One at a
time on each Draft slip works for a handful of people; this is for the rest
of them, from a spreadsheet.
"""

import frappe
from frappe import _
from frappe.utils import flt


@frappe.whitelist()
def download_template(payroll_entry):
	"""One row per Draft slip on this run: employee, name, and whatever
	Brought Forward figure is already on the slip (0 the first time)."""
	from frappe.desk.utils import provide_binary_file
	from frappe.utils.xlsxutils import make_xlsx

	doc = frappe.get_doc("Payroll Entry", payroll_entry)
	doc.check_permission("read")

	slips = frappe.get_all(
		"Salary Slip",
		filters={"payroll_entry": payroll_entry, "docstatus": 0},
		fields=["employee", "employee_name", "custom_personal_relief_brought_forward"],
		order_by="employee_name",
	)

	rows = [["Employee", "Employee Name", "Personal Relief Brought Forward"]]
	for s in slips:
		rows.append([s.employee, s.employee_name, flt(s.custom_personal_relief_brought_forward)])

	provide_binary_file(
		"{0} - Relief Brought Forward".format(payroll_entry),
		"xlsx",
		make_xlsx(rows, "Relief").getvalue(),
	)


@frappe.whitelist()
def import_relief_brought_forward(payroll_entry, file_url):
	"""Set Personal Relief Brought Forward on each Draft slip in this run
	from a sheet of Employee -> amount, then re-validate each one so PAYE
	picks the new figure up immediately rather than waiting for a resave
	nobody remembers to trigger."""
	doc = frappe.get_doc("Payroll Entry", payroll_entry)
	doc.check_permission("write")
	frappe.has_permission("Salary Slip", "write", throw=True)

	table = _read_sheet(file_url)
	if not table:
		frappe.throw(_("That file has no rows."))

	header = [str(cell or "").strip().lower() for cell in table[0]]
	try:
		employee_col = header.index("employee")
	except ValueError:
		frappe.throw(_("The sheet needs an Employee column."))

	amount_col = next(
		(i for i, h in enumerate(header) if "relief" in h or "brought forward" in h),
		None,
	)
	if amount_col is None:
		frappe.throw(_("The sheet needs a Personal Relief Brought Forward column."))

	slips_by_employee = {
		s.employee: s.name
		for s in frappe.get_all(
			"Salary Slip",
			filters={"payroll_entry": payroll_entry, "docstatus": 0},
			fields=["name", "employee"],
		)
	}

	updated, unmatched = 0, []
	for number, line in enumerate(table[1:], start=2):
		if not line or not any(str(cell or "").strip() for cell in line):
			continue

		employee = str(line[employee_col] or "").strip() if employee_col < len(line) else ""
		if not employee:
			continue

		slip_name = slips_by_employee.get(employee)
		if not slip_name:
			unmatched.append(
				_("row {0}: no Draft slip for {1} on this run").format(number, employee)
			)
			continue

		amount = line[amount_col] if amount_col < len(line) else 0
		slip = frappe.get_doc("Salary Slip", slip_name)
		slip.custom_personal_relief_brought_forward = flt(amount)
		slip.save()
		updated += 1

	return {
		"updated": updated,
		"unmatched": unmatched[:20],
		"unmatched_count": len(unmatched),
	}


def _read_sheet(file_url):
	"""Rows from an uploaded xlsx or csv, as a list of lists. Same reading as
	Bulk Loan Closure's own import - proven working, not reinvented here."""
	from frappe.utils.csvutils import read_csv_content
	from frappe.utils.xlsxutils import read_xlsx_file_from_attached_file

	file_doc = frappe.get_doc("File", {"file_url": file_url})
	content = file_doc.get_content()

	if (file_doc.file_name or "").lower().endswith((".xlsx", ".xlsm")):
		return read_xlsx_file_from_attached_file(fcontent=content)
	if isinstance(content, bytes):
		content = content.decode("utf-8", errors="replace")
	return read_csv_content(content)
