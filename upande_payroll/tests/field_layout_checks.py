"""Checks that every field this app ships lands where it says it should.

Run with:

    bench --site <site> execute upande_payroll.tests.field_layout_checks.run

Nothing is written, so this is safe against a working site.

A wrong insert_after does not fail. Frappe appends the field to the end of the
form and says nothing, and a field_order Property Setter captured while a field
was newly created keeps it there for good. That is how ten Employee fields -
basic_pay, the statutory opt-outs, the salary expense account and the standard
payroll_cost_center with them - came to render inside the Connections tab on a
live site, months after they were added.

So the rule checked here is not "insert_after is set". It is: the field renders
in the same tab as the field it was told to sit after. That is the thing a
reader of the form would notice, and the thing nothing else was watching.

What counts as shipped is read from the app's own fixtures hook applied to the
live Custom Fields, not from the exported JSON. Reading the JSON meant a field
created in code and not yet exported was invisible here - which is exactly how
custom_mpesa_number came to exist on one site and ship to none, unnoticed. The
code-defined dictionaries are folded in as well, so a new field is checked from
the moment it is written rather than from the moment somebody remembers to
export.

There is deliberately no check that a code-created field also has a fixtures
filter. It looks like a gap and is not: create_custom_fields runs from
after_migrate, which runs on every site, so those fields travel without one.
A fixture only adds version control and carries any later edit made through the
UI.
"""

import glob
import json
import os

import frappe

RESULTS = []


def check(name, got, want):
    RESULTS.append((got == want, name, got, want))


def _fixture_filters():
	"""The Custom Field filters this app declares it ships."""
	out = []
	for entry in frappe.get_hooks("fixtures", app_name="upande_payroll") or []:
		if isinstance(entry, dict) and entry.get("dt") == "Custom Field":
			out.append(entry.get("filters") or [])
	return out


def code_defined_fields():
	"""Fields the app creates from code, as {doctype: [field, ...]}."""
	from upande_payroll.setup import MPESA_FIELDS, STATUTORY_FIELDS

	merged = {}
	for source in (STATUTORY_FIELDS, MPESA_FIELDS):
		for doctype, fields in source.items():
			merged.setdefault(doctype, []).extend(fields)
	return merged


def shipped_fields():
	"""Every Custom Field this app ships, read live rather than from the JSON."""
	seen, out = set(), []

	for filters in _fixture_filters():
		for row in frappe.get_all(
			"Custom Field", filters=filters, fields=["dt", "fieldname", "insert_after"]
		):
			key = (row.dt, row.fieldname)
			if key in seen:
				continue
			seen.add(key)
			out.append(("fixtures", row.dt, row.fieldname, row.insert_after))

	for doctype, fields in code_defined_fields().items():
		for field in fields:
			key = (doctype, field["fieldname"])
			if key in seen:
				continue
			seen.add(key)
			out.append(("setup.py", doctype, field["fieldname"], field.get("insert_after")))

	return out


def _tab_of(meta, fieldname):
    """The Tab Break a field renders under, or None before the first one."""
    tab = None
    for field in meta.fields:
        if field.fieldtype == "Tab Break":
            tab = field.fieldname
        if field.fieldname == fieldname:
            return tab
    return "__absent__"


def run():
    fields = shipped_fields()
    check("the app ships fields to check", len(fields) > 0, True)

    metas = {}
    for _src, doctype, _fn, _after in fields:
        if doctype not in metas:
            frappe.clear_cache(doctype=doctype)
            metas[doctype] = frappe.get_meta(doctype, cached=False)

    no_anchor, missing_anchor, absent, wrong_tab = [], [], [], []

    for src, doctype, fieldname, after in fields:
        meta = metas[doctype]
        names = {f.fieldname for f in meta.fields}

        if not (after or "").strip():
            no_anchor.append(f"{doctype}.{fieldname} ({src})")
            continue
        if fieldname not in names:
            absent.append(f"{doctype}.{fieldname} ({src})")
            continue
        if after not in names:
            missing_anchor.append(f"{doctype}.{fieldname} -> {after}")
            continue

        anchor = meta.get_field(after)
        order = [f.fieldname for f in meta.fields]

        # A Tab Break opens a tab rather than sitting in one, so the only thing
        # to ask of it is that it still comes after what it was told to follow.
        if meta.get_field(fieldname).fieldtype == "Tab Break":
            if order.index(fieldname) < order.index(after):
                wrong_tab.append(
                    f"{doctype}.{fieldname} (a tab) is placed before its anchor {after!r}"
                )
            continue

        field_tab = _tab_of(meta, fieldname)
        # An anchor that is itself a tab means the field belongs in that tab.
        anchor_tab = after if anchor.fieldtype == "Tab Break" else _tab_of(meta, after)
        if field_tab != anchor_tab:
            wrong_tab.append(
                f"{doctype}.{fieldname} renders in {field_tab!r} "
                f"but its anchor {after!r} is in {anchor_tab!r}"
            )

    check("every shipped field declares an insert_after", no_anchor, [])
    check("every shipped field exists on its doctype", absent, [])
    check("every insert_after anchor exists", missing_anchor, [])
    check("every shipped field renders in the same tab as its anchor", wrong_tab, [])

    _report(len(fields))


def _report(count):
    failed = [r for r in RESULTS if not r[0]]
    for ok, name, got, want in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            for line in (got if isinstance(got, list) else [got]):
                print(f"          {line}")
    print(f"\n  {count} shipped fields checked")
    print(f"  {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
