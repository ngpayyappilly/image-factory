import copy
import datetime as dt

import pytest

from imagefactory import manifest as mf
from imagefactory.channels import ChannelService
from imagefactory.config import Config
from imagefactory.platforms.base import Platform
from imagefactory.signing import SigningError
from imagefactory.store import LocalStore

GATES = ["goss", "compliance", "vuln_scan", "secrets", "sbom", "smoke"]


class FakePlatform(Platform):
    name = "aws"
    packer_source = "amazon-ebs"

    def __init__(self):
        super().__init__({})
        self.channels = {}          # (flavor, channel) -> version
        self.problems = []
        self.deregistered = []

    def packer_vars(self, flavor_cfg, version):
        return {}

    def images_from_packer(self, artifact_id, flavor, version):
        return {}

    def verify_available(self, version, images):
        return list(self.problems)

    def set_channel(self, flavor, channel, version, images):
        self.channels[(flavor, channel)] = str(version)

    def deregister(self, version, images):
        self.deregistered.append(str(version))


class Clock:
    def __init__(self, day):
        self.t = dt.datetime(day.year, day.month, day.day, 12, tzinfo=dt.timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, days):
        self.t += dt.timedelta(days=days)


@pytest.fixture
def cfg():
    return Config.load()


@pytest.fixture
def env(tmp_path, cfg):
    store = LocalStore(str(tmp_path))
    plat = FakePlatform()
    clock = Clock(dt.date(2026, 11, 12))     # a Thursday
    signed_ok = {"value": True}

    def verify(m):
        if not (m.signature and signed_ok["value"]):
            raise SigningError("bad signature")

    svc = ChannelService(cfg, store, {"aws": plat}, verify, now=clock)

    class Env:
        pass

    e = Env()
    e.cfg, e.store, e.plat, e.clock, e.svc, e.signed_ok = cfg, store, plat, clock, svc, signed_ok

    def make(version, gates=GATES, signed=True, author="alice"):
        m = mf.Manifest(version=version, flavor="al2023", baseline_version="1.0.0", commit="abc", pipeline_run="1",
                        author=author, created_at=mf.iso(clock()), images={"aws": {"us-east-1": "ami-" + version}})
        for g in gates:
            m.record_gate(g, "pass")
        m.signature = "sig" if signed else None
        mf.save(store, m)
        return m

    e.make = make
    return e
