# Bootstrapping, testing and tearing down on AWS

The procedure that was run for real (one account, one region), with what it found. Placeholders in `<angle brackets>`; keep real account IDs, buckets
and subnets in gitignored files (`config/*.local.yaml`, `*.local.env`, `*.tfvars`). For design see [ARCHITECTURE.md](ARCHITECTURE.md).

**Costs for a test:** about $3/month for the three KMS keys while they exist, a few cents per build (a `t3.medium` for the build, a `t3.small` for the boot test),
and a few cents/month per AMI snapshot. **Confirm the target account first**: the bootstrap creates IAM roles, KMS keys, a bucket and the account's GitHub OIDC
provider (only one can exist per account). Prefer a member account over an Organization's management account.

## 1. Bootstrap

```bash
cd terraform/aws-bootstrap
cat > local.tfvars <<'EOF'                      # gitignored (*.tfvars)
region      = "<region>"
bucket_name = "imagefactory-<account-id>-<region>"
# Who may assume the builder/promoter roles locally. For an SSO session this is the ROLE ARN WITH ITS PATH, not the session ARN:
#   aws iam list-roles --path-prefix /aws-reserved/sso.amazonaws.com/ --query "Roles[].Arn"
builder_trusted_principal_arns  = ["arn:aws:iam::<account-id>:role/aws-reserved/sso.amazonaws.com/<region>/AWSReservedSSO_<PermissionSet>_<hash>"]
promoter_trusted_principal_arns = ["<same>"]
github_repository = "<org>/<repo>"              # enables GitHub OIDC trust (creates the account's OIDC provider)
EOF
terraform init && terraform plan -var-file=local.tfvars -out=bootstrap.plan     # review: expect 17 to add, 0 to change/destroy
terraform apply bootstrap.plan                                                  # state is LOCAL (gitignored): keep it
```

## 2. Local config and tool pins

Generate `config/images.local.yaml` from `config/images.yaml` and override `store.bucket/region`, `platforms.aws.regions`, `ebs_kms_key_id: alias/imagefactory-ebs`,
`restrict_build_sg_to_runner_ip: true`, and `smoke_test.subnet_ids` (a default-VPC subnet is fine). Use **one region** while testing.
Pin goss, trivy and syft per CPU architecture in `tool-pins.local.env` (`TOOL_URLS_JSON`, `TOOL_SHA256_JSON`); compute the SHA256 of each downloaded archive yourself
and compare it with the publisher's digest.

## 3. Build

```bash
make build-setup                                # .venv-build: Python >= 3.11 and the pinned ansible-core
export IMAGEFACTORY_CONFIG=config/images.local.yaml; set -a; source tool-pins.local.env; set +a
V=$(imagefactory plan --flavors al2023 | python3 -c 'import json,sys; print(list(json.load(sys.stdin).values())[0]["version"])')   # reserves a version
caffeinate -dimsu make build FLAVOR=al2023 PLATFORM=aws VERSION=$V             # ~55 min from the US to ap-south-1
```

Run it with `caffeinate`, or in CI: **a sleeping laptop silently kills a long build** (see Findings). Use a credentials profile that assumes the builder role
with `source_profile` + `role_arn`; a statically assumed role from an SSO session expires after one hour.

**Fast loop for the stages after Ansible:** copy the local config with `images.al2023.cis_role: ""` (about 15 minutes). Those images are debug-only: never release them.

## 4. Release and verify

```bash
export PATH="$PWD/.tools/bin:$PATH"             # a pinned cosign (see scripts/install-tools.sh; on macOS download the darwin binary and verify its checksum)
imagefactory release --matrix '{"al2023_aws":{"flavor":"al2023","platform":"aws","version":"'$V'"}}' --run-id local
```

Check it in AWS rather than trusting the log: the AMI (`describe-images --owners self`: `ImdsSupport v2.0`, tags, snapshot encrypted with the CMK), the manifest in S3
(`signed`, all gates), the signature verifying **as the promoter role**, and the SSM pointer (read it as an admin: the builder can only write `dev`). A consumer launch
can be checked with `ec2 run-instances --dry-run --image-id resolve:ssm:/imagefactory/<flavor>/dev/ami_id`.

## 5. Tear down

Order matters; delete by explicit ID, and have each AMI report its own snapshot (do not pair them by list order).

1. **Stop builds** with SIGINT (`pkill -INT -f "packer build"`): Packer then terminates its instance and removes its security group and key pair.
2. **AMIs and their snapshots**: for each AMI, read its snapshot id, then `deregister-image` and `delete-snapshot`.
3. **SSM parameters** under `/imagefactory` (written by the CLI, not Terraform).
4. **Empty the bucket including every object version** (it is versioned, so `terraform destroy` fails on a non-empty bucket).
5. `terraform plan -destroy -out=...`, check it lists exactly the 17 resources, then apply that plan.
6. Verify nothing remains (roles, policies, OIDC provider, buckets, aliases, parameters, instances, AMIs, snapshots, volumes).

**KMS keys are deleted only after their waiting period** (30 days here); they show as `PendingDeletion`, and `aws kms cancel-key-deletion` undoes it until then.

## Findings from the first real run

Everything below passed offline checks and still failed (or was wrong) against the real service. Each is fixed or recorded in `CLAUDE.md`'s gotchas.

| Found | Why it matters |
|---|---|
| The unpinned AL2023 AMI filter matched three kernel streams created seconds apart | The base image was non-deterministic. The kernel stream is now pinned |
| The CIS roles need collections and `ansible-core` >= 2.16.1 (Python >= 3.11) | A build failed 2 minutes in. They are pinned, with a preflight check |
| The CIS role aborts on EC2 (`4.6.6`, root password) | Documented deviation in `ansible/vars/cis_al2023.yml` |
| A custom Packer `execute_command` dropped `environment_vars` | The tool pins and `FLAVOR` never reached the gate script |
| A directory upload onto a missing directory wrote every file onto one file | Goss never ran. The directory is created first |
| `gate-ingest` exited 0 with **no** evidence | The download nests the directory name. It now searches recursively and fails when evidence is missing |
| goss test files were malformed (`exit-status`, `exists`, regex delimiters) | Found only by running goss. A static test now checks the rules |
| cosign uploaded every signature to the **public** Sigstore log | Disabled (`--tlog-upload=false`); verification no longer depends on it |
| SSH latency (about 340 ms) makes every Ansible task about 14 s | A build is about 55 min from the US to Mumbai; build near the runner |
| The laptop slept mid-build | The SSH session broke and the build hung silently. Use `caffeinate` or CI |
| Stacked PRs: #6's fixes were merged into a branch that had already been merged | They never reached `main`. Do not stack PRs |
