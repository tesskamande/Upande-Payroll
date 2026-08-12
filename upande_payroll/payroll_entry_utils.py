import frappe

# Employee fields Payroll Entry can narrow a run by, on top of the branch,
# department, designation and grade HRMS already offers.
#
# Employment Type is here because companies split payroll along it: Kaitet runs
# contract and permanent staff separately, where the same person's grade and
# designation say nothing about which run they belong to.
EXTRA_FILTERS = ["employment_type"]


class PayrollEntryMixin:
	"""Carry the extra filters into the employee query.

	make_filters() builds the dict HRMS hands to the employee query. It only
	knows about its own fields, so anything added to the form has to be put in
	here or it never reaches the query.
	"""

	def make_filters(self):
		filters = super().make_filters()
		for fieldname in EXTRA_FILTERS:
			value = self.get(fieldname)
			if value:
				filters[fieldname] = value

		# Whatever was built in the Advanced Filters box, carried as JSON on the
		# form. Get Employees posts the whole document, so it arrives here
		# without needing to intercept the button.
		advanced = self.get("advanced_employee_filters")
		if advanced:
			try:
				parsed = frappe.parse_json(advanced) or []
			except Exception:
				parsed = []
			if parsed:
				filters["advanced_employee_filters"] = parsed

		return filters


def patch_filter_conditions():
	"""Teach HRMS's employee query to apply the extra filters.

	set_filter_conditions() walks a hardcoded list of fieldnames, so a filter
	the form collects and make_filters() passes along is still dropped on the
	floor - the run would quietly include everybody, which is the worst way for
	a payroll filter to fail. Wrapping the function is the smallest change that
	leaves the rest of HRMS's query building alone.

	Guarded so repeated imports do not wrap the wrapper, and so an HRMS release
	that moves or renames this function leaves the filters visibly not working
	rather than breaking payroll outright.
	"""
	try:
		from hrms.payroll.doctype.payroll_entry import payroll_entry as core
	except ImportError:
		return

	original = getattr(core, "set_filter_conditions", None)
	if original is None or getattr(original, "_upande_wrapped", False):
		return

	def wrapped(query, filters, qb_object):
		query = original(query, filters, qb_object)
		for fieldname in EXTRA_FILTERS:
			if filters.get(fieldname):
				query = query.where(qb_object[fieldname] == filters[fieldname])

		advanced = filters.get("advanced_employee_filters")
		if advanced:
			# Resolved through Frappe's own filter engine rather than translated
			# into query-builder conditions here. The box can produce like, in,
			# between, is set and the rest, and a hand-written translation of
			# those is where a payroll filter starts quietly matching the wrong
			# people.
			scoped = list(advanced)
			if filters.get("company"):
				scoped.append(["company", "=", filters["company"]])

			allowed = frappe.get_all("Employee", filters=scoped, pluck="name")
			# An empty IN list is not valid SQL, and "nothing matched" has to
			# mean nobody - never everybody.
			query = query.where(qb_object.name.isin(allowed or [""]))

		return query

	wrapped._upande_wrapped = True
	core.set_filter_conditions = wrapped


patch_filter_conditions()
