import frappe

# Employee fields Payroll Entry can narrow a run by, on top of the branch,
# department, designation and grade HRMS already offers.
#
# Empty on purpose: the Advanced Filters box covers any Employee field, so a
# dedicated one has to earn the space on the form. Add a fieldname here and the
# form field beside it in setup.py if one ever does.
EXTRA_FILTERS = []


class PayrollEntryMixin:
	"""Carry the extra filters into the employee query.

	make_filters() builds the dict HRMS hands to the employee query. It only
	knows about its own fields, so anything added to the form has to be put in
	here or it never reaches the query.
	"""

	def get_salary_slip_details(self, for_withheld_salaries=False):
		"""The rows the bank entry is worked out from, without the employer's
		own contributions.

		make_bank_entry() adds up every earning on the slips and takes off every
		deduction, and an employer contribution is carried as a deduction so it
		gets taken off too - money the employee never bore, subtracted from what
		the bank pays them. A run of four came to 161,523.95 against a net pay of
		179,373.95, short by exactly the employer NSSF and Housing Levy.

		Filtered here rather than by flagging the components, because the two
		flags that would exclude them from this also exclude them from the
		accrual (payroll_entry.py:394), where they belong - expense debited,
		liability to the fund credited. This is the only caller of this method,
		so nothing else is affected.
		"""
		rows = super().get_salary_slip_details(for_withheld_salaries)
		employer_side = employer_contribution_components()
		if not employer_side:
			return rows
		return [row for row in rows if row.salary_component not in employer_side]

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
			scoped = _combine_same_field(advanced)
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


def employer_contribution_components():
	"""Components the employer pays on top of wages, rather than out of them."""
	return {
		row.name
		for row in frappe.get_all(
			"Salary Component",
			filters={"custom_is_employer_contribution": 1},
			fields=["name"],
		)
	}


def _combine_same_field(conditions):
	"""Turn repeated equals on one field into a single "any of these".

	Conditions are ANDed, which is right across different fields - grade AND
	department narrows, as it should. On the SAME field it is never what anyone
	means: two rows reading Employment Type equals Contract and Employment Type
	equals Apprentice asks for somebody who is both, and matches nobody. Read as
	"either", which is what was intended, it returns both groups.

	Only plain equals is folded. like, between, greater than and the rest keep
	their AND, because combining those changes what they ask rather than
	clarifying it.
	"""
	equals, others, order = {}, [], []

	for condition in conditions:
		fieldname, operator, value = condition[0], condition[1], condition[2]
		if str(operator).strip().lower() not in ("=", "equals"):
			others.append(condition)
			continue
		if fieldname not in equals:
			equals[fieldname] = []
			order.append(fieldname)
		if value not in equals[fieldname]:
			equals[fieldname].append(value)

	combined = []
	for fieldname in order:
		values = equals[fieldname]
		combined.append(
			[fieldname, "=", values[0]] if len(values) == 1
			else [fieldname, "in", values]
		)

	return combined + others
