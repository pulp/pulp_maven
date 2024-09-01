#!/bin/bash

# This script expects all <app_label>-api.json files to exist in the plugins root directory.
# It produces a <app_label>-ruby-client.tar file in the plugins root directory.

# WARNING: DO NOT EDIT!
#
# This file was generated by plugin_template, and is managed by it. Please use
# './plugin-template --github pulp_maven' to update this file.
#
# For more info visit https://github.com/pulp/plugin_template

set -mveuo pipefail

# make sure this script runs at the repo root
cd "$(dirname "$(realpath -e "$0")")"/../../..

pushd ../pulp-openapi-generator
rm -rf "pulp_maven-client"

./gen-client.sh "../pulp_maven/maven-api.json" "maven" ruby "pulp_maven"

pushd pulp_maven-client
gem build pulp_maven_client
tar cvf "../../pulp_maven/maven-ruby-client.tar" "./pulp_maven_client-"*".gem"
popd
popd