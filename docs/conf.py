# Configuration file for the Sphinx documentation builder.
# See for more config options: https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Path setup --------------------------------------------------------------

import os, sys

sys.path.append(os.path.abspath('../'))
sys.path.append(os.path.abspath('../../../kissapi/'))

# -- Project information -----------------------------------------------------

import datetime
project = 'Sphinx KissAPI'
author = "Isaac Nygaard"
copyright = '{}, {}'.format(datetime.datetime.now().year, author)

# -- General configuration ---------------------------------------------------

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom ones.

# TODO: enable https://pypi.org/project/sphinxcontrib-prettyspecialmethods/ ?
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.autosectionlabel",
    "sphinxcontrib.kissapi",
    'sphinx_rtd_theme'
]

from sphinxcontrib.kissapi.default_render import package_template
kissapi_config = {
    "overwrite": True,
    "output": {
        "kissapi":{
            "package":"sphinxcontrib.kissapi",
            "render":package_template
        }
    }
}

# Add any paths that contain templates here, relative to this directory.
templates_path = [] # '_templates'

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = []


# -- Options for HTML output -------------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
html_theme = 'sphinx_rtd_theme'
html_logo = "_static/dosa_logo_small.jpg"
html_theme_options = {
    "logo_only": True,
    # using flexbox css, so sticky makes anchor links fail
    "sticky_navigation": False
}

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ['_static']
html_css_files = ["custom.css"]
html_js_files = ["custom.js"]