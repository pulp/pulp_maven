#!/usr/bin/env python3

from setuptools import find_packages, setup

with open("requirements.txt") as requirements:
    requirements = requirements.readlines()

setup(
    name="pulp-maven",
    version="0.2.0",
    description="pulp-maven plugin for the Pulp Project",
    license="GPLv2+",
    author="Pulp Project Developers",
    author_email="pulp-dev@redhat.com",
    url="http://www.pulpproject.org/",
    python_requires=">=3.6",
    install_requires=requirements,
    include_package_data=True,
    packages=find_packages(exclude=["tests", "tests.*"]),
    classifiers=[
        "License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)",
        "Operating System :: POSIX :: Linux",
        "Development Status :: 4 - Beta",
        "Framework :: Django",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
    ],
    entry_points={"pulpcore.plugin": ["pulp_maven = pulp_maven:default_app_config"]},
)
