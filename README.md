### Upande Payroll Customizations

Config-driven Kenyan payroll on top of ERPNext/HRMS. Every rule is opt-in through
`Company Payroll Settings` - a company with no settings record runs on stock
HRMS behaviour untouched; every feature below only applies once that company
has switched it on.

What it adds:

- **Kenyan statutory calculations** - PAYE, NSSF, SHIF, Affordable Housing
  Levy, personal relief, and the statutory reports (P9, P10, NSSF, SHIF,
  Bank Remittance, Company Register, Leave Liability, HELB) that go with them.
- **Payroll journal control** - rewrites the accrual Journal Entry per company
  policy (gross pay account method, cost centre split, dimension tagging);
  leaves the journal alone entirely when `Gross Pay Account Method` is blank,
  so another script can own it instead.
- **Bank files** - `Bank File Format` lets a company define its own bank's
  exact upload layout (KCB, Equity, ...) as data, downloaded straight from the
  Bank Remittance report.
- **Salary Review** - a reasoned, dated audit trail for basic pay changes,
  independent of Salary Structure Assignment.
- **Employee Salary Advance** - simple per-annum interest, spread evenly,
  independent of the `lending` app.
- **Overtime, leave encashment, gratuity, terminal dues, deduction caps** -
  each driven by its own Company Payroll Settings section rather than a
  hardcoded default.

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch UpandePayroll
bench install-app upande_payroll
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/upande_payroll
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit
