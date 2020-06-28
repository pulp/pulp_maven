#!/usr/bin/env bash

# WARNING: DO NOT EDIT!
#
# This file was generated by plugin_template, and is managed by it. Please use
# './plugin-template --travis pulp_maven' to update this file.
#
# For more info visit https://github.com/pulp/plugin_template

set -euv

source .travis/utils.sh

export PRE_BEFORE_SCRIPT=$TRAVIS_BUILD_DIR/.travis/pre_before_script.sh
export POST_BEFORE_SCRIPT=$TRAVIS_BUILD_DIR/.travis/post_before_script.sh

if [[ -f $PRE_BEFORE_SCRIPT ]]; then
  source $PRE_BEFORE_SCRIPT
fi

# Developers often want to know the final pulp config
echo "PULP CONFIG:"
tail -v -n +1 .travis/settings/settings.* ~/.config/pulp_smash/settings.json

if [[ "$TEST" == 'pulp' || "$TEST" == 'performance' || "$TEST" == 's3' ]]; then
  # Many functional tests require these
  cmd_prefix dnf install -yq lsof which dnf-plugins-core
fi

if [[ -f $POST_BEFORE_SCRIPT ]]; then
  source $POST_BEFORE_SCRIPT
fi
