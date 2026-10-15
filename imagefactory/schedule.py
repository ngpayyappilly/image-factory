"""Is this flavor's monthly build due?

The build day is two business days after Patch Tuesday. Instead of acting only on that exact day, a flavor
stays *due* from the build day until a build has been released that month, so a failed, skipped or delayed
run is retried by the next daily trigger. Retries are capped so a persistent failure alerts instead of
burning builds.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from . import manifest as mf
from .store import Store
from .versioning import Version, scheduled_build_date

WAIT, DUE, DONE, EXHAUSTED = "wait", "due", "done", "exhausted"


@dataclass
class ScheduleStatus:
    state: str
    attempts: int
    build_date: dt.date

    def describe(self) -> str:
        return {
            WAIT: "waiting for the build day (%s)" % self.build_date,
            DUE: "due (build day %s, %d attempt(s) so far)" % (self.build_date, self.attempts),
            DONE: "already released this month",
            EXHAUSTED: "%d attempt(s) since %s and none released" % (self.attempts, self.build_date),
        }[self.state]


def status(store: Store, flavor: str, today: dt.date, max_attempts: int) -> ScheduleStatus:
    build_date = scheduled_build_date(today.year, today.month)
    if today < build_date:
        return ScheduleStatus(WAIT, 0, build_date)
    since = build_date.strftime("%Y%m%d")
    attempts, released = 0, False
    for v in mf.used_versions(store, flavor):
        try:
            ver = Version.parse(v)
        except ValueError:
            continue
        if ver.date < since:
            continue
        attempts += 1                                   # every started build counts, released or not
        m = store.get(mf.manifest_key(flavor, v))
        released = released or bool(m and m.get("signature"))
    state = DONE if released else EXHAUSTED if attempts >= max_attempts else DUE
    return ScheduleStatus(state, attempts, build_date)
