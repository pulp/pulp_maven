# Upload Maven Artifacts

Maven artifacts can be uploaded to Pulp using the content API.
There are two endpoints available:

- **Synchronous upload** (`content/maven/artifact/upload/`) — creates the content unit and returns it immediately
- **Async create** (`content/maven/artifact/`) — dispatches a task and returns a task href; supports adding to a repository in the same request

When uploading multiple artifacts, the recommended workflow is:

1. Upload all content units in parallel using the synchronous upload endpoint
2. Add them all to a repository in a single request using the repository modify API

## Prerequisites

Create a Maven repository and distribution to serve the uploaded content:

=== "run"

    ```bash
    pulp maven repository create --name maven-releases
    pulp maven distribution create --name maven-releases --repository maven-releases --base-path maven-releases
    ```

## Synchronous upload

Upload an artifact file using the synchronous `upload` endpoint:

```bash
curl -u admin:password -X POST \
  http://pulp-hostname/pulp/api/v3/content/maven/artifact/upload/ \
  -F "file=@spring-cloud-config-server-4.3.0-redhat-1.jar" \
  -F "relative_path=org/springframework/cloud/spring-cloud-config-server/4.3.0-redhat-1/spring-cloud-config-server-4.3.0-redhat-1.jar" \
  -F 'pulp_labels={"vendor": "redhat", "verified": "true"}'
```

The `relative_path` must follow Maven repository layout conventions:
`<group_id_as_path>/<artifact_id>/<version>/<filename>`.

The `group_id`, `artifact_id`, `version`, and `filename` fields are automatically extracted from the `relative_path`.

The optional `pulp_labels` field accepts a JSON dictionary of key/value pairs for tagging content units. Labels can be used to filter and organize content.

You can also reference a pre-uploaded artifact instead of uploading a file:

```bash
curl -u admin:password -X POST \
  http://pulp-hostname/pulp/api/v3/content/maven/artifact/upload/ \
  -H "Content-Type: application/json" \
  -d '{
    "artifact": "/pulp/api/v3/artifacts/<uuid>/",
    "relative_path": "org/springframework/cloud/spring-cloud-config-server/4.3.0-redhat-1/spring-cloud-config-server-4.3.0-redhat-1.jar",
    "pulp_labels": {"vendor": "redhat", "verified": "true"}
  }'
```

## Upload using the Python client

```python
from pulpcore.client.pulp_maven import ApiClient, ContentArtifactApi, Configuration

configuration = Configuration(
    host="http://pulp-hostname",
    username="admin",
    password="password",
)
client = ApiClient(configuration)
api = ContentArtifactApi(client)

content = api.upload(
    file="/path/to/spring-cloud-config-server-4.3.0-redhat-1.jar",
    relative_path="org/springframework/cloud/spring-cloud-config-server/4.3.0-redhat-1/spring-cloud-config-server-4.3.0-redhat-1.jar",
    pulp_labels={"vendor": "redhat", "verified": "true"},
)
print(content.group_id)      # org.springframework.cloud
print(content.artifact_id)   # spring-cloud-config-server
print(content.version)       # 4.3.0-redhat-1
print(content.filename)      # spring-cloud-config-server-4.3.0-redhat-1.jar
print(content.pulp_labels)   # {'vendor': 'redhat', 'verified': 'true'}
```

## Adding content to a repository

After uploading content units, add them to a repository using the modify API.
This approach allows you to upload many artifacts in parallel and then add them
all in a single repository version:

```bash
curl -u admin:password -X POST \
  http://pulp-hostname/pulp/api/v3/repositories/maven/maven/<uuid>/modify/ \
  -H "Content-Type: application/json" \
  -d '{
    "add_content_units": [
      "/pulp/api/v3/content/maven/artifact/<uuid-1>/",
      "/pulp/api/v3/content/maven/artifact/<uuid-2>/",
      "/pulp/api/v3/content/maven/artifact/<uuid-3>/"
    ]
  }'
```

Using the Python client:

```python
from pulpcore.client.pulp_maven import RepositoriesMavenApi

repo_api = RepositoriesMavenApi(client)
repo_api.modify(
    maven_maven_repository_href="/pulp/api/v3/repositories/maven/maven/<uuid>/",
    repository_add_remove_content={
        "add_content_units": [c.pulp_href for c in uploaded_content],
    },
)
```

## Async create with repository

The default create endpoint (`content/maven/artifact/`) dispatches an async task
and can optionally add the content to a repository in the same request:

```bash
curl -u admin:password -X POST \
  http://pulp-hostname/pulp/api/v3/content/maven/artifact/ \
  -F "file=@spring-cloud-config-server-4.3.0-redhat-1.jar" \
  -F "relative_path=org/springframework/cloud/spring-cloud-config-server/4.3.0-redhat-1/spring-cloud-config-server-4.3.0-redhat-1.jar" \
  -F "repository=/pulp/api/v3/repositories/maven/maven/<uuid>/"
```

This returns a 202 response with a task href. Use the task API to monitor completion.
