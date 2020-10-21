********************************************************
{{ title }}
********************************************************

.. toctree::
    :hidden:
    :glob:

    {% for item in toc -%}
    {{ item }}
    {% endfor %}

{%- import "macros.rst" as macros %}
.. automodule:: {{ module }}

    {% for section in vars -%}
    .. rubric:: {{ section["title"] }} Summary
    {{ macros.summary(vars=section["vars"]) | indent }}
    {% endfor %}

    {{ macros.autodoc(autodoc) | indent }}

    {%- if aliased_vars|length %}
    Imported Values
    ---------------

    *The following values are available for import as well, but were defined in other modules*

    {% for section in aliased_vars -%}
    .. rubric:: Imported {{ section["title"] }} Summary
    {{ macros.summary(vars=section["vars"]) | indent }}
    {% endfor %}
    {% endif %}