{# Wrap block in class if condition is true #}
{% macro conditional_class(cond,type,content) %}
{%- if cond %}
.. rst-class:: {{ type }}
{{ content|indent }}
{%- else %}
{{ content }}
{%- endif %}
{% endmacro %}

{# Summary table, basically an enhanced autosummary #}
{% macro summary(vars) -%}
.. list-table::
    :widths: 20, 80
    :class: kiss-summary

    {% for vdef in vars -%}
    * - :obj:`{{ vdef["display_name"] }} <{{ vdef["qualified_name"] }}>`
        {%- if vdef["signature"] %}\ *{{ vdef["signature"] }}*\ {%+ endif %}

        {%- if vdef["aliases"] %}
{%- set alias_block %}
**Aliases:**
{%- for alias in vdef["aliases"] %}
``{{ alias }}``{{- ", " if not loop.last }}
{%- endfor %}
{%- endset %}
        {{ conditional_class(vdef["aliases"]|length > 2, "kissapi_wrap", alias_block)|indent(8) }}
        {%- endif %}

        {%- if vdef["module"] %}
        {%- set different_name = (vdef["display_name"] != vdef["name"]) and not vdef["is_module"] %}
{% set source_block %}
**Source:**
{%- if different_name %}
``{{ vdef["name"] }}`` from
{%- endif %}
:mod:`~{{ vdef["module"] }}`
{% endset %}
        {{ conditional_class(different_name, "kissapi_wrap", source_block)|indent(8) }}
        {%- endif %}
      - {{ vdef["summary"] }}
    {% endfor %}
{%- endmacro %}

{# A list of pure autodoc sections (rubric + auto[TYPE] directives) #}
{% macro autodoc(data) %}
{%- if data|length > 0 %}
{%- for doc_args in data -%}
.. rubric:: {{ doc_args["title"] }} Documentation
{% for v in doc_args["list"] -%}
.. {{ doc_args["type"] }}:: {{ v }}
{% endfor -%}
{% endfor -%}
{% endif -%}
{% endmacro %}