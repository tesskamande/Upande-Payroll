"""Checks for Bank Branch, the bank and branch codes payroll advice files need.

Run with:

    bench --site <site> execute upande_payroll.tests.bank_branch_checks.run

Everything is rolled back at the end, so it is safe against a working site. It
builds its own banks and branches and touches one existing employee only inside
the transaction it discards.

Covers: the composed name, branch code uniqueness scoped to the bank rather
than global, trimming, renaming carrying the Employee link across, and that
disabling is not deleting.
"""

import frappe

RESULTS = []


def check(name, got, want):
    RESULTS.append((got == want, name, got, want))


def refuses(name, fn):
    """A check that passes only when the operation is rejected.

    Wrapped in a savepoint: a throw part way through an insert leaves the
    transaction holding half a document, and the next check would then be
    testing the wreckage.
    """
    frappe.db.savepoint("bank_branch_check")
    try:
        fn()
        RESULTS.append((False, name, "accepted", "rejected"))
    except frappe.ValidationError:
        RESULTS.append((True, name, "rejected", "rejected"))
    except Exception as e:
        RESULTS.append((False, name, f"{type(e).__name__}: {e}", "ValidationError"))
    frappe.db.rollback(save_point="bank_branch_check")


def _bank(name):
    if not frappe.db.exists("Bank", name):
        frappe.get_doc({"doctype": "Bank", "bank_name": name}).insert()
    return name


def _branch(bank, branch_name, code, **kw):
    doc = frappe.get_doc(
        {
            "doctype": "Bank Branch",
            "bank": bank,
            "branch_name": branch_name,
            "branch_code": code,
            **kw,
        }
    )
    doc.insert()
    return doc


def run():
    kcb = _bank("ZZ Test Bank One")
    absa = _bank("ZZ Test Bank Two")

    # --- the composed name ---------------------------------------------
    moi = _branch(kcb, "Moi Avenue", "100")
    check("name composes bank and branch", moi.name, f"{kcb} - Moi Avenue")

    # --- uniqueness is per bank, not global ----------------------------
    refuses(
        "a second branch on the same code in the same bank is refused",
        lambda: _branch(kcb, "Kimathi Street", "100"),
    )
    twin = _branch(absa, "Moi Avenue", "100")
    check("the same code under another bank is allowed", twin.branch_code, "100")

    # --- trimming -------------------------------------------------------
    padded = _branch(kcb, "Industrial Area", "  205  ")
    check("a pasted code is trimmed", padded.branch_code, "205")
    refuses(
        "a code of nothing but spaces is refused",
        lambda: _branch(kcb, "Nowhere", "   "),
    )

    # --- renaming carries the Employee link ----------------------------
    employee = frappe.get_all("Employee", filters={"status": "Active"}, limit=1)
    if employee:
        emp = employee[0].name
        frappe.db.set_value(
            "Employee", emp, {"bank_name": kcb, "custom_bank_branch": moi.name}
        )
        moi.branch_name = "Moi Avenue Branch"
        moi.save()
        check("the name follows a corrected branch name", moi.name, f"{kcb} - Moi Avenue Branch")
        check(
            "the employee link follows the rename",
            frappe.db.get_value("Employee", emp, "custom_bank_branch"),
            f"{kcb} - Moi Avenue Branch",
        )

    # --- a code correction is not a rename ------------------------------
    moi.branch_code = "101"
    moi.save()
    check("correcting only the code leaves the name alone", moi.name, f"{kcb} - Moi Avenue Branch")

    # --- disabling is not deleting --------------------------------------
    padded.disabled = 1
    padded.save()
    check(
        "a disabled branch is still on file",
        bool(frappe.db.exists("Bank Branch", padded.name)),
        True,
    )

    _report()
    frappe.db.rollback()


def _report():
    failed = [r for r in RESULTS if not r[0]]
    for ok, name, got, want in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            print(f"          got {got!r}, wanted {want!r}")
    print(f"\n  {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
