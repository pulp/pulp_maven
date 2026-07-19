from jinja2 import Template

SIMPLE_API_VERSION = "1.1"
SERIAL_CONSTANT = 1000000000

simple_index_template = """<!DOCTYPE html>
<html>
  <head>
    <title>Simple Index</title>
    <meta name="maven:repository-version" content="{{ SIMPLE_API_VERSION }}">
  </head>
  <body>
    {% for name in projects %}
      <a href="{{ name }}/">{{ name }}</a><br/>
    {% endfor %}
  </body>
</html>
"""

def write_simple_index(project_names, streamed=False):
    simple = Template(simple_index_template)
    context = {
        "SIMPLE_API_VERSION": SIMPLE_API_VERSION,
        "projects": project_names,
    }
    return simple.stream(**context) if streamed else simple.render(**context)


def write_simple_index_json(project_names):
    return {
        "meta": {"api-version": SIMPLE_API_VERSION, "_last-serial": SERIAL_CONSTANT},
        "projects": [{"name": name, "_last-serial": SERIAL_CONSTANT} for name in project_names],
    }


