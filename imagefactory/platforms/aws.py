"""AWS: AMIs built by Packer amazon-ebs, channels projected into SSM Parameter Store.

Per region, per flavor, per channel:
    <prefix>/<flavor>/<channel>/ami_id    plain AMI id (data type aws:ec2:image), so launch
                                          templates and Terraform can use resolve:ssm:<name>
    <prefix>/<flavor>/<channel>/version   canonical version string
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List

from ..versioning import Version
from .base import Platform


# Default build instance per CPU architecture (override per flavor with aws.instance_type).
DEFAULT_BUILD_INSTANCE = {"x86_64": "t3.medium", "arm64": "t4g.medium"}
DEFAULT_SMOKE_INSTANCE = {"x86_64": "t3.small", "arm64": "t4g.small"}
DEB_ARCH = {"x86_64": "amd64", "arm64": "arm64"}


class AwsPlatform(Platform):
    name = "aws"
    packer_source = "amazon-ebs"

    def __init__(self, config: Dict[str, Any], client_factory: Callable[[str, str], Any] = None):
        super().__init__(config)
        self._factory = client_factory or self._boto_client
        self._sleep = time.sleep
        self.prefix = config.get("ssm_prefix", "/imagefactory").rstrip("/")

    @staticmethod
    def _boto_client(service: str, region: str):
        import boto3
        return boto3.client(service, region_name=region)

    def param(self, flavor: str, channel: str, leaf: str) -> str:
        return "%s/%s/%s/%s" % (self.prefix, flavor, channel, leaf)

    # --- build ------------------------------------------------------------------------
    def packer_vars(self, flavor_cfg, version):
        a = flavor_cfg["aws"]
        c = self.config
        arch = flavor_cfg.get("arch", "x86_64")
        out = {
            "aws_regions": c["regions"],
            # {arch} is the EC2 name (x86_64|arm64); {deb_arch} the Debian/Ubuntu name (amd64|arm64) that Canonical's AMI names use.
            "aws_source_ami_name": a["source_ami_name"].replace("{arch}", arch).replace("{deb_arch}", DEB_ARCH[arch]),
            "aws_source_ami_owner": a["source_ami_owner"],
            "aws_ssh_user": a.get("ssh_user", ""),
            "aws_instance_type": a.get("instance_type") or DEFAULT_BUILD_INSTANCE[arch],
            "aws_ami_users": [str(x) for x in c.get("share_with_accounts", [])],
            "aws_vpc_id": c.get("build_vpc_id", ""),
            "aws_subnet_id": c.get("build_subnet_id", ""),
            "aws_ssh_interface": c.get("ssh_interface", ""),
            "aws_sg_source_public_ip": bool(c.get("restrict_build_sg_to_runner_ip", False)),
            "aws_kms_key_id": c.get("ebs_kms_key_id", ""),
            "aws_region_kms_key_ids": c.get("region_kms_key_ids", {}),
        }
        return out

    def images_from_packer(self, artifact_id, flavor, version):
        # "us-east-1:ami-0abc,us-west-2:ami-0def"
        return dict(p.split(":", 1) for p in artifact_id.split(",") if p)

    # --- distribution -----------------------------------------------------------------
    def verify_available(self, version, images):
        problems = []
        for region, ami in sorted(images.items()):
            ec2 = self._factory("ec2", region)
            r = ec2.describe_images(ImageIds=[ami])["Images"]
            if not r:
                problems.append("%s: %s not found" % (region, ami))
                continue
            img = r[0]
            if img["State"] != "available":
                problems.append("%s: %s is %s, not available" % (region, ami, img["State"]))
            tags = {t["Key"]: t["Value"] for t in img.get("Tags", [])}
            if tags.get("imagefactory:version") != str(version):
                problems.append("%s: %s is tagged %r, expected %s"
                                % (region, ami, tags.get("imagefactory:version"), version))
        missing = set(self.config["regions"]) - set(images)
        problems += ["%s: no image replicated to this region" % r for r in sorted(missing)]
        return problems

    # --- smoke test: boot-and-join from the distributed AMI, in every region ------------------
    def smoke_test(self, flavor, version, images, flavor_cfg):
        """Launch the AMI, wait for EC2 status checks and (if an instance profile is configured)
        for SSM registration, which is the 'join' check on AWS. Instances are always terminated."""
        cfg = self.config.get("smoke_test") or {}
        subnets = cfg.get("subnet_ids") or {}
        arch = flavor_cfg.get("arch", "x86_64")
        itype = (flavor_cfg["aws"].get("smoke_instance_type")
                 or (cfg.get("instance_types") or {}).get(arch) or DEFAULT_SMOKE_INSTANCE[arch])
        profile, timeout = cfg.get("instance_profile", ""), int(cfg.get("timeout_seconds", 600))
        problems = []
        for region, ami in sorted(images.items()):
            if region not in subnets:
                problems.append("%s: platforms.aws.smoke_test.subnet_ids has no subnet for this region" % region)
                continue
            problems += ["%s: %s" % (region, p) for p in
                         self._smoke_one(region, ami, str(version), itype, subnets[region], profile, timeout)]
        return problems

    def _smoke_one(self, region, ami, version, itype, subnet, profile, timeout):
        ec2 = self._factory("ec2", region)
        kw = {"ImageId": ami, "InstanceType": itype, "MinCount": 1, "MaxCount": 1, "SubnetId": subnet,
              "MetadataOptions": {"HttpTokens": "required", "HttpEndpoint": "enabled"},
              "TagSpecifications": [{"ResourceType": "instance", "Tags": [
                  {"Key": "imagefactory:managed", "Value": "true"},
                  {"Key": "imagefactory:smoke-test", "Value": version},
                  {"Key": "Name", "Value": "imagefactory-smoke-" + version}]}]}
        if profile:
            kw["IamInstanceProfile"] = {"Name": profile}
        iid = ec2.run_instances(**kw)["Instances"][0]["InstanceId"]
        try:
            try:
                ec2.get_waiter("instance_status_ok").wait(
                    InstanceIds=[iid], WaiterConfig={"Delay": 15, "MaxAttempts": max(1, timeout // 15)})
            except Exception as e:                      # WaiterError: status checks never went ok
                return ["%s did not pass EC2 status checks: %s" % (iid, e)]
            if profile and not self._ssm_online(region, iid, timeout):
                return ["%s booted but never registered with SSM within %ds" % (iid, timeout)]
            return []
        finally:
            ec2.terminate_instances(InstanceIds=[iid])

    def _ssm_online(self, region, iid, timeout):
        ssm = self._factory("ssm", region)
        for _ in range(max(1, timeout // 15)):
            info = ssm.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": [iid]}])["InstanceInformationList"]
            if info and info[0].get("PingStatus") == "Online":
                return True
            self._sleep(15)
        return False

    # --- channels ---------------------------------------------------------------------
    def set_channel(self, flavor, channel, version, images):
        for region, ami in sorted(images.items()):
            ssm = self._factory("ssm", region)
            ssm.put_parameter(Name=self.param(flavor, channel, "ami_id"), Value=ami, Type="String",
                              DataType="aws:ec2:image", Overwrite=True)
            ssm.put_parameter(Name=self.param(flavor, channel, "version"), Value=str(version),
                              Type="String", Overwrite=True)

    # --- retention --------------------------------------------------------------------
    def deregister(self, version, images):
        for region, ami in sorted(images.items()):
            ec2 = self._factory("ec2", region)
            found = ec2.describe_images(ImageIds=[ami])["Images"]
            if not found:
                continue
            snapshots = [b["Ebs"]["SnapshotId"] for b in found[0].get("BlockDeviceMappings", []) if "Ebs" in b]
            ec2.deregister_image(ImageId=ami)
            for s in snapshots:
                ec2.delete_snapshot(SnapshotId=s)
