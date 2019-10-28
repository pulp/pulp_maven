``pulp_maven`` Plugin
=====================

This is the ``pulp_maven`` Plugin for `Pulp Project
3.0+ <https://pypi.org/project/pulpcore/>`__. This plugin let's users use Pulp as a pull-through
cache for Maven repositories.

All REST API examples bellow use `httpie <https://httpie.org/doc>`__ to perform the requests.
The ``httpie`` commands below assume that the user executing the commands has a ``.netrc`` file
in the home directory. The ``.netrc`` should have the following configuration:

.. code-block::

    machine localhost
    login admin
    password admin

If you configured the ``admin`` user with a different password, adjust the configuration
accordingly. If you prefer to specify the username and password with each request, please see
``httpie`` documentation on how to do that.

This documentation makes use of the `jq library <https://stedolan.github.io/jq/>`_
to parse the json received from requests, in order to get the unique urls generated
when objects are created. To follow this documentation as-is please install the jq
library with:

``$ sudo dnf install jq``

Install ``pulpcore``
--------------------

Follow the `installation
instructions <https://docs.pulpproject.org/en/3.0/nightly/installation/instructions.html>`__
provided with pulpcore.

Users should install from **either** PyPI or source.

Install ``pulp-maven`` from source
----------------------------------

.. code-block:: bash

   sudo -u pulp -i
   source ~/pulpvenv/bin/activate
   git clone https://github.com/pulp/pulp_maven.git
   cd pulp_maven
   pip install -e .

Install ``pulp-maven`` From PyPI
--------------------------------

.. code-block:: bash

   sudo -u pulp -i
   source ~/pulpvenv/bin/activate
   pip install pulp-maven

Make and Run Migrations
-----------------------

.. code-block:: bash

   export DJANGO_SETTINGS_MODULE=pulpcore.app.settings
   django-admin makemigrations maven
   django-admin migrate maven

Run Services
------------

.. code-block:: bash

   django-admin runserver 24817
   gunicorn pulpcore.content:server --bind 'localhost:24816' --worker-class 'aiohttp.GunicornWebWorker' -w 2
   sudo systemctl restart pulpcore-resource-manager
   sudo systemctl restart pulpcore-worker@1
   sudo systemctl restart pulpcore-worker@2


Create a new Maven remote ``bar``
---------------------------------

``$ http POST http://localhost:24817/pulp/api/v3/remotes/maven/maven/ name='bar' url='https://repo1.maven.org/maven2/'``

.. code:: json

    {
        "pulp_href": "/pulp/api/v3/remotes/maven/maven/2668a20c-3908-4767-b134-531e5145d7b7/",
        ...
    }

``$ export REMOTE_HREF=$(http :24817/pulp/api/v3/remotes/maven/maven/ | jq -r '.results[] | select(.name == "bar") | .pulp_href')``

Create a Maven Distribution for the Maven Remote
------------------------------------------------

``$ http POST http://localhost:24817/pulp/api/v3/distributions/maven/maven/ name='baz' base_path='my/local/maven' remote=$REMOTE_HREF``


.. code:: json

    {
        "pulp_href": "/pulp/api/v3/distributions/67baa17e-0a9f-4302-b04a-dbf324d139de/",
       ...
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
