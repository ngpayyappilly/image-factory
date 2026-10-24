SHELL := /bin/bash
VENV  := .venv
export PATH := $(CURDIR)/$(VENV)/bin:$(PATH)

.PHONY: setup build-setup test validate lint-actions tf-validate check build
setup:  ## create the venv and install dependencies
	python3 -m venv $(VENV) && $(VENV)/bin/pip install -q --upgrade pip && $(VENV)/bin/pip install -q -e '.[dev]' ansible-core actionlint-py

# Real builds need ansible-core 2.18 (Python >= 3.11); the dev venv may be older, so builds use their own.
PYTHON_BUILD ?= python3.12
build-setup:  ## venv for real builds (Python >= 3.11, pinned ansible-core)
	$(PYTHON_BUILD) -m venv .venv-build && .venv-build/bin/pip install -q --upgrade pip && .venv-build/bin/pip install -q -e . -r ansible/requirements-pip.txt

test:  ## unit tests (no cloud access needed)
	$(VENV)/bin/python -m pytest -q

validate:  ## packer validate for every flavor x platform, ansible syntax check
	scripts/validate.sh
	bash -n scripts/*.sh packer/scripts/linux/*.sh
	$(VENV)/bin/ansible-playbook --syntax-check ansible/linux.yml -i localhost, -e flavor=al2023

lint-actions:  ## GitHub Actions workflows
	$(VENV)/bin/actionlint .github/workflows/*.yml

tf-validate:
	@for d in terraform/aws-bootstrap terraform/modules/golden-image-aws terraform/azure-bootstrap terraform/modules/golden-image-azure; do \
	  echo "== $$d"; (cd $$d && terraform init -backend=false -input=false >/dev/null && terraform validate && terraform fmt -check) || exit 1; done

check: test validate lint-actions tf-validate  ## everything that can run offline

# make build FLAVOR=al2023 PLATFORM=aws   (needs AWS credentials; VERSION defaults to the next one)
FLAVOR ?= al2023
PLATFORM ?= aws
VERSION ?= $(shell $(VENV)/bin/imagefactory version-next --flavor $(FLAVOR))
build:  ## uses .venv-build when present (see build-setup)
	PATH="$$( [ -d .venv-build ] && echo $(CURDIR)/.venv-build/bin: )$$PATH" scripts/build.sh $(FLAVOR) $(PLATFORM) $(VERSION)
