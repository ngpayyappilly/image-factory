from imagefactory.platforms.aws import AwsPlatform
from imagefactory.versioning import Version

V = Version.parse("al2023-20261112.1")


class FakeEc2:
    def __init__(self, images):
        self.images, self.deregistered, self.snapshots = images, [], []

    def describe_images(self, ImageIds):
        return {"Images": [i for i in self.images if i["ImageId"] in ImageIds]}

    def deregister_image(self, ImageId):
        self.deregistered.append(ImageId)

    def delete_snapshot(self, SnapshotId):
        self.snapshots.append(SnapshotId)


class FakeSsm:
    def __init__(self):
        self.params = {}

    def put_parameter(self, Name, Value, Type, Overwrite, DataType="text"):
        assert Overwrite
        self.params[Name] = (Value, DataType)


def img(ami, state="available", version=str(V)):
    return {"ImageId": ami, "State": state, "Tags": [{"Key": "imagefactory:version", "Value": version}],
            "BlockDeviceMappings": [{"Ebs": {"SnapshotId": "snap-" + ami}}]}


def make(ec2_by_region, ssm_by_region=None, regions=("us-east-1", "us-west-2")):
    ssm_by_region = ssm_by_region or {}
    factory = lambda svc, region: (ec2_by_region if svc == "ec2" else ssm_by_region)[region]
    return AwsPlatform({"regions": list(regions), "ssm_prefix": "/imagefactory/"}, client_factory=factory)


def test_images_from_packer():
    p = make({})
    assert p.images_from_packer("us-east-1:ami-1,us-west-2:ami-2") == {"us-east-1": "ami-1", "us-west-2": "ami-2"}


def test_verify_available_ok_and_each_failure_mode():
    ec2 = {"us-east-1": FakeEc2([img("ami-1")]), "us-west-2": FakeEc2([img("ami-2")])}
    p = make(ec2)
    assert p.verify_available(V, {"us-east-1": "ami-1", "us-west-2": "ami-2"}) == []

    ec2 = {"us-east-1": FakeEc2([img("ami-1", state="pending")]), "us-west-2": FakeEc2([img("ami-2", version="other")])}
    problems = make(ec2).verify_available(V, {"us-east-1": "ami-1", "us-west-2": "ami-2"})
    assert any("pending" in x for x in problems) and any("tagged" in x for x in problems)

    ec2 = {"us-east-1": FakeEc2([img("ami-1")]), "us-west-2": FakeEc2([])}
    problems = make(ec2).verify_available(V, {"us-east-1": "ami-1"})     # replica region missing entirely
    assert problems == ["us-west-2: no image replicated to this region"]


def test_set_channel_writes_ami_and_version_per_region():
    ssm = {"us-east-1": FakeSsm(), "us-west-2": FakeSsm()}
    make({}, ssm).set_channel("al2023", "stable", V, {"us-east-1": "ami-1", "us-west-2": "ami-2"})
    assert ssm["us-west-2"].params["/imagefactory/al2023/stable/ami_id"] == ("ami-2", "aws:ec2:image")
    assert ssm["us-east-1"].params["/imagefactory/al2023/stable/version"][0] == str(V)


def test_deregister_removes_image_and_snapshots():
    e = FakeEc2([img("ami-1")])
    make({"us-east-1": e}).deregister(V, {"us-east-1": "ami-1"})
    assert e.deregistered == ["ami-1"] and e.snapshots == ["snap-ami-1"]


def test_packer_vars_mapping(cfg):
    out = AwsPlatform(cfg.platform_cfg("aws")).packer_vars(cfg.image("al2023"), V)
    assert out["aws_regions"] == ["us-east-1", "us-west-2"]
    assert out["aws_ssh_user"] == "ec2-user" and out["aws_source_ami_owner"] == "amazon"


class SmokeEc2:
    def __init__(self, status_ok=True):
        self.status_ok, self.launched, self.terminated = status_ok, [], []

    def run_instances(self, **kw):
        self.launched.append(kw)
        return {"Instances": [{"InstanceId": "i-%d" % len(self.launched)}]}

    def get_waiter(self, name):
        assert name == "instance_status_ok"
        ok = self.status_ok

        class W:
            def wait(self, **kw):
                if not ok:
                    raise RuntimeError("Max attempts exceeded")
        return W()

    def terminate_instances(self, InstanceIds):
        self.terminated += InstanceIds


class SmokeSsm:
    def __init__(self, online):
        self.online = online

    def describe_instance_information(self, Filters):
        return {"InstanceInformationList": [{"PingStatus": "Online"}] if self.online else []}


FLAVOR_CFG = {"aws": {}}


def smoke_platform(ec2, ssm=None, **cfg):
    conf = {"regions": ["us-east-1"], "smoke_test": {"subnet_ids": {"us-east-1": "subnet-1"}, "timeout_seconds": 30, **cfg}}
    p = AwsPlatform(conf, client_factory=lambda svc, region: ec2 if svc == "ec2" else ssm)
    p._sleep = lambda s: None
    return p


def test_smoke_pass_boots_imdsv2_and_always_terminates():
    ec2 = SmokeEc2()
    assert smoke_platform(ec2).smoke_test("al2023", V, {"us-east-1": "ami-1"}, FLAVOR_CFG) == []
    assert ec2.launched[0]["MetadataOptions"]["HttpTokens"] == "required"
    assert ec2.terminated == ["i-1"]


def test_smoke_fail_still_terminates():
    ec2 = SmokeEc2(status_ok=False)
    problems = smoke_platform(ec2).smoke_test("al2023", V, {"us-east-1": "ami-1"}, FLAVOR_CFG)
    assert problems and "status checks" in problems[0]
    assert ec2.terminated == ["i-1"]                      # never leak a build-time instance


def test_smoke_join_check_needs_ssm_registration_when_profile_set():
    ec2 = SmokeEc2()
    p = smoke_platform(ec2, SmokeSsm(online=False), instance_profile="imagefactory-smoke")
    problems = p.smoke_test("al2023", V, {"us-east-1": "ami-1"}, FLAVOR_CFG)
    assert problems and "never registered with SSM" in problems[0]
    assert smoke_platform(SmokeEc2(), SmokeSsm(online=True), instance_profile="x").smoke_test(
        "al2023", V, {"us-east-1": "ami-1"}, FLAVOR_CFG) == []


def test_smoke_reports_missing_subnet_for_a_region_instead_of_skipping_it():
    p = smoke_platform(SmokeEc2())
    problems = p.smoke_test("al2023", V, {"us-east-1": "ami-1", "us-west-2": "ami-2"}, FLAVOR_CFG)
    assert problems == ["us-west-2: platforms.aws.smoke_test.subnet_ids has no subnet for this region"]


def test_smoke_instance_type_follows_architecture():
    for arch, expected in (("x86_64", "t3.small"), ("arm64", "t4g.small")):
        ec2 = SmokeEc2()
        smoke_platform(ec2).smoke_test("f", V, {"us-east-1": "ami-1"}, {"arch": arch, "aws": {}})
        assert ec2.launched[0]["InstanceType"] == expected
    ec2 = SmokeEc2()                                    # an explicit per-flavor override wins
    smoke_platform(ec2).smoke_test("f", V, {"us-east-1": "ami-1"}, {"arch": "arm64", "aws": {"smoke_instance_type": "c7g.large"}})
    assert ec2.launched[0]["InstanceType"] == "c7g.large"
