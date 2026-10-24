# Security policy

ImageFactory builds and signs images that other people run, so security reports are taken seriously.

## Reporting a vulnerability

Please do **not** open a public issue. Report it privately through GitHub:
[Report a vulnerability](https://github.com/ngpayyappilly/image-factory/security/advisories/new).

Include what you found, how to reproduce it, and which flavor, platform or workflow it affects. You will get an acknowledgement, and the maintainer will keep you updated until it is fixed or ruled out. Please give a reasonable time to fix before disclosing publicly.

## What counts

Examples of things to report: a way to promote an image that failed a gate, to forge or bypass the signed manifest, to read or write another channel's state, secrets or credentials leaking into an image or a log, an overly broad IAM, OIDC or KMS policy in `terraform/`, or a workflow that lets untrusted input reach a `run:` step.

## Supported versions

There are no tagged releases yet; only the current `main` is supported.

## Out of scope

Findings in the base images themselves (Amazon Linux, RHEL, Ubuntu) belong to their vendors. Problems caused by running the pipeline against an account you do not control are not a vulnerability in this project.
