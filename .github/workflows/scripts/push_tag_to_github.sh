#!/bin/sh
set -e

remote_repo=https://${GITHUB_ACTOR}:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git

git push "${remote_repo}" $1
