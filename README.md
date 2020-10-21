Welcome to Kiss API!
====================

This project is a [Sphinx](https://www.sphinx-doc.org/) plugin for automatically generating python API docs.

Why use this plugin?
--------------------
This plugin takes a different approach than others like [autoapi](https://sphinx-autoapi.readthedocs.io),
[automodapi](https://sphinx-automodapi.readthedocs.io), or plain
[autodoc](https://www.sphinx-doc.org/en/master/usage/extensions/autodoc.html). I found that it these other plugins
did a pretty good job of automatically generating API docs, but it was never perfect. They wouldn't know exactly what
to include in the docs, and there just weren't enough customization options to get what I needed.

Instead of generating the docs automatically, this plugin provides a simple, flexible API for analyzing and introspecting
your python code. Using the API, you can generate reST documentation yourself however you want!

I've also included a default "renderer" which will generate reST documentation automatically. You can use these if
you are satisfied with the output and don't need any additional customization.

Quickstart
----------
Insert the `kissapi` reST directive into your documentation. This triggers the plugin to create API classes that you
can use to generate documentation. Use the `kissapi_config` in your sphinx `config.py` to add your documentation
generation callback.

```rest
.. kissapi:: your_package_name
    :introspect: optional_config_key
    :render: optional_config_key
``` 
