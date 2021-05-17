#!/bin/bash

# WARNING: DO NOT EDIT!
#
# This file was generated by plugin_template, and is managed by it. Please use
# './plugin-template --github pulp_maven' to update this file.
#
# For more info visit https://github.com/pulp/plugin_template

# make sure this script runs at the repo root
cd "$(dirname "$(realpath -e "$0")")"/../..

set -euv

export PULP_URL="${PULP_URL:-http://pulp}"

export VERSION=$(http pulp/pulp/api/v3/status/ | jq --arg plugin maven --arg legacy_plugin pulp_maven -r '.versions[] | select(.component == $plugin or .component == $legacy_plugin) | .version')
export response=$(curl --write-out %{http_code} --silent --output /dev/null https://pypi.org/project/pulp-maven/$VERSION/)
if [ "$response" == "200" ];
then
  echo "pulp_maven $VERSION has already been released. Skipping."
  exit
fi

pip install twine

python3 setup.py sdist bdist_wheel --python-tag py3
twine check dist/* || exit 1
twine upload dist/* -u pulp -p $PYPI_PASSWORD
