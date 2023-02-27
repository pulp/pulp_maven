Upload a Jar to a Maven Repository
==================================

Create a maven Repository for the

Create a Maven Repository
-------------------------

``$ http POST http://localhost:24817/pulp/api/v3/repositories/maven/maven/ name='my-snapshot-repository'``

Create a Maven Distribution for the Maven Repository
----------------------------------------------------

``$ http POST http://localhost:24817/pulp/api/v3/distributions/maven/maven/ name='my-snapshot-repository' base_path='my/local/snapshots' repository=$REPO_HREF``


.. code:: json

    {
        "pulp_href": "/pulp/api/v3/distributions/67baa17e-0a9f-4302-b04a-dbf324d139de/"
    }

Upload a Jar to the Repository
------------------------------

``$ http --form POST http://localhost:24817/pulp/api/v3/content/maven/artifact/ group_id='org.openapitools' artifact_id='openapi-generator-cli' version='6.4.0-SNAPSHOT' filename='openapi-generator-cli-6.4.0.jar' file@./openapi-generator-cli.jar repository=$REPO_HREF``


.. code:: json

    {
        "task": "/pulp/api/v3/tasks/03d5a40b-4bda-4ee7-96cb-f0639b6c5d6a/"
    }

Add Pulp as mirror for Maven
----------------------------

.. code:: xml

    <settings>
      <mirrors>
        <mirror>
          <id>pulp-maven-central</id>
          <name>Local Maven Central mirror </name>
          <url>http://localhost:24816/pulp/content/my/local/my/local/snapshots</url>
          <mirrorOf>central</mirrorOf>
        </mirror>
      </mirrors>
    </settings>
