import frappe

COMPANY = "Karen Roses"


def account(root_type, *patterns, account_type=None):
    """Pick a sensible existing account, by preference order."""
    for pattern in patterns:
        filters = {"company": COMPANY, "is_group": 0, "root_type": root_type,
                   "name": ("like", pattern)}
        if account_type:
            filters["account_type"] = account_type
        found = frappe.db.get_value("Account", filters, "name")
        if found:
            return found
    return frappe.db.get_value(
        "Account", {"company": COMPANY, "is_group": 0, "root_type": root_type}, "name")


def run():
    try:
        _run()
    except Exception:
        import traceback
        traceback.print_exc()


def _run():
    # A category is only a label - no lending code reads it - but it is what
    # groups products for reporting, so the demo has one.
    if not frappe.db.exists("Loan Category", "STAFF"):
        frappe.get_doc({
            "doctype": "Loan Category",
            "loan_category_code": "STAFF",
            "loan_category_name": "Staff Loans",
        }).insert(ignore_permissions=True)
        print("  created Loan Category: Staff Loans (STAFF)")

    # Which part of an outstanding demand a payment settles first. Lending
    # ships none, and a product will not submit without one. Penalties and
    # charges first, then interest, then the principal - the usual order, and
    # the one that stops a repayment reducing the balance while a charge sits
    # unpaid behind it.
    offset = "Standard Collection Order"
    if not frappe.db.exists("Loan Demand Offset Order", offset):
        frappe.get_doc({
            "doctype": "Loan Demand Offset Order",
            "title": offset,
            "components": [
                {"demand_type": "Penalty"},
                {"demand_type": "Charges"},
                {"demand_type": "Additional Interest"},
                {"demand_type": "Interest"},
                {"demand_type": "Principal"},
            ],
        }).insert(ignore_permissions=True)
        print(f"  created Loan Demand Offset Order: {offset}")

    accounts = {
        # What the employee owes the company - an asset until repaid.
        "loan_account": account("Asset", "%Employee Advance%", "%Loan%", "%Receivable%"),
        # Where the money goes out from, and comes back into.
        "disbursement_account": account("Asset", "%Cash%", "%Bank%"),
        "payment_account": account("Asset", "%Cash%", "%Bank%"),
        "interest_income_account": account("Income", "%Interest%", "%Other Income%"),
        "interest_accrued_account": account("Asset", "%Interest%", "%Receivable%"),
        "interest_receivable_account": account("Asset", "%Interest%", "%Receivable%"),
        "penalty_income_account": account("Income", "%Interest%", "%Other Income%"),
        "write_off_account": account("Expense", "%Write Off%", "%Salary%"),
    }

    name = "Karen Roses Staff Loan"
    if frappe.db.exists("Loan Product", name):
        print(f"  Loan Product {name} already exists")
    else:
        doc = frappe.get_doc({
            "doctype": "Loan Product",
            "product_code": "KR-STAFF",
            "product_name": name,
            "company": COMPANY,
            "loan_category": "STAFF",
            # A term loan repaid in equal monthly instalments, which is what a
            # staff advance recovered through payroll actually is.
            "is_term_loan": 1,
            "rate_of_interest": 0,
            "repayment_schedule_type": "Monthly as per repayment start date",
            "repayment_date_on": "Start of the next month",
            "maximum_loan_amount": 500000,
            "collection_offset_sequence_for_standard_asset": offset,
            "collection_offset_sequence_for_sub_standard_asset": offset,
            "collection_offset_sequence_for_written_off_asset": offset,
            "collection_offset_sequence_for_settlement_collection": offset,
            **accounts,
        })
        doc.insert(ignore_permissions=True)
        doc.submit()
        print(f"  created Loan Product: {doc.name}")

    frappe.db.commit()
    print("\n  accounts used:")
    for k, v in accounts.items():
        print(f"    {k:<30}{v}")
