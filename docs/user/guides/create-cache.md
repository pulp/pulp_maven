# Cache a Maven Repository

Pulp Maven can be used to cache packages from Maven Central or any other repository on the internet.

The commands below use the `pulp-cli-maven` package available on PyPI.

## 1. Create a new Maven Remote

=== "run"

    ```bash
    pulp maven remote create --name maven-central --url https://repo1.maven.org/maven2/
    ```

=== "output"

    ```json
    {
      "pulp_href": "/pulp/api/v3/remotes/maven/maven/a0554b43-d229-4aba-b106-bd9f41eddd31/",
      "pulp_created": "2023-03-10T20:28:08.573718Z",
      "name": "maven-central",
      "url": "https://repo1.maven.org/maven2/",
      "ca_cert": null,
      "client_cert": null,
      "tls_validation": true,
      "proxy_url": null,
      "pulp_labels": {},
      "pulp_last_updated": "2023-03-10T20:28:08.573744Z",
      "download_concurrency": null,
      "max_retries": null,
      "policy": "immediate",
      "total_timeout": null,
      "connect_timeout": null,
      "sock_connect_timeout": null,
      "sock_read_timeout": null,
      "headers": null,
      "rate_limit": null,
      "hidden_fields": [
        {
          "name": "client_key",
          "is_set": false
        },
        {
          "name": "proxy_username",
          "is_set": false
        },
        {
          "name": "proxy_password",
          "is_set": false
        },
        {
          "name": "username",
          "is_set": false
        },
        {
          "name": "password",
          "is_set": false
        }
      ]
    }
    ```

## 2. Create a Maven Repository

The repository will be used to store content initially cached by pulpcore-content.

You don't have to specify a remote on it, but adding one now will enable you to not have to specify one each time you want to add newly cached content to a repository.

=== "run"

    ```bash
    pulp maven repository create --name maven-central --remote maven-central
    ```

=== "output"

    ```json
    {
      "pulp_href": "/pulp/api/v3/repositories/maven/maven/550b4240-4d1a-4d98-811d-ce7fbbab81c8/",
      "pulp_created": "2023-03-16T10:10:18.047792Z",
      "versions_href": "/pulp/api/v3/repositories/maven/maven/550b4240-4d1a-4d98-811d-ce7fbbab81c8/versions/",
      "pulp_labels": {},
      "latest_version_href": "/pulp/api/v3/repositories/maven/maven/550b4240-4d1a-4d98-811d-ce7fbbab81c8/versions/0/",
      "name": "maven-central",
      "description": null,
      "retain_repo_versions": null,
      "remote": "/pulp/api/v3/remotes/maven/maven/a0554b43-d229-4aba-b106-bd9f41eddd31/"
    }
    ```

## 3. Create a Maven Distribution

Create a distribution with a defined remote.

Doing this ensures that when clients request content from that distribution, `pulpcore-content` will stream the content from the remote to the client.
During that process the content is saved into Pulp.

Adding the repository to the distribution will enable users to browse the HTML listing pages served by `pulpcore-content`.
However, the content will not be displayed there until the cached content is added to the repository as described in the next steps.

=== "run"

    ```bash
    pulp maven distribution create --name maven-central --remote maven-central --repository maven-central --base-path maven-central
    ```

=== "output"

    ```json
    Started background task /pulp/api/v3/tasks/627488da-5375-4827-9424-5b75b1c880d1/
    .Done.
    {
      "pulp_href": "/pulp/api/v3/distributions/maven/maven/1c70eb04-7229-44a2-bf74-b8a94f461b73/",
      "pulp_created": "2023-03-10T20:30:04.487734Z",
      "base_path": "maven-central",
      "base_url": "http://pulp-hostname/pulp/content/maven-central/",
      "content_guard": null,
      "pulp_labels": {},
      "name": "maven-central",
      "repository": "/pulp/api/v3/repositories/maven/maven/550b4240-4d1a-4d98-811d-ce7fbbab81c8/",
      "remote": "/pulp/api/v3/remotes/maven/maven/a0554b43-d229-4aba-b106-bd9f41eddd31/"
    }
    ```

## 4. Add Pulp as a mirror for Maven

In your `~/.m2/settings.xml` add Pulp as a mirror of Maven Central. The URL comes from the
`base_url` attribute of the Maven Distribution.

```xml title="~/.m2/settings.xml"
<settings>
  <mirrors>
    <mirror>
      <id>pulp-maven-central</id>
      <name>Local Maven Central mirror </name>
      <url>http://pulp-hostname/pulp/content/maven-central/</url>
      <mirrorOf>central</mirrorOf>
    </mirror>
  </mirrors>
</settings>
```

## 5. Add cached content to a repository

Whenever content is initially cached by Pulp in the above scenario, it does not belong to any
repository. Pulp considers such content an orphan after 24 hours. At that point an Orphan Cleanup
task would remove the cached content from Pulp. Adding the cached content to a repository would
prevent the cleanup from happening. The following command will create a new repository version
by adding all Maven content that was created from a remote associated with the repository since
the last repository version was created.

=== "run"

    ```bash
    pulp maven repository add-cached-content --name maven-central
    ```

=== "output"

    ```
    Started background task /pulp/api/v3/tasks/2459cf00-3c67-4dd7-bff2-35acd72f584f/
    Done.
    ```
