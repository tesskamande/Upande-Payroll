"""Checks that an emailed payslip is rendered by the generator that was chosen.

Run with:

    bench --site <site> execute upande_payroll.tests.payslip_email_checks.run

Everything is rolled back at the end, so it is safe against a working site.

Setting a Print Format's PDF Generator to Chrome and having the emailed slip
arrive as wkhtmltopdf is not a misconfiguration - it is the order the two
decisions are made in. HRMS attaches the slip without naming a print format, so
get_print settles the generator against a format of None ("wkhtmltopdf",
print_utils.py:51) and stores it in form_dict. printview then resolves the real
format and reads its generator (printview.py:98), but the stored answer wins
(printview.py:121). The format is used for the layout and ignored for the
engine, which is why setting a Default Print Format fixes how the slip looks
and not what produced it.

So the rule checked here is: what the app seeds is what the DocType's Default
Print Format asks for, and form_dict is left exactly as it was found.
"""

import io

import frappe

from upande_payroll.salary_slip_utils import _print_generator_for

RESULTS = []
FALLBACK = "wkhtmltopdf"


def check(name, got, want):
    RESULTS.append((got == want, name, got, want))


def resolved(print_format):
    """The generator get_print settles on for this print format."""
    return frappe.get_cached_value("Print Format", print_format, "pdf_generator") or FALLBACK


def run():
    fmt = frappe.db.get_value(
        "Print Format", {"doc_type": "Salary Slip", "disabled": 0}, "name"
    )
    if not fmt:
        print("  no Salary Slip print format on this site, nothing to check")
        return

    frappe.db.savepoint("payslip_email")
    try:
        _check_unset()
        _check_named(fmt)
        _check_beta_and_disabled_are_left_alone(fmt)
        _check_form_dict_restored(fmt)
    finally:
        frappe.db.rollback(save_point="payslip_email")
        frappe.clear_cache(doctype="Print Format")
        frappe.clear_cache(doctype="Salary Slip")

    _report()


def _default_format(name):
    frappe.db.set_value("DocType", "Salary Slip", "default_print_format", name)
    frappe.clear_cache(doctype="Salary Slip")


def _set(fmt, **values):
    for field, value in values.items():
        frappe.db.set_value("Print Format", fmt, field, value)
    frappe.clear_cache(doctype="Print Format")


def _check_unset():
    """No default format means nobody asked, so HRMS is left alone."""
    _default_format(None)
    check("no default print format seeds nothing", _print_generator_for("Salary Slip"), None)

    _default_format("Standard")
    check("the Standard format seeds nothing", _print_generator_for("Salary Slip"), None)


def _check_named(fmt):
    _default_format(fmt)

    _set(fmt, pdf_generator=None, print_format_builder_beta=0, disabled=0)
    check("a format naming no generator seeds nothing",
          _print_generator_for("Salary Slip"), None)

    for generator in ("chrome", "wkhtmltopdf"):
        _set(fmt, pdf_generator=generator)
        check(f"a format set to {generator} seeds {generator}",
              _print_generator_for("Salary Slip"), generator)

    # The bug itself: the same format, asked about the way HRMS asks.
    _set(fmt, pdf_generator="chrome")
    check("HRMS alone would still settle on wkhtmltopdf", resolved(None), FALLBACK)
    check("naming the format gives chrome", resolved(fmt), "chrome")
    check("what the app seeds matches the format",
          _print_generator_for("Salary Slip"), resolved(fmt))


def _check_beta_and_disabled_are_left_alone(fmt):
    _default_format(fmt)

    _set(fmt, pdf_generator="chrome", print_format_builder_beta=1)
    check("a weasyprint format seeds nothing", _print_generator_for("Salary Slip"), None)

    _set(fmt, print_format_builder_beta=0, disabled=1)
    check("a disabled format seeds nothing", _print_generator_for("Salary Slip"), None)
    _set(fmt, disabled=0)


def _check_form_dict_restored(fmt):
    """form_dict belongs to the request, not to one slip."""
    _default_format(fmt)
    _set(fmt, pdf_generator="chrome", print_format_builder_beta=0, disabled=0)

    form_dict = frappe.local.form_dict

    form_dict.pop("pdf_generator", None)
    _run_override()
    check("an absent pdf_generator is absent again afterwards",
          "pdf_generator" in form_dict, False)

    form_dict.pdf_generator = "wkhtmltopdf"
    _run_override()
    check("an existing pdf_generator is put back",
          form_dict.get("pdf_generator"), "wkhtmltopdf")
    form_dict.pop("pdf_generator", None)


def _run_override():
    """Drive the override with a stand-in for HRMS, so no email is sent.

    What super().email_salary_slip() does is HRMS's business; what matters here
    is the value it sees and what is left behind once it returns.
    """
    from upande_payroll.salary_slip_utils import SalarySlipMixin

    seen = []

    class Core:
        doctype = "Salary Slip"

        def email_salary_slip(self):
            seen.append(frappe.local.form_dict.get("pdf_generator"))

    class Stub(SalarySlipMixin, Core):
        pass

    Stub().email_salary_slip()
    check("HRMS is handed chrome", seen, ["chrome"])


def _report():
    failed = [r for r in RESULTS if not r[0]]
    for ok, name, got, want in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            print(f"          got  {got}")
            print(f"          want {want}")
    print(f"\n  {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")


# ----------------------------------------------------------------------
# End to end: drive HRMS's real email path and see which generator it reaches
# ----------------------------------------------------------------------

def run_email():
    """Email a real payslip both ways and record which PDF generator ran.

    Run separately from run():

        bench --site <site> execute upande_payroll.tests.payslip_email_checks.run_email

    This is the check the rest of the module cannot make. run() proves what the
    app decides; this drives HRMS's own email_salary_slip - email template,
    password, attachment and all - and reports which of the two generators
    frappe actually routed to at print_utils.py:79.

    Neither generator is really invoked and no mail is sent: both are replaced
    for the duration, so this needs no browser and no wkhtmltopdf binary and
    gives the same answer on any machine. Rendering for real is a poor test
    anyway - it needs wkhtmltopdf installed for one half and, for the other, a
    Chrome that can fetch the site over HTTP at its own host URL to build the
    header and footer. Neither holds on a development bench, and a failure
    there would say nothing about the routing this is checking.
    """
    slip = frappe.db.get_value("Salary Slip", {"docstatus": 1}, "name")
    fmt = frappe.db.get_value("Print Format", {"doc_type": "Salary Slip", "disabled": 0}, "name")
    if not slip or not fmt:
        print("  need a submitted salary slip and a print format on this site")
        return

    frappe.db.savepoint("payslip_email_e2e")
    was_in_test = frappe.flags.in_test
    try:
        frappe.flags.in_test = True     # HRMS then sends inline instead of enqueuing
        doc = frappe.get_doc("Salary Slip", slip)
        frappe.db.set_value("Employee", doc.employee, "prefered_email", "checks@example.invalid")
        _set(fmt, pdf_generator="chrome", print_format_builder_beta=0, disabled=0)

        _default_format(None)
        check("with no default print format HRMS reaches wkhtmltopdf",
              _generator_reached(doc), "wkhtmltopdf")

        _default_format(fmt)
        check("with the format set to chrome HRMS reaches chrome",
              _generator_reached(doc), "chrome")

        _set(fmt, pdf_generator="wkhtmltopdf")
        check("a format asking for wkhtmltopdf still reaches wkhtmltopdf",
              _generator_reached(doc), "wkhtmltopdf")
    finally:
        frappe.flags.in_test = was_in_test
        frappe.db.rollback(save_point="payslip_email_e2e")
        frappe.clear_cache(doctype="Print Format")
        frappe.clear_cache(doctype="Salary Slip")

    _report()


def _generator_reached(doc):
    """Which generator HRMS's email path routed to, without running either."""
    import frappe.utils.pdf as pdf_module

    STUB = b"%PDF-1.4\n% stub\n"
    reached = []

    def chrome(*args, **kwargs):
        reached.append("chrome")
        return STUB

    def wkhtmltopdf(*args, **kwargs):
        reached.append("wkhtmltopdf")
        return STUB

    real = (pdf_module.get_chrome_pdf, pdf_module.get_pdf, frappe.sendmail)
    pdf_module.get_chrome_pdf = chrome
    pdf_module.get_pdf = wkhtmltopdf
    frappe.sendmail = lambda **kwargs: None
    try:
        doc.email_salary_slip()
    finally:
        pdf_module.get_chrome_pdf, pdf_module.get_pdf, frappe.sendmail = real

    if not reached:
        return "(no PDF was generated - is the employee's Preferred Email set?)"
    return reached[0]
