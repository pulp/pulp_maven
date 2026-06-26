# Upload Maven Artifacts

Maven artifacts can be uploaded to Pulp using the content create API.
The API accepts either a file upload or a reference to a pre-uploaded artifact.

When uploading multiple artifacts, the recommended workflow is:

1. Upload all content units in parallel (without specifying a repository)
2. Add them all to a repository in a single request using the repository modify API

This avoids 429 throttling from repository locking during parallel uploads.

## Prerequisites

Create a Maven repository and distribution to serve the uploaded content:

=== "run"

    ```bash
    pulp maven repository create --name maven-releases
    pulp maven distribution create --name maven-releases --repository maven-releases --base-path maven-releases
    ```

## Upload a file directly

Upload an artifact file using the `file` field:

```bash
curl -u admin:password -X POST \
  http://pulp-hostname/pulp/api/v3/content/maven/artifact/ \
  -F "file=@spring-cloud-config-server-4.3.0-redhat-1.jar" \
  -F "relative_path=org/springframework/cloud/spring-cloud-config-server/4.3.0-redhat-1/spring-cloud-config-server-4.3.0-redhat-1.jar" \
  -F 'pulp_labels={"vendor": "redhat", "verified": "true"}'
```

The `relative_path` must follow Maven repository layout conventions:
`<group_id_as_path>/<artifact_id>/<version>/<filename>`.

The `group_id`, `artifact_id`, `version`, and `filename` fields are automatically extracted from the `relative_path`.

The optional `pulp_labels` field accepts a JSON dictionary of key/value pairs for tagging content units. Labels can be used to filter and organize content.

## Upload using a pre-uploaded artifact

If you have already uploaded an artifact to the Pulp artifacts API, reference it by href:

```bash
# First, upload the file as an artifact
curl -u admin:password -X POST \
  http://pulp-hostname/pulp/api/v3/artifacts/ \
  -F "file=@spring-cloud-config-server-4.3.0-redhat-1.jar"

# Then create the Maven content unit referencing the artifact
curl -u admin:password -X POST \
  http://pulp-hostname/pulp/api/v3/content/maven/artifact/ \
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

content = api.create(
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

## Upload and add to a repository in one request

You can also specify a `repository` parameter on the create request to upload and add the
content to a repository in a single call. When a repository is specified, the content is
added via an immediate task that requires an exclusive lock on the repository. If the lock
cannot be acquired (e.g. another upload is in progress), the API returns **429 Too Many Requests**.

Callers should implement retry logic with backoff when using this approach:

```bash
upload_with_retry() {
  local max_retries=5
  local attempt=0
  while [ $attempt -lt $max_retries ]; do
    status=$(curl -s -o /dev/null -w "%{http_code}" -u admin:password -X POST \
      http://pulp-hostname/pulp/api/v3/content/maven/artifact/ \
      -F "file=@$1" \
      -F "relative_path=$2" \
      -F "repository=$3")
    if [ "$status" = "201" ]; then
      return 0
    elif [ "$status" = "429" ]; then
      attempt=$((attempt + 1))
      sleep $((attempt * 2))
    else
      echo "Error: HTTP $status"
      return 1
    fi
  done
  echo "Failed after $max_retries retries"
  return 1
}
```

For uploading many artifacts, the batch approach described in the previous section is
preferred as it avoids the 429 throttling entirely.
