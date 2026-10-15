"""Conditional writes, the lock built on them, and catch-up scheduling."""
import datetime as dt
import json
import threading

import pytest

from imagefactory import cli, lock, schedule
from imagefactory import manifest as mf
from imagefactory.store import LocalStore, S3Store


# --- conditional writes -------------------------------------------------------------------

def test_local_store_conditional_writes(tmp_path):
    s = LocalStore(str(tmp_path))
    assert s.put_if_absent("k.json", {"v": 1}) is True
    assert s.put_if_absent("k.json", {"v": 2}) is False                  # create-if-absent: second creator loses
    obj, etag = s.get_with_etag("k.json")
    assert obj == {"v": 1}
    assert s.put_if_match("k.json", {"v": 3}, etag) is True
    assert s.put_if_match("k.json", {"v": 4}, etag) is False             # stale etag: the replace is refused
    assert s.get("k.json") == {"v": 3}
    assert s.get_with_etag("missing.json") == (None, None)


def test_local_store_list_is_recursive_like_s3(tmp_path):
    s = LocalStore(str(tmp_path))
    for k in ("builds/f/v1/aws.json", "builds/f/v1/azure.json", "builds/f/v2/aws.json", "manifests/f/v1.json"):
        s.put(k, {})
    assert s.list("builds/f/") == ["builds/f/v1/aws.json", "builds/f/v1/azure.json", "builds/f/v2/aws.json"]


def test_only_one_of_many_concurrent_creators_wins(tmp_path):
    s, wins = LocalStore(str(tmp_path)), []
    ts = [threading.Thread(target=lambda i=i: wins.append(s.put_if_absent("lock.json", {"who": i}))) for i in range(16)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert wins.count(True) == 1


class FakeS3:
    """Just enough S3 to check how S3Store maps conditional-write failures."""

    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self, code=None):
        self.code, self.calls = code, []

    def put_object(self, **kw):
        self.calls.append(kw)
        if self.code:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": self.code, "Message": "x"}}, "PutObject")


def test_s3_store_conditional_writes_use_native_preconditions():
    s3 = FakeS3()
    st = S3Store("b", "p", client=s3)
    assert st.put_if_absent("k", {"a": 1}) and s3.calls[-1]["IfNoneMatch"] == "*"
    assert st.put_if_match("k", {"a": 1}, '"etag1"') and s3.calls[-1]["IfMatch"] == '"etag1"'
    assert s3.calls[-1]["Key"] == "p/k" and s3.calls[-1]["ServerSideEncryption"] == "aws:kms"


@pytest.mark.parametrize("code", ["PreconditionFailed", "ConditionalRequestConflict"])
def test_s3_store_reports_a_lost_race_as_false(code):
    assert S3Store("b", client=FakeS3(code)).put_if_absent("k", {}) is False


def test_s3_store_does_not_swallow_real_errors():
    from botocore.exceptions import ClientError
    with pytest.raises(ClientError):
        S3Store("b", client=FakeS3("AccessDenied")).put_if_absent("k", {})


# --- lock ---------------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.t = dt.datetime(2026, 11, 12, 6, tzinfo=dt.timezone.utc)

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += dt.timedelta(seconds=s)


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path))


def test_lock_is_exclusive_and_reentrant_for_its_owner(store):
    c = Clock()
    lock.acquire(store, "build", "run-1", 3600, now=c, sleep=c.sleep)
    with pytest.raises(lock.LockBusy, match="run-1"):
        lock.acquire(store, "build", "run-2", 3600, now=c, sleep=c.sleep)
    lock.acquire(store, "build", "run-1", 3600, now=c, sleep=c.sleep)       # a retried step of the same run


def test_release_frees_the_lock_and_only_for_the_holder(store):
    c = Clock()
    lock.acquire(store, "build", "run-1", 3600, now=c, sleep=c.sleep)
    assert lock.release(store, "build", "run-2", now=c) is False            # not yours
    with pytest.raises(lock.LockBusy):
        lock.acquire(store, "build", "run-2", 3600, now=c, sleep=c.sleep)
    assert lock.release(store, "build", "run-1", now=c) is True
    assert lock.release(store, "build", "run-1", now=c) is False            # idempotent
    lock.acquire(store, "build", "run-2", 3600, now=c, sleep=c.sleep)


def test_a_killed_run_cannot_block_forever(store):
    c = Clock()
    lock.acquire(store, "build", "run-1", 600, now=c, sleep=c.sleep)        # run-1 dies without releasing
    c.sleep(601)
    lock.acquire(store, "build", "run-2", 600, now=c, sleep=c.sleep)        # expired: takeover works


def test_waiting_for_a_busy_lock_polls_then_succeeds_or_times_out(store):
    c = Clock()
    lock.acquire(store, "build", "run-1", 120, now=c, sleep=c.sleep)
    lock.acquire(store, "build", "run-2", 600, wait_seconds=300, now=c, sleep=c.sleep)    # run-1 expires while we wait
    with pytest.raises(lock.LockBusy):
        lock.acquire(store, "build", "run-3", 600, wait_seconds=60, now=c, sleep=c.sleep)


def test_lock_names_are_validated(store):
    with pytest.raises(ValueError):
        lock.acquire(store, "../escape", "o", 60)


def test_lock_cli_returns_a_distinct_exit_code_when_busy(tmp_path, capsys):
    import yaml
    raw = yaml.safe_load(open("config/images.yaml"))
    raw["store"] = {"type": "local", "local_path": str(tmp_path / "s")}
    (tmp_path / "c.yaml").write_text(yaml.safe_dump(raw))
    run = lambda *a: cli.main(["--config", str(tmp_path / "c.yaml"), "lock", *a])
    assert run("acquire", "--name", "build", "--owner", "run-1") == 0
    assert run("acquire", "--name", "build", "--owner", "run-2") == 3
    assert "busy" in capsys.readouterr().err
    assert run("release", "--name", "build", "--owner", "run-1") == 0
    assert run("acquire", "--name", "build", "--owner", "run-2") == 0


# --- catch-up scheduling ------------------------------------------------------------------
# Build day in November 2026 is Thursday the 12th (Patch Tuesday the 10th + 2 business days).

def put_build(store, version, released=False):
    flavor = version.split("-")[0]
    store.put(mf.part_key(flavor, version, "aws"), {"version": version})
    if released:
        store.put(mf.manifest_key(flavor, version), {"version": version, "signature": "sig"})


D = lambda day: dt.date(2026, 11, day)


def test_not_due_before_the_build_day(store):
    assert schedule.status(store, "al2023", D(11), 3).state == schedule.WAIT


def test_due_on_the_build_day_and_every_day_after_until_released(store):
    assert schedule.status(store, "al2023", D(12), 3).state == schedule.DUE
    assert schedule.status(store, "al2023", D(25), 3).state == schedule.DUE        # a delayed or missed run is caught up


def test_a_failed_build_is_retried_on_the_next_trigger_then_capped(store):
    put_build(store, "al2023-20261112.1")                                           # attempt 1: never released
    assert schedule.status(store, "al2023", D(13), 3).state == schedule.DUE
    put_build(store, "al2023-20261113.1")
    assert schedule.status(store, "al2023", D(14), 3).state == schedule.DUE
    put_build(store, "al2023-20261116.1")
    st = schedule.status(store, "al2023", D(17), 3)
    assert st.state == schedule.EXHAUSTED and st.attempts == 3                      # stop burning builds, raise an alert


def test_a_released_build_this_month_completes_the_cycle(store):
    put_build(store, "al2023-20261113.1", released=True)
    assert schedule.status(store, "al2023", D(14), 3).state == schedule.DONE
    assert schedule.status(store, "rhel9", D(14), 3).state == schedule.DUE          # per flavor: others are unaffected


def test_a_build_before_the_build_day_does_not_count(store):
    put_build(store, "al2023-20261103.1", released=True)                           # an early manual rebuild
    assert schedule.status(store, "al2023", D(12), 3).state == schedule.DUE


def test_a_manual_critical_cve_build_after_the_build_day_satisfies_the_month(store):
    put_build(store, "al2023-20261118.1", released=True)
    assert schedule.status(store, "al2023", D(19), 3).state == schedule.DONE


def test_plan_scheduled_cli(tmp_path, capsys, monkeypatch):
    import yaml
    raw = yaml.safe_load(open("config/images.yaml"))
    raw["store"] = {"type": "local", "local_path": str(tmp_path / "s")}
    (tmp_path / "c.yaml").write_text(yaml.safe_dump(raw))
    plan = lambda day: cli.main(["--config", str(tmp_path / "c.yaml"), "plan", "--flavors", "al2023,rhel9", "--scheduled",
                                 "--date", "2026-11-%02d" % day, "--ado-variable", "matrix"])
    out = lambda: capsys.readouterr().out
    assert plan(11) == 0
    text = out()
    assert "variable=build;isOutput=true]false" in text
    assert plan(12) == 0
    assert "variable=build;isOutput=true]true" in out()
    # al2023 now has 3 failed attempts; rhel9 released: al2023 is exhausted and reported, rhel9 is done.
    st = LocalStore(str(tmp_path / "s"))
    for d in (12, 13, 16):
        put_build(st, "al2023-202611%02d.1" % d)
    put_build(st, "rhel9-20261113.1", released=True)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "gh"))
    assert cli.main(["--config", str(tmp_path / "c.yaml"), "plan", "--flavors", "al2023,rhel9", "--scheduled",
                     "--date", "2026-11-17", "--github-output"]) == 0
    gh = dict(l.split("=", 1) for l in (tmp_path / "gh").read_text().splitlines())
    assert gh["any"] == "false" and gh["exhausted"] == "al2023" and json.loads(gh["matrix"]) == {}
