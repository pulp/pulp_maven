from setuptools import find_packages, setup

with open("README.rst") as f:
    long_description = f.read()

with open("requirements.txt") as requirements:
    requirements = requirements.readlines()

setup(
    name="pulp-maven",
    version="0.8.2.dev",
    description="pulp-maven plugin for the Pulp Project",
    long_description=long_description,
    long_description_content_type="text/x-rst",
    license="GPLv2+",
    author="Pulp Project Developers",
    author_email="pulp-dev@redhat.com",
    url="http://www.pulpproject.org/",
    python_requires=">=3.8",
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
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
    ],
    entry_points={"pulpcore.plugin": ["pulp_maven = pulp_maven:default_app_config"]},
)
