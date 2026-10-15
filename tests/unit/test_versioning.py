import datetime as dt

import pytest

from imagefactory.versioning import (Version, add_business_days, business_days_between, is_build_day_for,
                                     next_version, patch_tuesday, scheduled_build_date)


def test_parse_roundtrip_and_ordering():
    v = Version.parse("rhel9-20261110.1")
    assert (v.flavor, v.date, v.build) == ("rhel9", "20261110", 1)
    assert str(v) == "rhel9-20261110.1"
    assert Version.parse("rhel9-20261110.10") > Version.parse("rhel9-20261110.9")  # numeric, not lexical


@pytest.mark.parametrize("bad", ["rhel9", "RHEL9-20261110.1", "rhel9-2026110.1", "rhel9-20261110", "rhel9-20261110.x"])
def test_parse_rejects(bad):
    with pytest.raises(ValueError):
        Version.parse(bad)


def test_platform_specific_forms():
    v = Version.parse("rhel9-20261110.2")
    assert v.azure == "2026.1110.2"        # Compute Gallery needs Major.Minor.Patch
    assert v.gcp == "rhel9-20261110-2"     # GCE names cannot contain dots


def test_next_version_increments_per_day_and_flavor():
    d = dt.date(2026, 11, 10)
    existing = ["rhel9-20261110.1", "rhel9-20261110.2", "rhel9-20261109.7", "al2023-20261110.5", "junk"]
    assert str(next_version("rhel9", d, existing)) == "rhel9-20261110.3"
    assert str(next_version("al2023", d, existing)) == "al2023-20261110.6"
    assert str(next_version("win2022", d, existing)) == "win2022-20261110.1"


def test_patch_tuesday_is_second_tuesday():
    assert patch_tuesday(2026, 11) == dt.date(2026, 11, 10)   # the plan's example date
    assert patch_tuesday(2026, 10) == dt.date(2026, 10, 13)
    assert all(patch_tuesday(2026, m).weekday() == 1 for m in range(1, 13))


def test_scheduled_build_is_two_business_days_after_patch_tuesday():
    assert scheduled_build_date(2026, 11) == dt.date(2026, 11, 12)
    assert is_build_day_for(dt.date(2026, 11, 12))
    assert not is_build_day_for(dt.date(2026, 11, 11))


def test_business_days():
    fri = dt.date(2026, 10, 9)
    assert add_business_days(fri, 1) == dt.date(2026, 10, 12)       # skips the weekend
    assert business_days_between(fri, dt.date(2026, 10, 16)) == 5
    assert business_days_between(fri, fri) == 0
