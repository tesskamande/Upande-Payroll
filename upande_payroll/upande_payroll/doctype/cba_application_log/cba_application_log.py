# Copyright (c) 2026, Teresia and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CBAApplicationLog(Document):
	"""One employee's pay change from one CBA application.

	Written by ``apply_cba_to_employees`` and never by hand - the doctype is
	in_create with no write permission, so what it says is what the run did.
	Applying pay with update_modified=False leaves no version history on the
	Employee, and this is the record that takes its place.
	"""

	pass
