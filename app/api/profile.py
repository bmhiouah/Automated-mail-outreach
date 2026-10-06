"""The profile: the single row the generator reads to fill every {{my_*}} placeholder."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db                                # noqa: E402
from domain import get_profile, now_iso  # noqa: E402
from taxonomy import PROFILE_FIELDS, PROFILE_QUESTIONS  # noqa: E402


def _profile_payload():
    """The profile row plus the ten questions, so the form renders from
    one call. The questions are static, but serving them here keeps the
    form, the save and the prompt reading a single list."""
    return {**get_profile(), "questions": PROFILE_QUESTIONS}


def api_profile_update(payload):
    """Write only the fields actually sent, so a partial save cannot blank the rest."""
    sets, args = [], []
    for f in PROFILE_FIELDS:
        if f in (payload or {}):
            sets.append(f"{f}=?")
            args.append(payload[f])
    if sets:
        sets.append("updated_at=?")
        args.append(now_iso())
        db.execute(f"UPDATE profile SET {', '.join(sets)} WHERE id=1", args)
    return _profile_payload()


ROUTES = [
    ("GET", "profile", lambda p, rest, body: _profile_payload()),
    ("PUT", "profile", lambda p, rest, body: api_profile_update(body)),
]