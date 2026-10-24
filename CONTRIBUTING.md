# Contributing to ImageFactory

Thanks for helping. You do not need a cloud account to contribute: most of the project is testable offline.

## Get set up

```bash
git clone https://github.com/ngpayyappilly/image-factory && cd image-factory
make setup     # venv + dependencies (Python >= 3.9; keep your code 3.9-compatible)
make check     # pytest, packer validate (every flavor x platform), ansible syntax, actionlint, terraform validate
```

`make check` is what CI runs. It needs `packer`, `terraform`, `actionlint` and `ansible` on your PATH; `make test` runs only the unit tests.

## Making a change

1. Fork the repo and branch from an up-to-date `main` (`feat/...`, `fix/...`, `docs/...`, `chore/...`).
2. Keep the change focused: one concern per pull request, and do not stack pull requests on each other.
3. Add a test with every rule you add or change. Tests use fakes (`tests/unit/conftest.py`, `FakePlatform`, `LocalStore`); there is no cloud in the unit tests.
4. Run `make check`. After editing HCL, run `packer fmt -recursive packer` and `terraform fmt` first, because `make check` fails on unformatted files.
5. Open a pull request against `main` and say what changed, why, and what you did and did not verify. `main` is protected: the `check` and `unit-tests-py39` jobs must pass, and nobody pushes to it directly.

## Rules that must not be broken

The full list, with the reasons, is in [CLAUDE.md](CLAUDE.md) under "Invariants". The short version:

- **Build once, promote the artifact.** Promotion only repoints a channel; it never builds. A rebuild always gets a new version.
- **Gates fail closed.** Missing evidence is unmet, unparseable evidence is a failure, zero tests is a failure. Do not weaken a gate to make something pass; use a time-boxed, approved entry in `config/exceptions.yaml`.
- **One writer per object.** Never add a read-modify-write of a shared object in the store.
- **No secrets baked into images or logged.** Keep user input out of `run:` steps in workflows (pass it through `env:`).
- **Optional platforms fail loudly.** VMware is not implemented and must raise `PlatformNotImplemented` rather than silently skip.

Other conventions: modules start with `from __future__ import annotations`; new flavors and platform settings go in `config/images.yaml`; GitHub Actions are pinned to the commit SHA of a real tag, with a version comment.

## What to work on

The README lists where help is most wanted. Issues labelled [good first issue](https://github.com/ngpayyappilly/image-factory/labels/good%20first%20issue) are the smallest starting points. The most valuable contribution is running a flavor on a real cloud account and reporting what breaks.

Anything that touches a real cloud account (`make build`, `packer build`, `terraform apply`, `imagefactory promote|rollback|release|sign`) costs money and should never run against an account you do not own. Say in the pull request which of these you ran and which you could only verify offline.

## Reporting bugs and security problems

Use the issue templates for bugs and feature requests. For a vulnerability, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

Please also read the [Code of Conduct](CODE_OF_CONDUCT.md).
