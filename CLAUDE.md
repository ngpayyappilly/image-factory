# ImageFactory

Golden-image pipeline: build hardened images once with Packer, gate them, sign them, and promote the
same artifact through dev → canary → stable. **AWS (AMIs) is the primary platform.** Azure, GCP and
VMware are optional, off by default, and only partly implemented. Design source:
`ImageFactory Design and Implementation Plan.pdf` (repo root). User docs: `README.md`, `docs/ARCHITECTURE.md` (how the pieces fit, state model, trust model), `docs/PIPELINES.md` (how the CI workflows are designed), `docs/PLATFORMS.md`. Keep PIPELINES.md in step with the workflows and ARCHITECTURE.md in step when you change promotion rules, the manifest, or the trust model.

## Commands

```bash
make setup           # venv + deps (Python >= 3.9; the system python is 3.9, so keep code 3.9-compatible)
make check           # everything that runs offline: pytest, packer validate (all flavor x platform),
                     #   ansible syntax, actionlint, terraform validate. Run before calling work done.
make test            # unit tests only (no cloud access)
.venv/bin/imagefactory --help
make build FLAVOR=al2023 PLATFORM=aws    # REAL build: needs AWS credentials and costs money
```

## Layout

| Path | What |
|---|---|
| `imagefactory/` | CLI + core. `channels.py` (promotion/rollback/retention rules), `release.py` (per-flavor release), `schedule.py` (monthly catch-up), `lock.py`, `gates.py` (evidence → pass/fail), `manifest.py` (manifest + per-platform build records), `store.py` (incl. conditional writes), `signing.py`, `versioning.py`, `config.py`, `cli.py` |
| `imagefactory/platforms/` | One class per cloud behind `base.Platform`. `aws.py`, `azure.py` and `gcp.py` are complete (Azure/GCP drive the `az`/`gcloud` CLI through an injectable runner); `optional.py` (VMware) only maps Packer vars |
| `config/images.yaml` | Single source of truth: flavors, platforms, gate lists, bake days, retention. `exceptions.yaml` holds time-boxed gate exceptions |
| `packer/{linux,windows}/` | One build block, one `source` per platform; select with `-only`. `packer/scripts/` run inside the build VM |
| `ansible/`, `tests/goss/`, `tests/pester/` | Shared hardening and the test suites that produce gate evidence |
| `terraform/aws-bootstrap/` | Bucket, KMS keys, builder/promoter roles, GitHub OIDC trust. `modules/golden-image-aws/` is the consumer module |
| `.github/workflows/`, `pipelines/` | GitHub Actions (preferred; includes `ci.yml` running `make check` on PRs) and Azure DevOps equivalents; keep them in step |
| `scripts/` | `build.sh` (one flavor/platform build), `validate.sh` (offline validation), `assume-role.sh` (ADO), `install-tools.sh` (pinned, checksum-verified Packer + cosign for ADO/self-hosted agents) |

## Invariants: do not break these

- **Build once, promote the artifact.** Nothing in `channels.py` builds; promotion only repoints. Images and signed manifests are immutable; a rebuild always gets a new version (`<flavor>-<YYYYMMDD>.<build>`).
- **Gates are blocking and fail closed.** Missing evidence leaves a gate *unmet*, unparseable evidence is a *fail*, a test run with zero tests is a *fail*. A gate passes only if **every built platform** passes it (`Manifest.refresh_gates`).
- **One writer per object.** Platform builds of a flavor run in parallel, so each writes only its own record (`builds/<flavor>/<version>/<platform>.json`); `release` is the single writer of the manifest (`mf.assemble`). Never add a read-modify-write of a shared object. Versions are reserved atomically (`reserve_version`); channel changes take the `channels-<flavor>` lock.
- **Release is per flavor.** `release_flavor` judges one flavor on its own (records present, pre-release gates green, smoke, sign); never make one flavor's failure block another's release.
- **State is platform-neutral.** Manifests and channel pointers live in the store (S3); platforms only *project* them (AWS: SSM `<prefix>/<flavor>/<channel>/ami_id`). `_set()` projects first, then writes state, so a failed projection leaves state unchanged.
- **Signature covers image identity only** (`Manifest.canonical_bytes` excludes gates, gate_results, signature, deprecated_at). Adding a field that changes what the image *is* changes the signed bytes; adding lifecycle state must be excluded there.
- **Architecture is part of the flavor** (`al2023` vs `al2023arm`), never a build-time switch: versions, channels and manifests stay unambiguous. arm64 is Linux-on-AWS only and `Config.validate` enforces it; `arch` is part of the signed manifest identity. Gate-tool pins are keyed by arch (`tool_urls[arch][tool]`).
- **Optional platforms fail loudly.** Enabling VMware (not yet implemented) must raise `PlatformNotImplemented` at promotion rather than silently skipping. Don't "fix" this by returning empty results.
- **No secrets baked or logged.** Build creds are short-lived (OIDC / assume-role); tool binaries are downloaded only with a pinned SHA256 (`gates.sh`); keep user input out of `run:` scripts in workflows (pass via `env:`).
- **Don't weaken a gate to make something pass.** Use a time-boxed, approved entry in `config/exceptions.yaml` (needs id, gate, reason, approved_by, expires).

## Conventions

- Python 3.9: start modules with `from __future__ import annotations`; no `X | Y` runtime types, no `match`.
- Core logic takes injected collaborators (store, platform clients via `client_factory`, `now`, signing `run`) so it is unit-testable without a cloud. Tests use fakes (`tests/unit/conftest.py`, `FakePlatform`, `LocalStore`); there is no moto/localstack. Add a test with every rule you add or change.
- boto3 is imported lazily; the CLI turns `BotoCoreError`/`ClientError` into `error: ...` + exit 2.
- Config-driven: new flavors/platform settings go in `config/images.yaml`; Packer vars are generated by `imagefactory packer-vars`, so every Packer variable needs a default and a mapping in the platform's `packer_vars`.
- Run `packer fmt -recursive packer` and `terraform fmt` after editing HCL (`make check` fails on unformatted files).
- GitHub Actions are pinned to commit SHAs with a version comment. Resolve SHAs from the real tag (peeled commit), never guess.

## Gotchas learned the hard way

- `cleanup.sh` deletes SSH host keys **before** tests/scans (so scans see what ships), but `sshd -T/-t` refuse to run without one: the goss tests use a throwaway key at `/tmp/imagefactory-tools/hostkey`.
- The build user's SSH key is removed only in `seal.sh` (last provisioner); removing it earlier breaks Packer's next connection.
- AWS SSM parameters are **regional**; channel pointers are written to every region in `platforms.aws.regions`.
- Azure gallery versions must be `Major.Minor.Patch` and GCE names can't contain dots: use `Version.azure` / `Version.gcp`; the canonical string lives in a tag/label.
- `make` resolves simple commands with its own PATH, so Makefile recipes use `$(VENV)/bin/...`.
- Lock and version reservation need S3 conditional writes (boto3 >= 1.36); `LocalStore` emulates them for tests.
- `git ls-remote` shows the tag object for annotated tags; use the `^{}` line for the commit SHA.

## Skills to use

Invoke these with the Skill tool when the task matches, before writing code from memory.

| When | Skill |
|---|---|
| Writing or changing boto3 code (`platforms/aws.py`, `store.py`, anything calling AWS) | `aws-sdk-python-usage` |
| AMIs, EC2 launch/status-check failures, IMDSv2, SSM agent registration, smoke-test debugging | `aws-compute` |
| Inspector/GuardDuty findings, scanner choice, CVE detection, vulnerability gate policy | `aws-security` |
| SLO dashboards, burn-rate alarms, CloudWatch metrics, structured stage events | `aws-observability` |
| Build/storage/replication spend, monthly cost reporting, Free Tier | `aws-billing-and-cost-management` |
| Getting local AWS credentials for a first real build | `signing-in-to-aws` |
| Planning how to test what can't run offline (sandbox-account build, e2e) | `engineering:testing-strategy` |
| Pull-back runbook, post-incident review, blameless postmortem | `engineering:incident-response` |
| Runbooks, release notes, deprecation calendar, onboarding docs | `engineering:documentation` |
| Reviewing a diff, especially IAM, signing, OIDC trust, promotion rules | `code-review`, `security-review` |
| Cleanup after a larger change | `simplify` |
| Before pushing | `no-mistakes` |
| Working near real accounts (destructive commands) | `careful` or `guard` |

Not relevant here (different tools): `aws-cdk`, `aws-cloudformation`, `aws-deployment`. This repo uses Terraform, Packer and GitHub Actions.

## Workflow: every change goes through a pull request

`main` is protected on GitHub (PR required, no direct pushes, no force-push, even for admins; required checks `check` and `unit-tests-py39`). This is a standing decision, so never push to `main` and never try to work around the protection.

1. Branch from an up-to-date `main` (`feat/...`, `fix/...`, `docs/...`, `chore/...`).
2. Run `make check` locally first.
3. Commit with a clear message (end it with the attribution line from the session), push the **branch**, and open the PR with `gh pr create` (end the description with the attribution line). Say what changed, why, and what was and was not verified.
4. Wait for CI to pass. **Do not merge unless the user asks**; the user decides when.
5. Applies to docs and config changes too. Keep PRs focused; one concern per PR.

Committing and pushing still only happens when the user asks for it (or asks for a change that is meant to land). When they do, that means a branch and a PR.

## Ask before doing

Real-cloud and outward-facing actions are never part of routine work here. Confirm first before: `terraform apply`, `make build`, `packer build`, `imagefactory promote|rollback|release|sign`, `imagefactory retention --apply` (deregisters AMIs and deletes snapshots), or anything using real AWS credentials. Dry runs (`retention` without `--apply`, `packer validate`, `terraform validate`) are fine. Don't commit or push unless asked, and never to `main` directly (see the PR workflow above).

## Known gaps (don't assume these exist)

Never run against real AWS. Windows track is an unexecuted scaffold. The VMware runtime plugin and consumer Terraform module are not written. Not built from the plan: daily CVE detection, SLO dashboards/alerts, fleet inventory/drift report, catalog integration, the 6-hourly availability probe, Git tagging of versions. CIS coverage is a hand-written goss subset plus pinned ansible-lockdown roles that have not been exercised; the org's mandated standard/scanner is still an open decision. `CHANGE-ME` values in `config/images.yaml` must be filled in Phase 0.
