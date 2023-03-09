Cache Maven Repository
=======================

Pulp Maven can be used to cache packages from Maven Central or any other repository on the internet.

The commands below use the ``pulp-cli-maven`` package available on PyPI.

Create a new Maven Remote
-------------------------

.. code-block:: bash

    $ pulp maven remote create --name maven-central --url https://repo1.maven.org/maven2/

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



Create a Maven Distribution with the Maven Remote
-------------------------------------------------

.. code-block:: bash

    $ pulp maven distribution create --name maven-central --remote maven-central --base-path maven-central

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
      "repository": null,
      "remote": "/pulp/api/v3/remotes/maven/maven/a0554b43-d229-4aba-b106-bd9f41eddd31/"
    }



Add Pulp as a mirror for Maven
------------------------------

In your ~/.m2/settings.xml add Pulp as a mirror of Maven Central.

.. code:: xml

    <settings>
      <mirrors>
        <mirror>
          <id>pulp-maven-central</id>
          <name>Local Maven Central mirror </name>
          <url>http://localhost:24816/pulp/content/maven-central</url>
          <mirrorOf>central</mirrorOf>
        </mirror>
      </mirrors>
    </settings>
