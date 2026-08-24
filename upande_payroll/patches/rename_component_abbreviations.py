import frappe

# Old abbreviation -> new, keyed by component name. Existing components are never
# touched by ensure_statutory_components(), which only creates what is missing,
# so a site already running keeps whatever it was given on the day it installed.
RENAMES = {
	"Employee NSSF Tier 1": "NSSFTIER1",
	"Employee NSSF Tier 2": "NSSFTIER2",
	"Employer NSSF Tier 1": "NSSFTIER1E",
	"Employer NSSF Tier 2": "NSSFTIER2E",
	"Pay As You Earn": "PAYE",
}


def execute():
	for component, abbr in RENAMES.items():
		if not frappe.db.exists("Salary Component", component):
			continue

		current = frappe.db.get_value("Salary Component", component, "salary_component_abbr")
		if current == abbr:
			continue

		# Abbreviations have to stay unique - ERPNext ships an Income Tax component
		# with the abbreviation IT, which is what pushed our Pay As You Earn to IT_1
		# on a fresh install. Skip rather than collide.
		taken = frappe.db.exists(
			"Salary Component", {"salary_component_abbr": abbr, "name": ("!=", component)}
		)
		if taken:
			frappe.logger().warning(
				f"upande_payroll: {abbr} already used by {taken}, left {component} as {current}"
			)
			continue

		frappe.db.set_value("Salary Component", component, "salary_component_abbr", abbr)

	frappe.db.commit()
