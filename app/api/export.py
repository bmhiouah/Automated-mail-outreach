"""Downloads: the sourcing worklist as markdown, the contact list as CSV.

Both return `Raw`, because a file is not JSON. The CSV column order is fixed here
rather than derived from the row, so a new contact column cannot silently reshuffle
a spreadsheet you already have open.
"""
import csv
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from domain import Raw                          # noqa: E402
from api.contacts import api_contacts           # noqa: E402
from api.sourcing import api_sourcing_markdown  # noqa: E402

CONTACT_COLUMNS = ["first_name", "last_name", "job_title", "desk", "company_name",
                   "email", "email_masked", "linkedin_url", "city",
                   "source"]


def api_export_sourcing(params):
    body = api_sourcing_markdown(params).encode("utf-8")
    return Raw(body, "text/markdown; charset=utf-8",
               {"Content-Disposition": "attachment; filename=sourcing-worklist.md"})


def api_export_contacts(params):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CONTACT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in api_contacts({}):
        writer.writerow(row)
    return Raw(buf.getvalue().encode("utf-8"), "text/csv; charset=utf-8")


ROUTES = [
    ("GET", "export/sourcing", lambda p, rest, body: api_export_sourcing(p)),
    ("GET", "export/contacts", lambda p, rest, body: api_export_contacts(p)),
]