#!/usr/bin/env bash

# WARNING: DO NOT EDIT!
#
# This file was generated by plugin_template, and is managed by it. Please use
# './plugin-template --github pulp_maven' to update this file.
#
# For more info visit https://github.com/pulp/plugin_template

# make sure this script runs at the repo root
cd "$(dirname "$(realpath -e "$0")")"/../../..
REPO_ROOT="$PWD"

set -euv

source .github/workflows/scripts/utils.sh

if [[ "$TEST" = "docs" || "$TEST" = "publish" ]]; then
  pip install -r ../pulpcore/doc_requirements.txt
  pip install -r doc_requirements.txt
fi

pip install -r functest_requirements.txt

cd .ci/ansible/

TAG=ci_build
if [[ "$TEST" == "plugin-from-pypi" ]]; then
  PLUGIN_NAME=pulp_maven
elif [[ "${RELEASE_WORKFLOW:-false}" == "true" ]]; then
  PLUGIN_NAME=./pulp_maven/dist/pulp_maven-$PLUGIN_VERSION-py3-none-any.whl
else
  PLUGIN_NAME=./pulp_maven
fi
if [[ "${RELEASE_WORKFLOW:-false}" == "true" ]]; then
  # Install the plugin only and use published PyPI packages for the rest
  # Quoting ${TAG} ensures Ansible casts the tag as a string.
  cat >> vars/main.yaml << VARSYAML
image:
  name: pulp
  tag: "${TAG}"
plugins:
  - name: pulpcore
    source: pulpcore~=3.12.0
  - name: pulp_maven
    source:  "${PLUGIN_NAME}"
services:
  - name: pulp
    image: "pulp:${TAG}"
    volumes:
      - ./settings:/etc/pulp
VARSYAML
else
  cat >> vars/main.yaml << VARSYAML
image:
  name: pulp
  tag: "${TAG}"
plugins:
  - name: pulp_maven
    source: "${PLUGIN_NAME}"
  - name: pulpcore
    source: ./pulpcore
services:
  - name: pulp
    image: "pulp:${TAG}"
    volumes:
      - ./settings:/etc/pulp
VARSYAML
fi

cat >> vars/main.yaml << VARSYAML
pulp_settings: null
VARSYAML

ansible-playbook build_container.yaml
ansible-playbook start_container.yaml

echo ::group::PIP_LIST
cmd_prefix bash -c "pip3 list && pip3 install pipdeptree && pipdeptree"
echo ::endgroup::
