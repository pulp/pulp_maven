=========
Changelog
=========

..
    You should *NOT* be adding new change log entries to this file, this
    file is managed by towncrier. You *may* edit previous change logs to
    fix problems like typo corrections or such.
    To add a new change log entry, please see
    https://docs.pulpproject.org/en/3.0/nightly/contributing/git.html#changelog-update

    WARNING: Don't drop the next directive!

.. towncrier release notes start

0.6.0 (2023-05-17)
==================

Features
--------

- Added ability to add cached content to a repository.
  `#136 <https://pulp.plan.io/issues/136>`_
- Updated pulpcore compatibility to >=3.25.0,<3.40.
  `#153 <https://pulp.plan.io/issues/153>`_


----


0.5.0 (2023-03-17)
==================

Features
--------

- Added ability to add cached content to a repository.
  `#136 <https://pulp.plan.io/issues/136>`_


----


0.4.0 (2023-03-15)
==================

Features
--------

- Added ability to upload projects to repositories using the Maven Deploy Plugin.
  `#115 <https://pulp.plan.io/issues/115>`_


Misc
----

- `#111 <https://pulp.plan.io/issues/111>`_


----


0.3.3 (2022-06-22)
==================

No significant changes.


----


0.3.2 (2021-12-16)
==================

Bugfixes
--------

- Added view_name to DetailRelatedField to prevent a warning.
  `#8678 <https://pulp.plan.io/issues/8678>`_


----


0.3.1 (2021-06-28)
Misc
----

- `#8979 <https://pulp.plan.io/issues/8979>`_


----


0.3.0 (2021-05-26)
==================

Misc
----

- `#8745 <https://pulp.plan.io/issues/8745>`_


----


0.2.0 (2021-02-03)
==================

- Added compatibility with pulpcore 3.10.


----


0.1.0 (2020-02-11)
==================


Improved Documentation
----------------------

- Change the prefix of Pulp services from pulp-* to pulpcore-*
  `#4554 <https://pulp.plan.io/issues/4554>`_


Deprecations and Removals
-------------------------

- Change `_id`, `_created`, `_last_updated`, `_href` to `pulp_id`, `pulp_created`, `pulp_last_updated`, `pulp_href`
  `#5457 <https://pulp.plan.io/issues/5457>`_
- Sync is no longer available at the {remote_href}/sync/ repository={repo_href} endpoint. Instead, use POST {repo_href}/sync/ remote={remote_href}.

  Creating / listing / editing / deleting file repositories is now performed on /pulp/api/v3/maven/maven/ instead of /pulp/api/v3/repositories/. Only Maven content can be present in a Maven repository, and only a Maven repository can hold Maven content.
  `#5625 <https://pulp.plan.io/issues/5625>`_


Misc
----

- `#5580 <https://pulp.plan.io/issues/5580>`_, `#5625 <https://pulp.plan.io/issues/5625>`_, `#5701 <https://pulp.plan.io/issues/5701>`_


----


0.1.0b3 (2019-09-11)
====================


Bugfixes
--------

- Updates serializers and tests to make plugin compatible with pulpcore-plugin 0.1.0rc4.
  `#5217 <https://pulp.plan.io/issues/5217>`_


Misc
----

- `#4681 <https://pulp.plan.io/issues/4681>`_
