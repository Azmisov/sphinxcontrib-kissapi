********************************************************
{{ title }}
********************************************************

{% import "macros.rst" as macros %}

.. autoclass:: {{ class }}
    :show-inheritance:

    {% for section in vars %}
    .. rubric:: {{ section["title"] }} Summary
    {{ macros.summary(vars=section["vars"]) | indent() }}
    {% endfor %}

    .. rubric:: Attributes Documentation

    .. autoattribute:: pierce

    .. rubric:: Methods Documentation

    .. automethod:: __call__