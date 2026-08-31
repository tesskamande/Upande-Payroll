import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, getdate, now


def validate_basic_pay_against_cba(doc, method=None):
	"""Refuse to save an employee below the agreed rate for their Job Category.

	This is what makes applying an agreement a one-off act. Nobody can be
	entered under the scale afterwards, so a new starter does not need the whole
	agreement pushed out again to bring them into line - they are simply entered
	at the rate that already applies.
	"""
	if not doc.job_category:
		return

	minimum = get_cba_minimum(doc.job_category, doc.company)
	if minimum is None:
		return

	if flt(doc.basic_pay) < flt(minimum):
		frappe.throw(_(
			"Basic Pay of {0} is below the agreed rate of {1} for {2}. Enter at "
			"least the agreed rate."
		).format(
			frappe.format_value(flt(doc.basic_pay), {"fieldtype": "Currency"}),
			frappe.format_value(flt(minimum), {"fieldtype": "Currency"}),
			doc.job_category,
		))


def get_cba_minimum(job_category, company=None):
	"""The agreed rate for a Job Category under the agreement in force today.

	Dated on purpose: an agreement signed for next year must not hold up today's
	saves, and the rate that binds is the one currently running. Scoped to the
	employee's company too, so one company's scale never governs another's staff
	on a bench that carries more than one.
	"""
	filters = {"docstatus": 1, "effective_start_date": ("<=", getdate())}
	if company:
		filters["company"] = company

	cba = frappe.db.get_value(
		"CBA", filters, ["name", "applied_on"], as_dict=True,
		order_by="effective_start_date desc, creation desc",
	)
	if not cba:
		return None

	row = frappe.db.get_value(
		"CBA Pay Table",
		{"parent": cba.name, "job_category": job_category},
		["current_basic_pay", "new_basic_pay"],
		as_dict=True,
	)
	if not row:
		return None

	# Which of the two rates binds depends on whether the increase has actually
	# been paid out. Until the agreement is applied, everyone is still on the
	# old rate - holding them to the new one would refuse every save in the gap
	# between signing and applying. Once applied, the new rate is what everyone
	# is on and what a new starter must be entered at.
	if cba.applied_on:
		return flt(row.new_basic_pay) or flt(row.current_basic_pay)
	return flt(row.current_basic_pay)


def pay_rules(cba):
	"""What each Job Category in the pay table is worth, keyed by category."""
	rules = {
		row.job_category: {
			# The rate the agreement moves the category to, and the same figure
			# the Employee form enforces once this is applied. Taking the old
			# rate here left apply writing pay that the form then refused to
			# save - lifted to 9,731 while being held to 10,607.
			"agreed_rate": flt(row.new_basic_pay) or flt(row.current_basic_pay),
			"increase_amount": flt(row.increase_amount),
		}
		for row in cba.table_dqro
		if row.job_category
	}
	if not rules:
		frappe.throw(_("CBA Pay Table is empty. Add job categories before applying."))
	return rules


# Everyone still on the books. Suspended and Inactive staff are covered by the
# agreement exactly as Active ones are - the rate attaches to the job, not to
# whether somebody happens to be at work this month. Filtering to Active alone
# also left them stranded: skipped by apply, yet still held to the new rate by
# the Employee validate hook, so their records could not be saved at all until
# somebody raised them by hand. Left is the one status that ends it.
COVERED_STATUSES = ("Active", "Inactive", "Suspended")


def affected_employees(cba, cba_map):
	"""Everyone this agreement would touch: on the books, in its Company, in one
	of its categories.

	Shared by the preview and by apply, so the count shown before pressing the
	button is the count that gets raised - not a second query that could drift
	from it.
	"""
	return frappe.get_all(
		"Employee",
		filters={
			"status": ["in", COVERED_STATUSES],
			"company": cba.company,
			"job_category": ["in", list(cba_map.keys())],
		},
		fields=["name", "employee_name", "status", "job_category", "basic_pay"],
		order_by="job_category, employee_name",
	)


def new_basic_pay(current, rule):
	"""The increase on top of what they earn, floored at the agreed rate.

	Whichever is higher: a differential earned above the old scale is not
	flattened, and nobody is left under the new one.
	"""
	return round(max(flt(current) + rule["increase_amount"], rule["agreed_rate"]), 2)


@frappe.whitelist()
def preview_cba_impact(cba_name):
	"""Who this agreement would raise, and by how much, before anything is
	written.

	Asked for confirmation without saying how many people it covers, the only
	honest answer is to go and count - so the button counts first.
	"""
	cba = frappe.get_doc("CBA", cba_name)
	# frappe.get_doc does not consult the permission system, and a whitelisted
	# method is reachable by any logged-in user. Without this, the pay scale and
	# everyone's headcount were readable by a portal login. Asking the document
	# rather than naming a role means the Role Permissions Manager governs it.
	cba.check_permission("read")

	cba_map = pay_rules(cba)
	employees = affected_employees(cba, cba_map)

	categories = {}
	for emp in employees:
		rule = cba_map[emp.job_category]
		row = categories.setdefault(emp.job_category, {
			"job_category": emp.job_category,
			"agreed_rate": rule["agreed_rate"],
			"increase_amount": rule["increase_amount"],
			"count": 0,
			"lifted_to_scale": 0,
			"not_active": 0,
		})
		row["count"] += 1
		# Worth separating: these are the ones whose rise is more than the
		# negotiated amount, because they were under the old scale to begin
		# with. A surprise in the payroll total usually traces back to them.
		if flt(emp.basic_pay) + rule["increase_amount"] < rule["agreed_rate"]:
			row["lifted_to_scale"] += 1
		# Counted and raised, but not at work - worth saying so before the
		# button is pressed, rather than leaving it to be noticed in the total.
		if emp.status != "Active":
			row["not_active"] += 1

	# Categories with nobody in them are worth showing too - an empty one is
	# usually a Job Category left unset on the employee records.
	for job_category, rule in cba_map.items():
		categories.setdefault(job_category, {
			"job_category": job_category,
			"agreed_rate": rule["agreed_rate"],
			"increase_amount": rule["increase_amount"],
			"count": 0,
			"lifted_to_scale": 0,
			"not_active": 0,
		})

	return {
		"total": len(employees),
		"not_active": sum(1 for emp in employees if emp.status != "Active"),
		"company": cba.company,
		"applied_on": cba.applied_on,
		"categories": sorted(
			categories.values(), key=lambda r: (-r["count"], r["job_category"])
		),
	}


@frappe.whitelist()
def apply_cba_to_employees(cba_name):
	"""Bulk-apply a submitted CBA's pay table to every active employee in the
	CBA's Company whose Job Category matches a row in it.

	Everyone gets the category's flat Increase Amount on top of their current
	Basic Pay, and nobody ends below the category's New Basic Pay - so someone
	already paid above the old scale keeps that margin, and someone below it
	comes up to scale.

	What someone was on before is not copied onto the Employee record - their
	salary slips already carry it, period by period, and a single field could
	only ever hold the last change.
	"""
	cba = frappe.get_doc("CBA", cba_name)
	# Raising everyone's pay is the most consequential thing this app does, and a
	# whitelisted method gets none of the doctype's permissions for free. Submit
	# rights on the agreement and write rights on Employee, both as configured -
	# no role is named here, so permissions stay where they are administered.
	cba.check_permission("submit")
	frappe.has_permission("Employee", "write", throw=True)

	if cba.docstatus != 1:
		frappe.throw(_("CBA must be submitted before it can be applied."))

	# Lock the agreement's own row before reading applied_on. Two people pressing
	# the button together both used to read it as empty and both went on to
	# apply, so everyone got the increase twice. Whoever gets the lock second
	# waits here, and then sees the timestamp the first one wrote.
	applied_on = frappe.db.get_value("CBA", cba.name, "applied_on", for_update=True)

	# Once only. Every press adds the increase again, and re-applying to pick up
	# a new starter would raise everyone else a second time. The minimum check on
	# the Employee form is what makes this safe: nobody can be entered below the
	# agreed rate, so there is nothing to catch up on later.
	if applied_on:
		frappe.throw(
			_(
				"This agreement was already applied on {0}, and pay has been "
				"raised. Applying it again would add the increase a second time. "
				"A new employee is entered at the agreed rate directly - the "
				"form will not accept less."
			).format(frappe.format_value(applied_on, {"fieldtype": "Datetime"})),
			title=_("Already Applied"),
		)

	cba_map = pay_rules(cba)
	employees = affected_employees(cba, cba_map)

	applied_at = now()
	updated = 0
	for emp in employees:
		previous_pay = flt(emp.basic_pay)
		new_pay = new_basic_pay(previous_pay, cba_map[emp.job_category])

		frappe.db.set_value(
			"Employee",
			emp.name,
			{"basic_pay": new_pay},
			update_modified=False,
		)

		# What the raise actually was, per person, recorded as it happens.
		# Writing pay with update_modified=False leaves no version history, so
		# without this the run is invisible afterwards - and "who went up, from
		# what, by how much" is the first thing anyone asks.
		frappe.get_doc({
			"doctype": "CBA Application Log",
			"cba": cba.name,
			"company": cba.company,
			"employee": emp.name,
			"employee_name": emp.employee_name,
			"job_category": emp.job_category,
			"previous_basic_pay": previous_pay,
			"increase_amount": round(new_pay - previous_pay, 2),
			"new_basic_pay": new_pay,
			"applied_on": applied_at,
			"applied_by": frappe.session.user,
		}).insert(ignore_permissions=True)
		updated += 1

	frappe.db.set_value("CBA", cba.name, "applied_on", applied_at, update_modified=False)
	frappe.db.commit()

	if employees:
		message = f"Updated {updated} employee(s)."
	else:
		message = "No active employees found matching this CBA's job categories."

	return {"success": True, "message": message, "updated": updated}


# ----------------------------------------------------------------------
# Automatic progression between job categories
# ----------------------------------------------------------------------

def category_since(employee):
	"""When this employee entered the category they are in now.

	The date the category was last changed, or their joining date where nothing
	has changed it. That fallback is what makes the rule mean "nine months in
	the job" on a site that has never recorded a promotion - which is every site
	the first time this runs.
	"""
	row = frappe.db.get_value(
		"Employee", employee, ["custom_job_category_since", "date_of_joining"], as_dict=True
	)
	if not row:
		return None
	return getdate(row.custom_job_category_since or row.date_of_joining) \
		if (row.custom_job_category_since or row.date_of_joining) else None


def progression_rules(company, as_on=None):
	"""{from category: (to category, months)} under the agreement in force.

	Read from the CBA rather than from settings because this is a negotiated
	term, not a payroll preference: the agreement that fixes the semi-skilled
	rate is the same one that says when somebody becomes semi-skilled. It also
	means the rule is dated - a later agreement changing nine months to six does
	not rewrite what applied under the last one.
	"""
	filters = {"docstatus": 1, "effective_start_date": ("<=", getdate(as_on or getdate()))}
	if company:
		filters["company"] = company

	cba = frappe.db.get_value(
		"CBA", filters, ["name", "company"], as_dict=True,
		order_by="effective_start_date desc, creation desc",
	)
	if not cba:
		return None, {}

	rules = {}
	for row in frappe.get_all(
		"CBA Pay Table",
		filters={"parent": cba.name, "parenttype": "CBA"},
		fields=["job_category", "promotes_to", "after_months"],
	):
		if row.job_category and row.promotes_to and cint(row.after_months) > 0:
			rules[row.job_category] = (row.promotes_to, cint(row.after_months))

	return cba, rules


@frappe.whitelist()
def run_job_category_progressions(company=None, as_on=None, dry_run=0):
	"""Move everyone who has served long enough into their next category.

	Runs daily, and by hand from the CBA when somebody wants to see what it
	would do first. Idempotent: an employee already in the destination category
	has no rule to match, so a second run the same day moves nobody.

	Pay goes up to the destination category's agreed rate, because it has to -
	validate_basic_pay_against_cba refuses to save anybody below the rate for
	the category they are in, so a promotion that left pay alone could not be
	saved. Anybody already paid above the new rate keeps what they are on.
	"""
	as_on = getdate(as_on or getdate())
	dry_run = cint(dry_run)

	companies = [company] if company else frappe.get_all(
		"CBA", filters={"docstatus": 1}, pluck="company", distinct=True
	)

	moved = []
	for co in [c for c in companies if c]:
		cba, rules = progression_rules(co, as_on)
		if not rules:
			continue

		for emp in frappe.get_all(
			"Employee",
			filters={"company": co, "status": "Active", "job_category": ("in", list(rules))},
			fields=["name", "employee_name", "job_category", "basic_pay"],
		):
			since = category_since(emp.name)
			if not since:
				continue

			to_category, months = rules[emp.job_category]
			if getdate(add_months(since, months)) > as_on:
				continue

			previous_pay = flt(emp.basic_pay)
			minimum = get_cba_minimum(to_category, co)
			new_pay = max(previous_pay, flt(minimum)) if minimum is not None else previous_pay

			moved.append({
				"employee": emp.name, "employee_name": emp.employee_name,
				"from": emp.job_category, "to": to_category,
				"since": str(since), "previous_basic_pay": previous_pay,
				"new_basic_pay": new_pay,
			})
			if dry_run:
				continue

			# update_modified=False for the same reason the rate run uses it -
			# a bulk change should not stamp every employee record as edited -
			# which is also why the log below is the only trace, and not optional.
			frappe.db.set_value("Employee", emp.name, {
				"job_category": to_category,
				"custom_job_category_since": as_on,
				"basic_pay": new_pay,
			}, update_modified=False)

			frappe.get_doc({
				"doctype": "CBA Application Log",
				"cba": cba.name,
				"company": co,
				"employee": emp.name,
				"employee_name": emp.employee_name,
				"event": "Promotion",
				"from_job_category": emp.job_category,
				"job_category": to_category,
				"previous_basic_pay": previous_pay,
				"increase_amount": round(new_pay - previous_pay, 2),
				"new_basic_pay": new_pay,
				"applied_on": now(),
				"applied_by": frappe.session.user,
			}).insert(ignore_permissions=True)

	if not dry_run and moved:
		frappe.db.commit()
	return moved

