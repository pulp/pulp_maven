# Cache a Maven Repository

Pulp Maven can be used to cache packages from Maven Central or any other repository on the internet.

When a client requests content from a distribution with a remote configured, Pulp streams the
content from the remote and automatically saves it into the associated repository. This means
cached content is immediately available and protected from orphan cleanup without any additional
steps.

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

The repository will be used to store cached content. When content is fetched via pull-through
caching, it is automatically added to this repository.

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
During that process the content is automatically saved into Pulp and added to the repository, creating a new repository version for each new content unit.

Adding the repository to the distribution will enable users to browse the HTML listing pages served by `pulpcore-content`.

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

Once your Maven client resolves packages through this mirror, Pulp automatically caches each
artifact and adds it to the repository. The cached content will be available in subsequent
requests even if the upstream repository becomes unreachable, and the remote can later be removed
from the distribution without losing access to cached content.
