# WARNING: DO NOT EDIT!
#
# This file was generated by plugin_template, and is managed by it. Please use
# './plugin-template --travis pulp_maven' to update this file.
#
# For more info visit https://github.com/pulp/plugin_template

import glob
import os
import re
import requests
import subprocess
import sys

KEYWORDS = ["fixes", "closes", "re", "ref"]
NO_ISSUE = "[noissue]"
STATUSES = ["NEW", "ASSIGNED", "POST", "MODIFIED"]
REDMINE_URL = "https://pulp.plan.io"
CHANGELOG_EXTS = [".feature", ".bugfix", ".doc", ".removal", ".misc"]

sha = sys.argv[1]
project = ""
message = subprocess.check_output(["git", "log", "--format=%B", "-n 1", sha]).decode("utf-8")


def __check_status(issue):
    response = requests.get(f"{REDMINE_URL}/issues/{issue}.json")
    response.raise_for_status()
    bug_json = response.json()
    status = bug_json["issue"]["status"]["name"]
    if status not in STATUSES:
        sys.exit(
            "Error: issue #{issue} has invalid status of {status}. Status must be one of "
            "{statuses}.".format(issue=issue, status=status, statuses=", ".join(STATUSES))
        )

    if project:
        project_id = bug_json["issue"]["project"]["id"]
        project_json = requests.get(f"{REDMINE_URL}/projects/{project_id}.json").json()
        if project_json["project"]["identifier"] != project:
            sys.exit(f"Error: issue {issue} is not in the {project} project.")


def __check_changelog(issue):
    matches = glob.glob(f"CHANGES/**/{issue}.*", recursive=True)

    if len(matches) < 1:
        sys.exit(f"Could not find changelog entry in CHANGES/ for {issue}.")
    for match in matches:
        if os.path.splitext(match)[1] not in CHANGELOG_EXTS:
            sys.exit(f"Invalid extension for changelog entry '{match}'.")


print("Checking commit message for {sha}.".format(sha=sha[0:7]))

# validate the issue attached to the commit
regex = r"(?:{keywords})[\s:]+#(\d+)".format(keywords=("|").join(KEYWORDS))
pattern = re.compile(regex)

issues = pattern.findall(message)

if issues:
    for issue in pattern.findall(message):
        __check_status(issue)
        __check_changelog(issue)
else:
    if NO_ISSUE in message:
        print("Commit {sha} has no issues but is tagged {tag}.".format(sha=sha[0:7], tag=NO_ISSUE))
    else:
        sys.exit(
            "Error: no attached issues found for {sha}. If this was intentional, add "
            " '{tag}' to the commit message.".format(sha=sha[0:7], tag=NO_ISSUE)
        )

print("Commit message for {sha} passed.".format(sha=sha[0:7]))
