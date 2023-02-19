#!/bin/bash

# WARNING: DO NOT EDIT!
#
# This file was generated by plugin_template, and is managed by it. Please use
# './plugin-template --github pulp_maven' to update this file.
#
# For more info visit https://github.com/pulp/plugin_template

# make sure this script runs at the repo root
cd "$(dirname "$(realpath -e "$0")")"/../..

set -uv

MATCHES=$(grep -n -r --include \*.py "_(f")

if [ $? -ne 1 ]; then
  printf "\nERROR: Detected mix of f-strings and gettext:\n"
  echo "$MATCHES"
  exit 1
fi
