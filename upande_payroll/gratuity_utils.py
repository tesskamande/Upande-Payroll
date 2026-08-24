import frappe
from frappe import _
from frappe.utils import cint, add_days, add_to_date, flt, getdate


def calculate_gratuity(doc, method=None):
	"""Compute gratuity amount, tax-exemption phasing, and PAYE spreading for a
	Gratuity document, driven by Company Payroll Settings instead of hardcoded
	values.

	- Total gratuity = (applicable earning components from the latest Salary
	  Slip) x (Gratuity Rule Slab fraction) x (completed years of service).
	- Gratuity accrued on/after Gratuity Tax Exemption Date is tax-exempt;
	  years straddling that date are prorated by day.
	- The taxable portion is spread back over the most recent N assessment
	  years (Gratuity PAYE Recent Years), with anything older bucketed into
	  one row, per KRA's gratuity PAYE-spreading practice.
	"""
	settings = frappe.get_cached_doc("Company Payroll Settings", doc.company)
	if not settings.enable_gratuity_calculation:
		return

	if doc.pay_via_salary_slip and doc.custom_pay_via_terminal_dues:
		frappe.throw("Pay via Salary Slip and Pay via Terminal Dues Settlement are mutually exclusive - choose one mode of payment.")

	if settings.gratuity_salary_component and not doc.salary_component:
		doc.salary_component = settings.gratuity_salary_component

	employee = frappe.get_doc("Employee", doc.employee)
	if not employee.date_of_joining:
		frappe.throw(f"No Date of Joining found for employee: {doc.employee}")
	if not employee.relieving_date:
		frappe.throw(f"Please set Relieving Date for employee: {doc.employee}")

	doj = getdate(employee.date_of_joining)
	rd = getdate(employee.relieving_date)

	# Completed years of service (anniversary method)
	years = rd.year - doj.year
	if (rd.month, rd.day) < (doj.month, doj.day):
		years -= 1

	gratuity_rule = frappe.get_doc("Gratuity Rule", doc.gratuity_rule)
	minimum_years = gratuity_rule.minimum_year_for_gratuity or 5
	if years < minimum_years:
		frappe.throw(
			f"Employee {doc.employee} has only {years} completed year(s). "
			f"Minimum {minimum_years} years required for gratuity."
		)
	doc.current_work_experience = years

	# Applicable earning total, from the latest submitted Salary Slip
	salary_slip_name = frappe.db.get_value(
		"Salary Slip",
		{"employee": doc.employee, "docstatus": 1},
		"name",
		order_by="start_date desc",
	)
	if not salary_slip_name:
		frappe.throw(f"No submitted salary slip found for employee: {doc.employee}")

	salary_slip = frappe.get_doc("Salary Slip", salary_slip_name)
	applicable_components = frappe.get_all(
		"Gratuity Applicable Component",
		filters={"parent": doc.gratuity_rule},
		pluck="salary_component",
	)
	if not applicable_components:
		frappe.throw(f"No applicable earning components found in Gratuity Rule: {doc.gratuity_rule}")

	total_component_amount = sum(
		flt(row.amount) for row in salary_slip.earnings if row.salary_component in applicable_components
	)
	if not total_component_amount:
		frappe.throw(
			f"No applicable earning component found in last salary slip. "
			f"Please check Gratuity Rule: {doc.gratuity_rule}"
		)

	rule_slabs = frappe.get_all(
		"Gratuity Rule Slab",
		filters={"parent": doc.gratuity_rule},
		fields=["from_year", "to_year", "fraction_of_applicable_earnings"],
		order_by="idx",
	)
	if not rule_slabs:
		frappe.throw(f"No slabs found in Gratuity Rule: {doc.gratuity_rule}")

	# One fraction per year of service, not one for the whole run. A rule tiered
	# as 15/26 for years 1-10 and 20/26 after was quietly paying 15/26 on every
	# year, because only the first slab was ever read - the rest were fetched
	# and thrown away.
	year_amounts = [
		total_component_amount * _slab_fraction(rule_slabs, yr)
		for yr in range(1, years + 1)
	]
	doc.amount = flt(sum(year_amounts), 2)

	# Tax-exemption phasing per completed year of service
	exemption_date = settings.gratuity_tax_exemption_date
	recent_years = cint(settings.gratuity_paye_recent_years)
	if recent_years <= 0:
		frappe.throw(
			_("Set Gratuity PAYE Recent Years in Company Payroll Settings for {0}.")
				.format(doc.company)
		)

	has_exemption = False
	taxable_total = 0.0
	exemption_rows = []

	for yr in range(1, years + 1):
		# Real date arithmetic, not a rebuilt string: someone who joined on 29
		# February has no anniversary in a common year, and "2027-02-29" is not
		# a date - it threw rather than calculating. add_to_date lands on the
		# 28th in those years, which is how an anniversary is normally read.
		ann_start = getdate(add_to_date(doj, years=yr - 1))
		ann_end = getdate(add_to_date(doj, years=yr))
		total_days = (ann_end - ann_start).days
		amount_this_year = year_amounts[yr - 1]

		if not exemption_date or ann_end <= exemption_date:
			year_taxable, year_exempt = amount_this_year, 0.0
			year_taxable_days, year_exempt_days = total_days, 0
		elif ann_start >= exemption_date:
			year_taxable, year_exempt = 0.0, amount_this_year
			year_taxable_days, year_exempt_days = 0, total_days
			has_exemption = True
		else:
			year_taxable_days = (exemption_date - ann_start).days
			year_exempt_days = (ann_end - exemption_date).days
			year_taxable = flt(amount_this_year * year_taxable_days / total_days, 2)
			year_exempt = flt(amount_this_year - year_taxable, 2)
			has_exemption = True

		taxable_total += year_taxable
		exemption_rows.append({
			"ann_start": ann_start,
			"ann_end": ann_end,
			"total_days": total_days,
			"taxable_days": year_taxable_days,
			"exempt_days": year_exempt_days,
		})

	taxable_total = flt(taxable_total, 2)
	taxable_per_year = flt(taxable_total / years, 2) if years else 0.0

	# Spreading must start from the last TAXABLE year, not necessarily the
	# relieving year - gratuity earned after the exemption date isn't taxable
	# at all, so there is nothing to spread into those years.
	last_taxable_date = rd
	if exemption_date and rd >= exemption_date:
		last_taxable_date = add_days(exemption_date, -1)
	last_year = last_taxable_date.year

	bucket_year = last_year - recent_years
	bucket_count = years - recent_years

	doc.set("custom_gratuity_exemption_breakdown", [])

	if has_exemption:
		for i in range(recent_years):
			completed_yr = years - i
			if completed_yr < 1:
				continue
			row = exemption_rows[completed_yr - 1]
			doc.append("custom_gratuity_exemption_breakdown", {
				"gratuity_year": last_year - i,
				"service_period": (
					f"{row['ann_start'].strftime('%d/%m/%Y')} - {row['ann_end'].strftime('%d/%m/%Y')}"
				),
				"total_days": row["total_days"],
				"taxable_days": row["taxable_days"],
				"tax_exempt_days": row["exempt_days"],
			})

		if bucket_count > 0:
			# add_to_date, not a rebuilt date string: someone who joined on 29
			# February has no anniversary in a common year, and "2027-02-29" is
			# not a date - it threw rather than calculating.
			bucket_ann_end = getdate(add_to_date(doj, years=years - recent_years))
			doc.append("custom_gratuity_exemption_breakdown", {
				"gratuity_year": bucket_year,
				"service_period": f"{doj.strftime('%d/%m/%Y')} - {bucket_ann_end.strftime('%d/%m/%Y')}",
				"total_days": (bucket_ann_end - doj).days,
				"taxable_days": (bucket_ann_end - doj).days,
				"tax_exempt_days": 0,
			})

	if not doc.custom_gratuity_tax_computation:
		for i in range(recent_years):
			assessment_year = last_year - i
			doc.append("custom_gratuity_tax_computation", {
				"gratuity_year": assessment_year,
				"gratuity_portion": taxable_per_year,
			})

		if bucket_count > 0:
			doc.append("custom_gratuity_tax_computation", {
				"gratuity_year": bucket_year,
				"gratuity_portion": flt(taxable_per_year * bucket_count, 2),
			})

	_calculate_tax_on_rows(doc)


def _slab_fraction(slabs, year):
	"""The fraction that applies to one completed year of service.

	from_year is exclusive and to_year inclusive, which is how ERPNext writes
	these: 0-10 then 10-0 means years 1 to 10, then 11 onwards. A to_year of 0
	is open-ended, so a single 0/0 slab covers everything.
	"""
	for slab in slabs:
		start = flt(slab.from_year)
		end = flt(slab.to_year)
		if year > start and (not end or year <= end):
			return flt(slab.fraction_of_applicable_earnings)
	return flt(slabs[-1].fraction_of_applicable_earnings)


def _public_scheme_exemption(doc):
	"""How much of each year's gratuity is exempt because it is paid under a
	public pension scheme.

	Nil unless the employee is flagged for it on their Employee record - scheme
	membership is a fact about the person, not about one payment - and the
	allowance itself comes from Kenya Payroll Settings rather than being written
	into the code, so a change to the KRA figure is a setting, not a release.
	"""
	if not frappe.db.get_value("Employee", doc.employee, "paid_under_public_pension_scheme"):
		return 0.0

	exemption = flt(frappe.db.get_single_value(
		"Kenya Payroll Settings", "gratuity_public_scheme_annual_exemption"
	))
	if exemption <= 0:
		# Nil here would tax the whole gratuity without saying why, on the one
		# employee the allowance was meant for.
		frappe.throw(
			_("{0} is in a public pension scheme, but no gratuity exemption is set "
			  "in Kenya Payroll Settings.").format(frappe.bold(doc.employee_name or doc.employee))
		)

	return exemption


MISMATCH_TOLERANCE = 0.01


def _figures_from_payslips(employee, company, year):
	"""That year's figures worked out from this system's own payslips.

	Also reports whether payroll was actually run here for the whole of that
	year, which is what decides whether these figures or a carried-over record
	should be believed.
	"""
	slips = frappe.db.get_all(
		"Salary Slip",
		filters={
			"docstatus": 1, "employee": employee, "company": company,
			"end_date": ("between", [f"{year}-01-01", f"{year}-12-31"]),
		},
		fields=["name", "gross_pay", "start_date", "end_date",
				"custom_tax_charged", "custom_personal_relief_utilized"],
		order_by="start_date asc",
	)
	if not slips:
		return None

	# Both figures come off the payslips themselves rather than being worked out
	# again here. The Taxable Income row IS the pay that month's PAYE was
	# charged on, and the PAYE row is what was actually deducted - so the
	# gratuity, the payslip and the P9 all quote one number instead of three
	# calculations that can drift apart.
	#
	# Read from the components rather than the P9's column mapping on purpose:
	# the P9 takes its figures from whatever a site has tagged, and on a site
	# that has not done that tagging it reports nil while chargeable pay still
	# looks right. A believable pay figure beside a nil PAYE understates what
	# was already paid, and so overcharges the gratuity, with nothing to show
	# for it.
	from upande_payroll.kenya_statutory_calculator import (
		TAXABLE_INCOME_COMPONENT, get_statutory_components,
	)

	names = [slip.name for slip in slips]

	def component_total(component):
		return sum(flt(row.amount) for row in frappe.db.get_all(
			"Salary Detail",
			filters={"parent": ("in", names), "parentfield": "deductions",
					 "salary_component": component, "docstatus": 1},
			fields=["amount"],
		))

	taxable = component_total(TAXABLE_INCOME_COMPONENT)
	paye = component_total(get_statutory_components().paye)

	if not taxable:
		# Slips run before the Taxable Income component existed do not carry it,
		# so fall back to the P9's own month builder. Not a second opinion on
		# what chargeable pay means - the same code the card is built from.
		from upande_payroll.upande_payroll.report.kenya_p9_card_report.kenya_p9_card_report import (
			_month, _year_total,
		)

		relief = flt(frappe.db.get_single_value(
			"Kenya Payroll Settings", "monthly_personal_relief"
		))
		totals = _year_total([_month(frappe._dict(slip), relief) for slip in slips])
		taxable = flt(totals.get("chargeable_pay"))

	# The year is covered from its start, or from the day the person joined if
	# that is later. A company that went live mid-year has payslips for that
	# year but only for part of it, and half a year's figures are worse than the
	# full-year total the old system filed.
	doj = frappe.db.get_value("Employee", employee, "date_of_joining")
	starts_from = getdate(f"{year}-01-01")
	if doj and getdate(doj) > starts_from:
		starts_from = getdate(doj)

	return {
		"taxable": flt(taxable),
		"paye": paye,
		"covers_whole_year": getdate(slips[0].start_date) <= starts_from,
	}


def _annual_figures(employee, company, year):
	"""That year's chargeable pay and PAYE paid, from whichever source is the
	better authority.

	Payroll actually run in this system wins for the years it covers in full -
	those are the figures the P9 was filed from. Everything earlier comes from
	the carried-over records, which after a migration exist nowhere else. A year
	this system only partly ran falls back to the carried-over full-year figure,
	and only uses its own part-year total when there is nothing to fall back on.

	Returns (None, None) when neither source has it, leaving the row blank for
	someone to fill in by hand.
	"""
	# Employee Tax History is named after the employee, so the document name is
	# the employee id and the child rows can be read without loading the parent.
	imported = frappe.db.get_value(
		"Employee Prior Year Tax",
		{"parenttype": "Employee Tax History", "parent": employee,
		 "assessment_year": year},
		["annual_taxable_pay", "paye_paid"],
		as_dict=True,
	)
	payroll = _figures_from_payslips(employee, company, year)

	if payroll and payroll["covers_whole_year"]:
		if imported:
			_warn_on_disagreement(employee, year, imported, payroll)
		return payroll["taxable"], payroll["paye"]

	if imported:
		return flt(imported.annual_taxable_pay), flt(imported.paye_paid)

	if payroll:
		return payroll["taxable"], payroll["paye"]

	return None, None


def _warn_on_disagreement(employee, year, imported, payroll):
	"""Say so when both sources have a year and they do not match.

	This is the transition year: payroll ran here and the same year was also
	imported from the old system. The payslips are used, but a gap between the
	two usually means the import covered a year it should not have, or that the
	go-live cutover lost something - either way it is worth someone looking
	before the figures reach a leaver's tax computation.
	"""
	gaps = []
	for label, was, now in (
		("Annual Taxable Pay", flt(imported.annual_taxable_pay), payroll["taxable"]),
		("PAYE Already Paid", flt(imported.paye_paid), payroll["paye"]),
	):
		if abs(was - now) > MISMATCH_TOLERANCE:
			gaps.append(f"{label}: imported {was:,.2f} against payroll {now:,.2f}")

	if not gaps:
		return

	frappe.msgprint(
		_("{0} is in Employee Tax History for {1}, but payroll was also run "
		  "here for the whole of that year. The payroll figures have been used. "
		  "{2}").format(year, employee, " | ".join(gaps)),
		indicator="orange", title=_("Two sources for {0}").format(year),
	)


def _fill_prior_year_figures(doc):
	"""Fill in each row's prior-year figures, leaving anything already entered
	alone - a payroll officer who typed a figure had a reason, and a re-import
	should not quietly overwrite them."""
	for row in doc.custom_gratuity_tax_computation:
		if not row.gratuity_year:
			continue
		if flt(row.annual_taxable_pay) or flt(row.paye_already_paid):
			continue

		taxable, paye = _annual_figures(doc.employee, doc.company, row.gratuity_year)
		if taxable is None:
			continue
		row.annual_taxable_pay = taxable
		row.paye_already_paid = paye


def _calculate_tax_on_rows(doc):
	"""Compute PAYE due on each Gratuity Tax Computation row where Annual
	Taxable Pay is known - carried over from the previous system, worked out
	from this one's payslips, or typed in - using the Income Tax Slab in effect
	for that assessment year."""
	_fill_prior_year_figures(doc)
	allowance = _public_scheme_exemption(doc)

	for row in doc.custom_gratuity_tax_computation:
		# One allowance per row, the oldest row included. That row stands for
		# every year before the spread window, so it covers several years of
		# service on a single allowance - agreed deliberately rather than
		# multiplying it by the years behind it.
		portion = flt(row.gratuity_portion)
		row.exempt_amount = min(portion, allowance) if allowance else 0.0
		taxable_portion = max(0.0, portion - flt(row.exempt_amount))

		if not row.gratuity_year or not flt(row.annual_taxable_pay):
			continue

		lookup_date = f"{row.gratuity_year}-12-31"
		slab_name = frappe.db.get_value(
			"Income Tax Slab",
			{"company": doc.company, "effective_from": ("<=", lookup_date), "disabled": 0},
			"name",
			order_by="effective_from desc",
		)
		if not slab_name:
			frappe.msgprint(
				f"No Income Tax Slab found for year {row.gratuity_year} "
				f"effective on or before {lookup_date}."
			)
			continue

		slab = frappe.get_doc("Income Tax Slab", slab_name)
		relief = flt(slab.standard_tax_exemption_amount)
		revised = flt(row.annual_taxable_pay) + taxable_portion
		paye_paid = flt(row.paye_already_paid)

		tax = 0.0
		for band in slab.slabs:
			from_amt = flt(band.from_amount)
			to_amt = flt(band.to_amount)
			rate = flt(band.percent_deduction) / 100

			if revised <= from_amt:
				continue
			if to_amt == 0:
				tax += (revised - from_amt) * rate
			else:
				tax += (min(revised, to_amt) - from_amt) * rate

		row.revised_taxable_income = revised
		row.tax_on_revised_income = tax
		row.personal_relief = relief
		row.tax_on_gratuity = max(0.0, tax - relief - paye_paid)

	doc.custom_paye = flt(
		sum(flt(row.tax_on_gratuity) for row in doc.custom_gratuity_tax_computation), 2
	)
