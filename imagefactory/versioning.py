"""Image versions and the build calendar.

Version format: <flavor>-<YYYYMMDD>.<build>, e.g. rhel9-20261110.1. The flavor already
carries the OS major, so the plan's <os>-<major> collapses into one token.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Iterable

VERSION_RE = re.compile(r"^(?P<flavor>[a-z][a-z0-9]*)-(?P<date>\d{8})\.(?P<build>\d+)$")


@dataclass(frozen=True, order=True)
class Version:
    flavor: str
    date: str
    build: int

    @classmethod
    def parse(cls, s: str) -> "Version":
        m = VERSION_RE.match(s)
        if not m:
            raise ValueError("invalid image version %r (want <flavor>-<YYYYMMDD>.<build>)" % s)
        return cls(m["flavor"], m["date"], int(m["build"]))

    def __str__(self) -> str:
        return "%s-%s.%d" % (self.flavor, self.date, self.build)

    # Some platforms cannot store the canonical string; the canonical version is always
    # kept in a tag/label/metadata field so lookups stay unambiguous.
    @property
    def azure(self) -> str:
        """Azure Compute Gallery versions must be Major.Minor.Patch integers."""
        return "%s.%s.%d" % (self.date[:4], self.date[4:], self.build)

    @property
    def gcp(self) -> str:
        """GCE image names allow lowercase letters, digits and hyphens only."""
        return "%s-%s-%d" % (self.flavor, self.date, self.build)


def next_version(flavor: str, today: dt.date, existing: Iterable[str]) -> Version:
    date = today.strftime("%Y%m%d")
    builds = []
    for s in existing:
        try:
            v = Version.parse(s)
        except ValueError:
            continue
        if v.flavor == flavor and v.date == date:
            builds.append(v.build)
    return Version(flavor, date, max(builds, default=0) + 1)


# --- business-day calendar (weekends only; org holidays are not modelled) -------------

def add_business_days(d: dt.date, n: int) -> dt.date:
    while n > 0:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def business_days_between(start: dt.date, end: dt.date) -> int:
    """Whole business days elapsed after `start` up to and including `end`."""
    n, d = 0, start
    while d < end:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def patch_tuesday(year: int, month: int) -> dt.date:
    first = dt.date(year, month, 1)
    first_tuesday = first + dt.timedelta(days=(1 - first.weekday()) % 7)
    return first_tuesday + dt.timedelta(days=7)


def scheduled_build_date(year: int, month: int, lag_business_days: int = 2) -> dt.date:
    return add_business_days(patch_tuesday(year, month), lag_business_days)


def is_build_day_for(today: dt.date) -> bool:
    """True on the monthly scheduled build day (two business days after Patch Tuesday)."""
    return today == scheduled_build_date(today.year, today.month)
