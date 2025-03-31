# Changelog

[//]: # (You should *NOT* be adding new change log entries to this file, this)
[//]: # (file is managed by towncrier. You *may* edit previous change logs to)
[//]: # (fix problems like typo corrections or such.)
[//]: # (To add a new change log entry, please see the contributing docs.)
[//]: # (WARNING: Don't drop the towncrier directive!)

[//]: # (towncrier release notes start)

## 0.10.0 (2025-03-31) {: #0.10.0 }

#### Features {: #0.10.0-feature }

- Added domain support to the `mvn deploy` API.
  [#258](https://github.com/pulp/pulp_maven/issues/258)

---

## 0.9.0 (2025-02-27) {: #0.9.0 }

#### Features {: #0.9.0-feature }

- Adds domain support for the Pulp API. No support for maven deploy (yet).
  [#244](https://github.com/pulp/pulp_maven/issues/244)

#### Misc {: #0.9.0-misc }

- [#234](https://github.com/pulp/pulp_maven/issues/234), [#239](https://github.com/pulp/pulp_maven/issues/239)

---

## 0.8.3 (2025-02-25) {: #0.8.3 }

No significant changes.

---

## 0.8.2 (2025-02-06) {: #0.8.2 }

No significant changes.

---

## 0.8.1 (2024-06-20) {: #0.8.1 }


No significant changes.

---

## 0.8.0 (2023-12-20) {: #0.8.0 }

### Features

-   Changed the deploy api to use non blocking immediate tasks.

---

## 0.7.0 (2023-11-02) {: #0.7.0 }

### Features

-   Made plugin compatible with pulpcore 3.40+.
    [#172](https://pulp.plan.io/issues/172)

---

## 0.6.0 (2023-05-17) {: #0.6.0 }

### Features

-   Added ability to add cached content to a repository.
    [#136](https://pulp.plan.io/issues/136)
-   Updated pulpcore compatibility to >=3.25.0,<3.40.
    [#153](https://pulp.plan.io/issues/153)

---

## 0.5.0 (2023-03-17) {: #0.5.0 }

### Features

-   Added ability to add cached content to a repository.
    [#136](https://pulp.plan.io/issues/136)

---

## 0.4.0 (2023-03-15) {: #0.4.0 }

### Features

-   Added ability to upload projects to repositories using the Maven Deploy Plugin.
    [#115](https://pulp.plan.io/issues/115)

### Misc

-   [#111](https://pulp.plan.io/issues/111)

---

## 0.3.3 (2022-06-22) {: #0.3.3 }

No significant changes.

---

## 0.3.2 (2021-12-16) {: #0.3.2 }

### Bugfixes

-   Added view_name to DetailRelatedField to prevent a warning.
    [#8678](https://pulp.plan.io/issues/8678)

---

0.3.1 (2021-06-28)
Misc
---

-   [#8979](https://pulp.plan.io/issues/8979)

---

## 0.3.0 (2021-05-26) {: #0.3.0 }

### Misc

-   [#8745](https://pulp.plan.io/issues/8745)

---

## 0.2.0 (2021-02-03) {: #0.2.0 }

-   Added compatibility with pulpcore 3.10.

---

## 0.1.0 (2020-02-11) {: #0.1.0 }

### Improved Documentation

-   Change the prefix of Pulp services from pulp-* to pulpcore-*
    [#4554](https://pulp.plan.io/issues/4554)

### Deprecations and Removals

-   Change _id, _created, _last_updated, _href to pulp_id, pulp_created, pulp_last_updated, pulp_href
    [#5457](https://pulp.plan.io/issues/5457)

-   Sync is no longer available at the {remote_href}/sync/ repository={repo_href} endpoint. Instead, use POST {repo_href}/sync/ remote={remote_href}.

    Creating / listing / editing / deleting file repositories is now performed on /pulp/api/v3/maven/maven/ instead of /pulp/api/v3/repositories/. Only Maven content can be present in a Maven repository, and only a Maven repository can hold Maven content.
    [#5625](https://pulp.plan.io/issues/5625)

### Misc

-   [#5580](https://pulp.plan.io/issues/5580), [#5625](https://pulp.plan.io/issues/5625), [#5701](https://pulp.plan.io/issues/5701)

---

## 0.1.0b3 (2019-09-11)

### Bugfixes

-   Updates serializers and tests to make plugin compatible with pulpcore-plugin 0.1.0rc4.
    [#5217](https://pulp.plan.io/issues/5217)

### Misc

-   [#4681](https://pulp.plan.io/issues/4681)
