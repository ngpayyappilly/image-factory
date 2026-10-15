SHELL := /bin/bash
VENV  := .venv
export PATH := $(CURDIR)/$(VENV)/bin:$(PATH)

.PHONY: setup test validate lint-actions tf-validate check build
setup:  ## create the venv and install dependencies
	python3 -m venv $(VENV) && $(VENV)/bin/pip install -q --upgrade pip && $(VENV)/bin/pip install -q -e '.[dev]' ansible-core actionlint-py

test:  ## unit tests (no cloud access needed)
	$(VENV)/bin/python -m pytest -q

validate:  ## packer validate for every flavor x platform, ansible syntax check
	scripts/validate.sh
	bash -n scripts/*.sh packer/scripts/linux/*.sh
	$(VENV)/bin/ansible-playbook --syntax-check ansible/linux.yml -i localhost, -e flavor=al2023

lint-actions:  ## GitHub Actions workflows
	$(VENV)/bin/actionlint .github/workflows/*.yml

tf-validate:
	@for d in terraform/aws-bootstrap terraform/modules/golden-image-aws terraform/azure-bootstrap terraform/modules/golden-image-azure terraform/gcp-bootstrap terraform/modules/golden-image-gcp; do \
	  echo "== $$d"; (cd $$d && terraform init -backend=false -input=false >/dev/null && terraform validate && terraform fmt -check) || exit 1; done

check: test validate lint-actions tf-validate  ## everything that can run offline

# make build FLAVOR=al2023 PLATFORM=aws   (needs AWS credentials; VERSION defaults to the next one)
FLAVOR ?= al2023
PLATFORM ?= aws
VERSION ?= $(shell $(VENV)/bin/imagefactory version-next --flavor $(FLAVOR))
build:
	scripts/build.sh $(FLAVOR) $(PLATFORM) $(VERSION)
