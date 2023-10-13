""" Handles introspection of code, generating API classes to accessing code documentation.

	The entry point is `:class:~PackageAPI`, which is passed the root module
	of the package you want to introspect. It considers any module that is prefixed by the root module's name
	to be part of the package. For example, if ``foo`` is the root package Among those modules, it will take care of all the introspection to list
	modules, classes, variables, functions, etc.

	Idea here is that we can just have our own simple "documentation API" for accessing the package's API.
	Then you can do whatever you want with that, and generate API docs in your own format and style.

	Introspection has three main steps:
	1. Find target modules that are part of the package we want to analyze
	2. Build a table of all variables, as well as class attributes. Adding variables is broken into two steps, so
	   that it can handle cyclical variable references:

	   - Add variable to the table if not already (PackageAPI.add_variable)
	   - Recursively analyze members or other nested variable information, then repeat (VariableValueAPI.analyze_members)
	3. Determine the best source (e.g. qualified name) for each of the variables; all other references to the
	   variable value are considered aliases of the best source.
"""
import importlib

from ._utils import *
from ._types import *
from ._value import VariableValueAPI
from ._routine import RoutineAPI
from ._class import ClassAPI
from ._module import ModuleAPI
from ._package import PackageAPI
from ._documenter import Documenter

_pkg_memoize = {}
def analyze_package(pname:str, *args, **kwargs) -> PackageAPI:
	""" Factory for PackageAPI. It memoizes previously analyzed packages
		and reuses the results if possible; otherwise, it will import the module
		and create a new PackageAPI

		.. Note::
			Memoizing does not consider differing PackageAPI ``options`` arguments

		:param pname: the package to be analyzed
		:param args: forwarded to `:class:~PackageAPI.__init__`
		:param kwargs: forwarded to `:class:~PackageAPI.__init__`
		:returns: `:class:~PackageAPI` for inspecting package types and documentation
	"""
	if pname in _pkg_memoize:
		return _pkg_memoize[pname]
	m = importlib.import_module(pname)
	api = PackageAPI(m, *args, **kwargs)
	_pkg_memoize[pname] = api
	return api