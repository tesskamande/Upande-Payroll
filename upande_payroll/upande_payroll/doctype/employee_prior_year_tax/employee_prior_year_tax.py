from frappe.model.document import Document


class EmployeePriorYearTax(Document):
	"""One year's carried-over tax figures. Validated on the parent, where the
	rows can be checked against each other for duplicate years."""

	pass
