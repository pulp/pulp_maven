# Deploy a project to Pulp

Users can use the Maven Deploy Plugin to deploy a project to Pulp.

At this time this feature does not use authentication.
There is also no distinction between SNAPSHOT and release repositories.

## 1. Create a new Maven repository

=== "run"

    ```bash
    pulp maven repository create --name maven-releases
    ```

=== "output"

    ```json
    {
      "pulp_href": "/pulp/api/v3/repositories/maven/maven/2f8d4e8f-7bdb-4a77-8832-6f3d1089d063/",
      "pulp_created": "2023-03-10T21:00:05.426515Z",
      "versions_href": "/pulp/api/v3/repositories/maven/maven/2f8d4e8f-7bdb-4a77-8832-6f3d1089d063/versions/",
      "pulp_labels": {},
      "latest_version_href": "/pulp/api/v3/repositories/maven/maven/2f8d4e8f-7bdb-4a77-8832-6f3d1089d063/versions/0/",
      "name": "maven-releases",
      "description": null,
      "retain_repo_versions": null,
      "remote": null
    }
    ```

## 2. Create a Maven Distribution

The distribution serves will serve your repository in `http://<host-name>/pulp/content/maven-releases/`.

=== "run"

    ```bash
    pulp maven distribution create --name maven-releases --repository maven-releases --base-path maven-releases
    ```

=== "output"

    ```json
    {
      "pulp_href": "/pulp/api/v3/distributions/maven/maven/e881cbc7-036e-41ac-a17d-4e8741cd92ee/",
      "pulp_created": "2023-03-10T21:05:30.218854Z",
      "base_path": "maven-releases",
      "base_url": "http://pulp-hostname/pulp/content/maven-releases/",
      "content_guard": null,
      "pulp_labels": {},
      "name": "maven-releases",
      "repository": "/pulp/api/v3/repositories/maven/maven/2f8d4e8f-7bdb-4a77-8832-6f3d1089d063/",
      "remote": null
    }
    ```

## 3. Configure your project with the new repository

Add the following stanza to the `pom.xml` in your Maven project.

```xml title="pom.xml"
<distributionManagement>
  <repository>
    <id>pulp</id>
    <name>Nexus Releases</name>
    <url>http://pulp-hostname/pulp/maven/maven-releases/</url>
  </repository>
  <snapshotRepository>
    <id>pulp</id>
    <name>Nexus Snapshot</name>
    <url>http://pulp-hostname/pulp/maven/maven-releases/</url>
  </snapshotRepository>
</distributionManagement>
```

## 4. Deploy the project

=== "run"

    ```bash
    mvn deploy
    ```

=== "output"

    ```bash
    [INFO] Scanning for projects...
    [INFO]
    [INFO] -------------< org.sonatype.nexus.examples:simple-project >-------------
    [INFO] Building simple-project 1.0.0
    [INFO] --------------------------------[ jar ]---------------------------------
    [INFO]
    [INFO] --- maven-resources-plugin:2.6:resources (default-resources) @ simple-project ---
    [INFO] Using 'UTF-8' encoding to copy filtered resources.
    [INFO] skip non existing resourceDirectory /home/dkliban/devel/nexus-book-examples/maven/simple-project/src/main/resources
    [INFO]
    [INFO] --- maven-compiler-plugin:3.8.1:compile (default-compile) @ simple-project ---
    [INFO] Nothing to compile - all classes are up to date
    [INFO]
    [INFO] --- maven-resources-plugin:2.6:testResources (default-testResources) @ simple-project ---
    [INFO] Using 'UTF-8' encoding to copy filtered resources.
    [INFO] skip non existing resourceDirectory /home/dkliban/devel/nexus-book-examples/maven/simple-project/src/test/resources
    [INFO]
    [INFO] --- maven-compiler-plugin:3.8.1:testCompile (default-testCompile) @ simple-project ---
    [INFO] Nothing to compile - all classes are up to date
    [INFO]
    [INFO] --- maven-surefire-plugin:2.12.4:test (default-test) @ simple-project ---
    [INFO] Surefire report directory: /home/dkliban/devel/nexus-book-examples/maven/simple-project/target/surefire-reports

    -------------------------------------------------------
     T E S T S
    -------------------------------------------------------
    Running org.sonatype.nexus.examples.AppTest
    Tests run: 1, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 0.012 sec

    Results :

    Tests run: 1, Failures: 0, Errors: 0, Skipped: 0

    [INFO]
    [INFO] --- maven-jar-plugin:2.4:jar (default-jar) @ simple-project ---
    [INFO] Building jar: /home/dkliban/devel/nexus-book-examples/maven/simple-project/target/simple-project-1.0.0.jar
    [INFO]
    [INFO] --- maven-install-plugin:2.4:install (default-install) @ simple-project ---
    [INFO] Installing /home/dkliban/devel/nexus-book-examples/maven/simple-project/target/simple-project-1.0.0.jar to /home/dkliban/.m2/repository/org/sonatype/nexus/examples/simple-project/1.0.0/simple-project-1.0.0.jar
    [INFO] Installing /home/dkliban/devel/nexus-book-examples/maven/simple-project/pom.xml to /home/dkliban/.m2/repository/org/sonatype/nexus/examples/simple-project/1.0.0/simple-project-1.0.0.pom
    [INFO]
    [INFO] --- maven-deploy-plugin:2.7:deploy (default-deploy) @ simple-project ---
    Uploading to pulp: http://pulp-hostname/pulp/maven/maven-releases/org/sonatype/nexus/examples/simple-project/1.0.0/simple-project-1.0.0.jar
    Uploaded to pulp: http://pulp-hostname/pulp/maven/maven-releases/org/sonatype/nexus/examples/simple-project/1.0.0/simple-project-1.0.0.jar (3.4 kB at 1.0 kB/s)
    Uploading to pulp: http://pulp-hostname/pulp/maven/maven-releases/org/sonatype/nexus/examples/simple-project/1.0.0/simple-project-1.0.0.pom
    Uploaded to pulp: http://pulp-hostname/pulp/maven/maven-releases/org/sonatype/nexus/examples/simple-project/1.0.0/simple-project-1.0.0.pom (5.5 kB at 1.7 kB/s)
    Downloading from pulp: http://pulp-hostname/pulp/maven/maven-releases/org/sonatype/nexus/examples/simple-project/maven-metadata.xml
    Uploading to pulp: http://pulp-hostname/pulp/maven/maven-releases/org/sonatype/nexus/examples/simple-project/maven-metadata.xml
    Uploaded to pulp: http://pulp-hostname/pulp/maven/maven-releases/org/sonatype/nexus/examples/simple-project/maven-metadata.xml (321 B at 99 B/s)
    [INFO] ------------------------------------------------------------------------
    [INFO] BUILD SUCCESS
    [INFO] ------------------------------------------------------------------------
    [INFO] Total time:  10.839 s
    [INFO] Finished at: 2023-03-10T16:13:06-05:00
    [INFO] ------------------------------------------------------------------------
    ```

## Concurrent deploys

Each deployed file is added to the repository by a task that holds the repository while it runs.
An upload that arrives while another one holds it waits for its turn, for up to
`MAVEN_DEPLOY_TASK_TIMEOUT` seconds (30 by default), and only then answers `429`. Maven, Gradle and
sbt do not retry a refused upload, so raise the timeout if many builds publish into one repository
at the same time.

## Deploying in batches

By default every deployed file, checksum files included, becomes its own repository version. A
build publishes about a dozen files per module, so a release of a multi-module project leaves
hundreds of versions, each of which regenerates `maven-metadata.xml` and the index pages and stores
the list of the repository's content.

With `MAVEN_DEPLOY_BATCH = True` in the Pulp settings, a deployed file is recorded as pending and
the upload answers at once. A task then adds everything pending to the repository in one version,
once no file has been deployed for `MAVEN_DEPLOY_BATCH_LINGER` seconds (1 by default), and at the
latest `MAVEN_DEPLOY_BATCH_MAX_WAIT` seconds (10 by default) after the oldest pending file.

What clients see does not change:

- A pending file is served by the repository's distribution as soon as its upload is answered, so a
  build can resolve what it has just published.
- When the same path is deployed twice within a batch, the newer file is kept.
- A `maven-metadata.xml` deployed by the client within a batch is kept rather than regenerated, as
  it is when it arrives in a version of its own. This keeps SNAPSHOT versions deployed under
  timestamped file names resolvable.

What changes is that several files share one repository version, so versions no longer correspond
to single uploads.
