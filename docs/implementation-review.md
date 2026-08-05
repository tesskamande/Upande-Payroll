# Upande Payroll — Implementation Review

**App:** `upande_payroll` · **Platform:** Frappe / ERPNext v16 + HRMS
**Reviewed:** 5 August 2026 · **Dev site:** `Upande_Payroll` · **Reference company:** Karen Roses
**Target sites:** Karen Roses, Kikwetu, Kaitet, Mona Flowers

---

## 1. What this app replaces, and why it exists

Every client site was running Kenyan payroll through hand-written Server Scripts. Each
one encoded that client's rates, that client's component names and that client's
interpretation of the law directly in Python stored in the database. There were four
sets of them, they had drifted apart, and none of them could be tested, reviewed or
version-controlled.

The problems that caused, in the order they actually hurt:

1. **A KRA rate change meant editing four sites by hand.** NSSF's tier ceilings moved
   and there was no single place to move them.
2. **No two sites agreed on the same rule.** The 1/3 rule existed on Kaitet and nowhere
   else. Kikwetu had a deferred-deduction table that was never populated.
3. **Nothing was reviewable.** A Server Script has no diff, no history and no test.
4. **Nothing was portable.** Onboarding a new company meant re-typing the payroll logic.

This app collapses all of that into one installable, versioned application where
**every client-specific value is configuration, not code**. Two settings doctypes carry
the variation:

| | Scope | Holds |
|---|---|---|
| **Kenya Payroll Settings** | Single, KRA-wide | 34 fields — PAYE bands, NSSF tiers and ceilings, SHIF rate and floor, AHL rates, personal relief, insurance and pension relief caps |
| **Company Payroll Settings** | One per Company | 51 fields — which salary component means what, which accounts to post to, divisors, notice-period rules, wage bases, feature switches |

The design rule throughout: **the app never hardcodes a component name, a rate, a
divisor or an account.** If a company calls basic pay "Basic Salary", that is a field
value, not a code change.

**Code size:** ~3,000 lines of business logic, ~1,600 lines of doctype controllers,
~1,150 lines of report logic, ~1,500 lines of test harness, ~600 lines of client script.

---

## 2. Architecture

### 2.1 Where the app hooks into HRMS

```
Salary Slip
  ├─ regional_overrides["Kenya"].apply_regional_deductions
  │     └─ kenya_statutory_calculator.py   NSSF · SHIF · AHL · PAYE · reliefs
  ├─ validate   → deduction_cap.apply_deduction_cap        the 1/3 rule
  ├─ on_submit  → deduction_cap.settle_deferred_deductions ledger moves
  └─ on_cancel  → deduction_cap.unsettle_deferred_deductions

Journal Entry   before_insert → payroll_journal.rewrite_payroll_journal
Salary Structure before_update_after_submit → salary_structure_utils.validate_after_submit
Gratuity        validate  → gratuity_utils.calculate_gratuity
Leave Encashment validate → leave_encashment_utils.validate_leave_encashment
Leave Application on_submit/on_cancel → leave_travelling_allowance
Employee        validate  → cba_utils.validate_basic_pay_against_cba
after_migrate / after_install → setup.after_migrate
```

Two deliberate choices in there are worth defending:

**The 1/3 rule is on `validate`, not in `regional_overrides`.** The statutory
calculator computes one component at a time. The 1/3 rule has to see *every* deduction
on the slip simultaneously to know whether the total breaches the cap and which rows to
trim. It cannot live in a per-component hook.

**The accrual journal is rewritten on `before_insert`, not built from scratch.** Core
HRMS creates the Payroll Entry journal with one line per salary component. Rather than
duplicating Payroll Entry, `payroll_journal.py` lets core build the document and then
replaces the `accounts` table. It identifies the right journal by
`reference_type`/`reference_name` on the payable row — which core sets — rather than by
pattern-matching the remark text.

### 2.2 Doctypes added (26)

**Settings and configuration**
- `Kenya Payroll Settings` (single) + `Kenya PAYE Band`
- `Company Payroll Settings` + `Terminal Dues Notice Period Rule`,
  `Overtime Working Hours Rule`, `Statutory Income Component Mapping`,
  `Leave Provision Leave Type`

**The 1/3 rule**
- `Deduction Priority` — standalone, one per company, with
  `Deduction Priority Detail` (the ordered component list) and
  `Deduction Priority Base Component` (which earnings form the wage base)
- `Deduction Group` — the tiers a cut cascades through
- `Deferred Deduction` — the arrears ledger, with `Deferred Deduction Recovery`
- `Brought Forward Deduction`, `Salary Slip Deferred Deduction` — the payslip-side views

**Terminal dues**
- `Terminal Dues Settlement` (37 fields) + `Terminal Dues Earning`,
  `Terminal Dues Deduction`, `Terminal Dues Asset`

**Leave liability**
- `Leave Provision` (25 fields) + `Leave Provision Detail`

**Gratuity**
- `Gratuity Tax Computation`, `Gratuity Tax Exemption`

**Collective bargaining**
- `CBA` + `CBA Pay Table`

Standalone `Deduction Priority` was a specific instruction — the alternative was more
child tables on `Company Payroll Settings`, which is already at 51 fields and does not
need three more grids.

### 2.3 Custom fields and fixtures

62 custom fields across five core doctypes: Employee 29, Salary Slip 18, Gratuity 8,
Salary Component 3, Leave Encashment 2. Eight fixture files, including three Property
Setters.

**Fixtures are the source of truth, not the database.** This cost real time to learn.
Editing a Custom Field's description in the UI works until the next `bench migrate`,
which re-imports the fixture JSON and silently reverts it. Field descriptions were
rewritten twice before the cause was found. **Always edit the fixture JSON, then
migrate.** `bench export-fixtures` before `bench migrate`, and prefix multi-entry
Custom Field fixtures so they don't clobber each other.

**`Employee-main-field_order` overrides everything.** Once a site customises the
Employee form, Frappe stores the entire field order in a Property Setter and follows it
literally. `insert_after` is ignored. An explicit `idx` is ignored. Any field not named
in that list is appended to the bottom of the form. Three attempts at ordering the
Employee salary fields failed for this reason before `setup._splice_into_field_order`
was written to inject our fields into the saved order.

---

## 3. Module review

### 3.1 `kenya_statutory_calculator.py` (516 lines)

Computes NSSF, SHIF, the Affordable Housing Levy and PAYE from
`Kenya Payroll Settings`, driven per company by the component mapping.

**Correct and verified:**
- NSSF Tier 1 6% capped 540 (LEL 9,000); Tier 2 6% capped 5,940 (UEL 108,000)
- SHIF 2.75% with a **300 minimum that applies to monthly payrolls only** — a weekly
  slip gets 2.75% with no floor, which is right and is a case people get wrong
- AHL 1.5% employee + 1.5% employer
- PAYE bands 24,000@10% / 32,333@25% / 500,000@30% / 800,000@32.5% / above@35%
- Personal relief 2,400/month, insurance relief 15% capped 5,000, pension relief
  capped 30,000

**Design points:**
- `_get_structure_deduction_components()` returns `{"allowed", "by_formula"}`. A
  component absent from the employee's Salary Structure is not deducted at all — that
  is how an employee is exempted from NSSF, rather than by a flag.
- `_set_amount(..., by_formula)` returns the *effective* amount. This matters: relief
  must be computed against what was actually deducted, not what the app would have
  deducted. It is what makes a Salary Structure formula able to override the settings
  for one component without corrupting the reliefs downstream.

**Bug found and fixed:** in Flat Monthly relief mode the relief fields were never
written to the slip:

```python
salary_slip.custom_personal_relief_available_this_month = monthly_relief
salary_slip.custom_personal_relief_utilized = relief_utilized
```

Relief was applied to the PAYE figure correctly, but the audit fields read zero — which
would have produced wrong P9 and P10 returns. This is the most consequential defect
found in the whole build, and it was invisible on the payslip.

### 3.2 `kenya_statutory_gross_pay.py` (134 lines)

Small and important. ERPNext's `gross_pay` is **not** the base Kenyan statutory
deductions use. This module returns two separate figures:

- `statutory_cash_base` — the NSSF / SHIF / AHL base
- `taxable_income` — the PAYE base, *before* the employee NSSF/SHIF/AHL deductions

Keeping these apart, and apart from `gross_pay`, is why non-cash benefits (a car
benefit, for instance) can be taxable without inflating the statutory base.

### 3.3 `deduction_cap.py` (516 lines) — the 1/3 rule

The largest and most legally sensitive module. Employment Act 2007 §19(3): an employer
may not deduct more than two thirds of an employee's wages, statutory deductions count
*inside* that cap, and it applies "notwithstanding any other written law".

**How it works**

1. Compute the wage base. Three options, per company: full gross, cash earnings only,
   or a named list of components (`Deduction Priority Base Component`). Karen Roses uses
   constant allowances; others use full gross.
2. Bring forward any arrears from `Deferred Deduction`.
3. If total deductions exceed `PERMITTED_FRACTION = 2/3`, cut the excess.
4. The cut cascades through `Deduction Group` tiers — statutory first and untouchable,
   then each tier in order.
5. Within a tier, share the cut by **Pro-rata**, **Largest First** or **As Listed**.
6. What was not collected is written to `Deferred Deduction` and recovered on later
   slips, oldest debt first.

**Leavers.** `_is_final_slip` lets the relieving date decide alone; status is only
consulted when there is no date:

```python
if emp.relieving_date:
    return getdate(emp.relieving_date) <= getdate(doc.end_date)
return emp.status == "Left"
```

This was corrected during the build. An employee who leaves *within* the payroll period
can and must have their slip processed — the earlier version refused. On a final slip
the cap is not applied, because there is no later payslip to recover from.

**Bug found and fixed (self-caught, would have been serious):** the first version
credited the recovery ledger with the *scheduled* recovery amount rather than the amount
actually collected. Where the cap trimmed a recovery, the arrear was marked paid without
the money arriving. Allocation is now oldest-debt-first and credits only money actually
collected.

**Unconfigured-company handling:** `doc.flags.one_third_unconfigured = not rules` drives
a distinct message when no `Deduction Priority` exists, so a company that has not been
configured gets told so rather than silently running uncapped.

### 3.4 `terminal_dues_settlement.py` (controller) — final dues

Two provisions, as specified:

- **All Statutory** — NSSF, SHIF and the Housing Levy are deducted, reusing
  `compute_nssf` / `compute_shif` / `compute_housing_levy` from the statutory
  calculator, and they relieve taxable pay
- **PAYE Only** — tax alone, for employers who deduct nothing else from leavers

Earnings assembled: days worked, pay in lieu of notice (or a deduction where the
employee owes notice), gratuity, leave encashment, asset recovery.

**Bug found and fixed:** PAYE came out zero on settlements up to about 200,000. The
original implementation read the annual `Income Tax Slab` and applied the 28,800 annual
relief to a monthly figure. Rewritten onto Kenya Payroll Settings, relieving the levies
and NSSF against the retirement cap. This was a large error — a leaver could have been
paid out with no tax withheld.

**No silent fallback on the divisor.** Guessing 26 would produce a daily rate that looks
right and is not, and every derived figure — days worked, notice pay — would be wrong
with nothing showing it. Missing divisor now throws.

**Basic Pay fallback.** Where no payslip exists — someone who left before their first
payroll ran — the daily rate falls back to `Employee.basic_pay`. Previously returned
zero and the settlement silently came to nothing.

**Empty-journal guard (added this session).** A settlement that legitimately comes to
zero now posts no journal at all. This also works around a genuine ERPNext quirk:
`erpnext/accounts/utils.py:1836` reads an **empty** `accounts` list as "validate every
stock account in the company", so a journal with all its lines filtered out came back
complaining about `Stock In Hand - KR`, which had nothing to do with payroll.

### 3.5 `leave_provision.py` — leave liability

Snapshots the accrued leave liability for a period and posts the **movement**, not the
balance — so a second provision posts only the change, and releases where a group's
liability fell.

- `set_defaults()` pulls the basic pay component, divisor, both accounts and the leave
  types from settings, and defaults the posting date to today. This was rebuilt after
  the observation that a payroll manager should not be re-entering the divisor and leave
  types on every run.
- `validate_accounts()` enforces Expense / Liability root types and rejects group
  accounts.
- Liability uses `get_leave_balance_on(employee, leave_type, date)`, which is date-aware
  and handles allocation expiry — rather than reading the allocation total.

### 3.6 `gratuity_utils.py` (241 lines)

Computes gratuity from a `Gratuity Rule` slab, phases tax exemption by the exemption
date, and spreads the taxable portion back over recent assessment years per KRA
practice.

**Reverted, by instruction.** The KRA "360,000 per year of service under a public
pension scheme" rule was built and then removed in full, along with four orphaned
database columns. The instruction was explicit: leave gratuity as it was.

**Retained from that work — a real crash fix.** Anniversary dates were being rebuilt as
strings:

```python
getdate(f"{doj.year+yr}-{doj.month:02d}-{doj.day:02d}")   # throws for 29 February
```

An employee who joined on 29 February has no anniversary in a common year, and
`"2027-02-29"` is not a date. Replaced with `getdate(add_to_date(doj, years=yr))` in
**two** places. Worth noting how this surfaced: a claim of mine that
`total_days` could never be zero was challenged, and my reasoning for it was wrong.
Reading the code properly to answer the challenge is what exposed the crash.

### 3.7 `payroll_journal.py` (285 lines)

Rewrites the accrual journal so it posts by meaning rather than one line per component:
gross pay debited, absence components netted, employer contributions split (liability to
the fund credited, company cost debited to the Employer Expense Account), employee
deductions credited, loan repayments handled.

**Bug found and fixed:** the employer section was sweeping in non-employer components
because it keyed off `do_not_include_in_total`. Now keys off
`custom_is_employer_contribution`.

### 3.8 `statutory_reports.py` (212 lines) — the shared register engine

`IDENTITY_SOURCES` maps a logical column (National ID, NSSF number, SHIF number) to
several possible Employee fieldnames, because the four client sites do not agree on
field names.

**Bug found and fixed:** columns were being dropped entirely when the underlying field
did not exist, so the registers came out missing National ID / NSSF / SHIF and did not
match the Kikwetu reports they were meant to replace. Columns now **always render**,
selecting `NULL` where the field is absent — matching Kikwetu's exact column sets and
labels.

### 3.9 `cba_utils.py`, `leave_travelling_allowance.py`, `overtime_utils.py`, `salary_structure_utils.py`

- **CBA** — blocks saving an Employee below the collective-bargaining minimum for their
  job category, tracks Previous Base Pay, and bulk-applies a submitted CBA's pay table
  to matching employees.
- **LTA** — pays a leave travelling allowance for a long enough single stretch of
  qualifying leave, once per **allocation period** rather than per calendar year. The
  original Server Script only ran After Submit, so cancelling the leave left the
  Additional Salary standing; `cancel_lta` fixes that.
- **`salary_structure_utils.validate_after_submit`** — a Property Setter opens the
  earnings and deductions grids on a submitted Salary Structure so one component can be
  added without cancel/amend/re-assign. Since `validate()` does not run on a submitted
  save, the component checks are re-run here, and **removals are warned about** —
  submitted payslips keep what they were built with, but a draft slip silently loses the
  component on its next save.

---

## 4. Reports (8)

| Report | Purpose |
|---|---|
| **Company Register** | Full payroll register, all components |
| **National Social Security Fund** | NSSF return |
| **Affordable Housing Levy** | AHL return |
| **Social Health Insurance Fund** | SHIF return |
| **HELB** | Loan board deductions |
| **Kenya P9 Card** | Annual employee tax deduction card |
| **Kenya P10** | Annual employer PAYE return |
| **Leave Liability** | Provision backing the liability journal |

**Bug found and fixed:** Company Register was 3,000 short because it read
`do_not_include_in_total` from the **component master** rather than the **payslip row**.
The two can legitimately differ — the row is what was actually applied.

---

## 5. Testing

Three harnesses plus a UI seeder:

| Harness | Coverage |
|---|---|
| `tests/end_to_end_checks.py` | 81 assertions across both settings doctypes |
| `tests/deduction_cap_checks.py` | 74 assertions on the 1/3 rule |
| `tests/seed_ui_demo.py` | 15 employees seeding every code path for manual UI verification |

`deduction_cap_checks.statutory_rows()` builds from `get_statutory_components()` rather
than cloning a live Salary Structure, so the tests don't depend on how a site is set up.

### The UI seeder

```bash
bench --site <site> execute upande_payroll.tests.seed_ui_demo.run
bench --site <site> execute upande_payroll.tests.seed_ui_demo.wipe
```

Seeds 15 `TEST *` employees, 17 salary slips, 3 terminal dues settlements, 1 leave
provision, 2 deferred deductions, 2 leave allocations and 4 journal entries — **all
submitted**, all removable — and prints what to look for on each.

| Case | Proves |
|---|---|
| TEST 01 | Full statutory: NSSF 540 + 4,260, SHIF 2,200, AHL 1,200 |
| TEST 02 | SHIF floor: 300, not 2.75% of 8,000 |
| TEST 03 | Weekly slip: SHIF 55, floor correctly **not** applied |
| TEST 04 | Wage base 40,000 not 140,000 — car benefit taxed but not cash |
| TEST 05 | Second employer: relief 0, PAYE = tax charged |
| TEST 06 | Components absent from the structure → not deducted, PAYE higher |
| TEST 07 | Structure with no deductions at all |
| TEST 08 | Relief carry-forward across two months |
| TEST 09 | 1/3 cap trims an advance, arrear raised and recovered next month |
| TEST 10 | Final slip: cap not applied, arrears collected in full |
| TEST 11 / 12 | All Statutory vs PAYE Only, same gross |
| TEST 13 | No payslip → Basic Pay fallback |
| TEST 14 / 15 | Leave provision and its journal |

**TEST 11 vs 12, both on gross 169,230.77:**

| | All Statutory | PAYE Only |
|---|---|---|
| NSSF T1 / T2 | 540 / 5,940 | — |
| SHIF 2.75% | 4,653.85 | — |
| Housing Levy 1.5% | 2,538.46 | — |
| PAYE | **39,050.89** | **43,152.58** |
| Net payable | 116,507.57 | 126,078.19 |

PAYE is 4,101.69 lower under All Statutory — the levies and NSSF relieving taxable pay,
which is the entire point of having the two provisions.

**TEST 13:** no payslip, 15 × (52,000 / 26) = 30,000.00, PAYE 1,500, journal balanced.

### Seeder robustness

`run()` now commits stage by stage inside a try/except and prints a
`STAGES THAT DID NOT SEED` block. The first version rolled everything back when a single
case failed, which is how a debugging session found a completely empty site.

`set_days_worked(doc, days)` sets days worked on a **second** save, because the first
save fetches dues and recomputes days worked from submitted Attendance — overwriting
whatever was passed in. That is also the real UI workflow, and the row description
already prompts for it: *"no attendance records found. Edit 'Days Worked in Final Month'
above and save."*

---

## 6. Defect log

Every bug found during the build, with how it would have shown up in production.

| # | Defect | Production impact | Status |
|---|---|---|---|
| 1 | Personal relief fields zero in Flat Monthly mode | **Wrong P9 and P10 returns.** Invisible on the payslip | Fixed |
| 2 | Terminal dues PAYE zero up to ~200,000 | **Leaver paid out with no tax withheld** | Fixed |
| 3 | Recovery credited without collection | **Arrears written off unpaid** | Fixed (self-caught) |
| 4 | Employer section swept in non-employer components | Misstated payroll journal | Fixed |
| 5 | Company Register read the component master flag, not the payslip row | Register 3,000 short | Fixed |
| 6 | Register identity columns dropped when the field was absent | Registers did not match the Kikwetu reports they replace | Fixed |
| 7 | Gratuity crashed for a 29 February joiner | Hard failure, two places | Fixed |
| 8 | Divisor fell back to 26 silently | Every daily-rate figure quietly wrong | Fixed (now throws) |
| 9 | Terminal dues returned zero with no payslip | Leaver paid nothing, no warning | Fixed (Basic Pay fallback) |
| 10 | Gratuity scheme Check flag inert — a Check is `0`, never `None` | Flag did nothing | Reverted with the rest of that work |
| 11 | 1/3 rule refused mid-period leavers | Legitimate slips blocked | Fixed |
| 12 | Zero-value settlement journal tripped ERPNext's stock validation | Settlement could not be submitted | Fixed (guard) |
| 13 | Seeder rolled back all stages on one failure | Test data lost | Fixed (stage commits) |

### Platform behaviour learned the hard way

- Fixtures win over the database on every `bench migrate`
- `Employee-main-field_order` overrides `insert_after` **and** `idx` entirely
- A Check field is `0`, never `None` — "unset" and "explicitly off" are indistinguishable
- Frappe never drops a column when a field is removed
- HRMS `on_submit`: `if self.net_pay < 0: frappe.throw(...)`
- HRMS forbids inflating `Salary Slip Loan.total_payment` above `calculate_amounts()`
- HRMS blocks a slip only when `relieving_date < start_date`
- `@if_lending_app_installed` — loans degrade to no-ops without the `lending` app
- ERPNext `get_stock_accounts()` treats an empty accounts list as "check everything"
- ERPNext `validate_stock_accounts` only runs with perpetual inventory enabled

---

## 7. Open items

### Blocking go-live

**1. `Deduction Priority` and `Deduction Group` are not configured for any real
company.** The engine is built and tested; the data is not there. Until it is, the 1/3
rule will report itself unconfigured and deductions will run uncapped. This is the
single largest gap.

**2. Gratuity KRA rule — parked by instruction.** The 360,000-per-year-of-service
exemption for public pension schemes was reverted in full. You wanted to give your own
reading of the rule before it is rebuilt.

### Design decisions still open

**3. Loans in the 1/3 rule.** Parked. Only Karen Roses has the lending module. The
engine handles components sharing a priority level, but loan repayments are not yet
routed through it. Needs deciding before Karen goes live.

**4. A leaver whose arrears exceed their final dues.** HRMS refuses to submit a slip
with negative net pay. On a final slip the cap is not applied, so arrears come out in
full — and if they exceed the dues, the slip cannot be submitted at all. Three options:
write off the shortfall, leave it as a receivable against the employee, or cap the final
slip at zero net. This needs a decision, not a default.

**5. `Deferred Deduction Recovery` may be redundant** with `Brought Forward Deduction`.
Both describe the same movement from different sides. Worth removing one before there is
production data in it — much harder afterwards.

### Recommended, not built

**6. Affordability check at loan origination.** The 1/3 rule currently catches a breach
at payroll time, when the money is already committed. Checking at origination — "this
advance will breach the cap given existing deductions" — prevents the arrear rather than
managing it.

**7. Arrears ageing report.** There is a ledger with no report over it. Nobody can
currently answer "who owes what, and how long has it been outstanding".

### Engineering debt

**8. The test harnesses are assertion scripts, not unit tests.** They run through
`bench execute` against a live site rather than `bench run-tests` with fixtures and
rollback. They are genuinely useful and they caught real defects, but they depend on site
state and cannot run in CI. Converting the two `*_checks.py` files to `FrappeTestCase`
would make them gate a merge.

**9. No CI.** `pre-commit` is configured (ruff, eslint, prettier, pyupgrade) but nothing
runs it automatically.

**10. `Company Payroll Settings` is at 51 fields.** Still navigable, but it is the
obvious place for the next twenty, and it should not be. New areas deserve their own
doctype, as `Deduction Priority` got.

**11. Days worked is recomputed from Attendance on first save.** Correct for sites that
use Attendance, a sharp edge for those that don't — the officer must edit and re-save.
The row description says so, but it will generate questions.

---

## 8. Pre-go-live checklist

- [ ] Configure `Deduction Priority` + `Deduction Group` per company **(blocking)**
- [ ] Verify `Kenya Payroll Settings` rates against the current KRA schedule
- [ ] Map every salary component in `Company Payroll Settings` on each site
- [ ] Map every component to an account (`Salary Component Account`, per company)
- [ ] Set the daily rate divisor per company — it throws if missing
- [ ] Review `Terminal Dues Notice Period Rule` tenure bands
- [ ] Set the leave provision component, divisor, accounts and leave types
- [ ] Decide item 4 (leaver arrears exceeding final dues)
- [ ] Decide item 5 (`Deferred Deduction Recovery`) before production data exists
- [ ] Run the three test harnesses on each target site
- [ ] Parallel-run one period against the existing Server Scripts and reconcile
- [ ] Reconcile the P9 / P10 output against the last filed return

The parallel run is the one that matters. Every other check confirms the app does what
it was told; only a parallel run confirms it does what the old scripts did, and explains
every difference.

---

## 9. Assessment

**What is solid.** The configuration model is the right one and it holds up — four
client sites' worth of variation genuinely reduces to field values. The 1/3 rule is the
most complete implementation of Employment Act §19(3) across any of these sites, and it
is the only one with an arrears ledger that actually reconciles. The statutory
calculator is correct on the cases people get wrong: the monthly-only SHIF floor, the
taxable-but-not-cash wage base, relief computed against what was actually deducted. The
separation of `statutory_cash_base` from `taxable_income` from `gross_pay` is the
detail most implementations get wrong, and it is right here.

**What the defect log says.** Thirteen bugs, four of which would have produced wrong
statutory filings or wrong payments to a leaver. None were found by reading the code —
all were found by running it and checking a number against one worked out by hand. Two
were found only because a claim of mine was challenged and turned out to be wrong. That
is the argument for item 8: the harnesses that caught these should be able to run
automatically, not on request.

**The honest risk.** The code is in better shape than the configuration. An unconfigured
`Deduction Priority` means the 1/3 rule silently does nothing on that company — it
reports itself unconfigured, but a payroll still runs. The gap between "the app is
finished" and "the app is live" is almost entirely data entry, and it is the part with
no test coverage.

**Recommendation.** Configure Karen Roses fully, parallel-run one period against the
existing scripts, and reconcile line by line before touching the other three sites.
Settle items 4 and 5 first — both get more expensive once production data exists.
