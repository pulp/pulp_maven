#!/bin/bash

# WARNING: DO NOT EDIT!
#
# This file was generated by plugin_template, and is managed by it. Please use
# './plugin-template --github pulp_maven' to update this file.
#
# For more info visit https://github.com/pulp/plugin_template

set -euv

# make sure this script runs at the repo root
cd "$(dirname "$(realpath -e "$0")")"/../../..

VERSION="$1"

if [[ -z "$VERSION" ]]; then
  echo "No version specified."
  exit 1
fi

RESPONSE="$(curl --write-out '%{http_code}' --silent --output /dev/null "https://pypi.org/project/pulp-maven/$VERSION/")"

if [ "$RESPONSE" == "200" ];
then
  echo "pulp_maven $VERSION has already been released. Skipping."
  exit
fi

twine upload -u __token__ -p "$PYPI_API_TOKEN" \
dist/pulp?maven-"$VERSION"-py3-none-any.whl \
dist/pulp?maven-"$VERSION".tar.gz \
;
