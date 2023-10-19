********************************************************
{{ title }}
********************************************************

{% if toc -%}
.. toctree::
    :hidden:
    :glob:

    {% for item in toc -%}
    {{ item }}
    {% endfor %}
{%- endif %}

{%- import "macros.rst" as macros %}
.. auto{{ type }}:: {{ name }}
    :show-inheritance:

    {% for section in sections -%}
    .. rubric:: {{ section["title"] }} Summary
    {{ macros.summary(vars=section["vars"]) | indent }}
    {% endfor %}

    {{ macros.autodoc(autodoc) | indent }}

    {# *The following values are available for import as well, but were defined in other modules* #}