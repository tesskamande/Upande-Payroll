import frappe
from frappe.utils import add_days, add_to_date, flt, getdate


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
		fields=["fraction_of_applicable_earnings"],
		order_by="idx",
	)
	if not rule_slabs:
		frappe.throw(f"No slabs found in Gratuity Rule: {doc.gratuity_rule}")

	fraction = flt(rule_slabs[0].fraction_of_applicable_earnings)
	gratuity_per_year = total_component_amount * fraction
	doc.amount = flt(gratuity_per_year * years, 2)

	# Tax-exemption phasing per completed year of service
	exemption_date = settings.gratuity_tax_exemption_date
	recent_years = settings.gratuity_paye_recent_years or 4

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

		if not exemption_date or ann_end <= exemption_date:
			year_taxable, year_exempt = gratuity_per_year, 0.0
			year_taxable_days, year_exempt_days = total_days, 0
		elif ann_start >= exemption_date:
			year_taxable, year_exempt = 0.0, gratuity_per_year
			year_taxable_days, year_exempt_days = 0, total_days
			has_exemption = True
		else:
			year_taxable_days = (exemption_date - ann_start).days
			year_exempt_days = (ann_end - exemption_date).days
			year_taxable = flt(gratuity_per_year * year_taxable_days / total_days, 2)
			year_exempt = flt(gratuity_per_year - year_taxable, 2)
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


def _calculate_tax_on_rows(doc):
	"""Compute PAYE due on each Gratuity Tax Computation row where Annual
	Taxable Pay has been filled in (manually, from that year's actual filing),
	using the Income Tax Slab in effect for that assessment year."""
	for row in doc.custom_gratuity_tax_computation:
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
		revised = flt(row.annual_taxable_pay) + flt(row.gratuity_portion)
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
