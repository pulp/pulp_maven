Cache Maven Repository
=======================

Users can populate their repositories with content from an external sources by syncing
their repository.

Create a new Maven remote ``bar``
---------------------------------

``$ http POST http://localhost:24817/pulp/api/v3/remotes/maven/maven/ name='bar' url='https://repo1.maven.org/maven2/'``

.. code:: json

    {
        "_href": "/pulp/api/v3/remotes/maven/maven/2668a20c-3908-4767-b134-531e5145d7b7/"
    }

``$ export REMOTE_HREF=$(http :24817/pulp/api/v3/remotes/maven/maven/ | jq -r '.results[] | select(.name == "bar") | ._href')``

Create a Maven Distribution for the Maven Remote
------------------------------------------------

``$ http POST http://localhost:24817/pulp/api/v3/distributions/maven/maven/ name='baz' base_path='my/local/maven' remote=$REMOTE_HREF``


.. code:: json

    {
        "_href": "/pulp/api/v3/distributions/67baa17e-0a9f-4302-b04a-dbf324d139de/"
    }


Add Pulp as mirror for Maven
----------------------------

.. code:: xml

    <settings>
      <mirrors>
        <mirror>
          <id>pulp-maven-central</id>
          <name>Local Maven Central mirror </name>
          <url>http://localhost:24816/pulp/content/my/local/maven</url>
          <mirrorOf>central</mirrorOf>
        </mirror>
      </mirrors>
    </settings>
