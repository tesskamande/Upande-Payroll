import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate


class EmployeeTaxHistory(Document):
	"""One employee's tax figures for years this system never ran.

	Gratuity is taxed by adding it to the pay someone already declared that year
	and charging the difference. After a migration those years exist only in the
	old system, so without this every leaver's figures would be typed in by hand
	from paper returns.

	One document per employee, years in the grid - rather than a document per
	employee per year, which for a few thousand staff is a list nobody can use.
	"""

	def validate(self):
		self.validate_years()
		self.validate_amounts()

	def validate_years(self):
		this_year = getdate().year
		seen = {}

		for row in self.prior_years:
			year = int(row.assessment_year or 0)
			# Four digit calendar years. Extracts arrive with "2021/2022" and
			# with 21 in them often enough to be worth catching on the way in
			# rather than months later on somebody's final dues.
			if not 1960 <= year <= this_year + 1:
				frappe.throw(_(
					"Row {0}: Assessment Year should be a four digit calendar "
					"year between 1960 and {1}. Kenya's tax year runs January "
					"to December, so enter 2021 rather than 2021/2022."
				).format(row.idx, this_year + 1))

			if year in seen:
				frappe.throw(_(
					"Rows {0} and {1} are both for {2}. Each year should appear "
					"once - the gratuity calculation reads one figure per year."
				).format(seen[year], row.idx, year))
			seen[year] = row.idx

	def validate_amounts(self):
		suspect = []

		for row in self.prior_years:
			for fieldname in ("annual_taxable_pay", "paye_paid"):
				if flt(row.get(fieldname)) < 0:
					frappe.throw(_("Row {0}: {1} cannot be negative.").format(
						row.idx, _(row.meta.get_label(fieldname))
					))

			if flt(row.paye_paid) > flt(row.annual_taxable_pay):
				suspect.append(str(row.assessment_year))

		# Not fatal - but PAYE above the pay it was charged on almost always
		# means the two columns were swapped in the spreadsheet, and an
		# overstated figure here quietly under-taxes the gratuity instead of
		# failing.
		if suspect:
			frappe.msgprint(_(
				"PAYE paid is more than the annual taxable pay for {0}. That "
				"usually means the two columns are the wrong way round. Worth "
				"checking before this is used for gratuity."
			).format(", ".join(suspect)), indicator="orange",
				title=_("Check these figures"))
