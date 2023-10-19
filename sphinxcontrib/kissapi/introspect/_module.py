import inspect
from sphinx.pycode import ModuleAnalyzer, PycodeError

from ._utils import logger
from ._value import VariableValueAPI

class ModuleAPI(VariableValueAPI):
	""" Specialization of VariableValueAPI for module types. The main addition is it keeps track of the list of
		importable variables, those that were defined within the module and those that were not. This class also holds a
		``ModuleAnalyzer``, which can be used to get documentation for class instance attributes.
	"""
	__slots__ = ["imports","maybe_imports","analyzer"]

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.imports = set()
		""" Set of modules this module imports. Only includes modules part of the package """
		self.maybe_imports = set()
		""" Set of modules that were accessed in someway to reference a class or routine, e.g. ``from x import y``. We
			can't be 100% sure this module accessed directly, its just a guess. There's no way to detect these imports
			without analyzing the actual source code. For variable's whose source is ambiguous, we can use these
			possible imports to guess at which refs are the true source.
		"""
		# source code analysis; this gives you extra info like the estimated order variables were defined in source file
		try:
			self.analyzer = ModuleAnalyzer.for_module(self.source_fully_qualified_name_ext)
			""" autodoc ``ModuleAnalyzer`` object to introspect things not possible from runtime. This includes things
				like class instance documentation and variable definition source code ordering. 
			"""
			self.analyzer.analyze()
		except PycodeError as e:
			logger.warning("Could not analyze module %s", self.source_fully_qualified_name_ext, exc_info=e)
			self.analyzer = None
		# ModuleAPI's are internal modules of the package; safe to add to src_tbl
		self.package.src_tbl[self.source_fully_qualified_name_ext] = self

	@property
	def source_fully_qualified_name_ext(self):
		""" Fully qualified name given in source definition of class """
		return self.value.__name__

	def member_order(self, name: str) -> float:
		""" Get the source ordering for a variable name. This is not the line number, but a rank indicating the order
			variables were declared in the module.

			:param str name: name of a member we want the order of
			:returns: number which can be used as sort key; if the order cannot be determined (the variable
				was not found in the source module), +infinity is returned
		"""
		if self.analyzer is not None:
			return self.analyzer.tagorder.get(name, float("inf"))
		return float("inf")

	def instance_attrs(self, name: str) -> list[str]:
		""" Get a list of documented instance-level attributes for object `name` (e.g. Class/enum/etc).
			This is called by ``:meth:~introspect.ClassAPI.analyze_members`` to get instance members

			:param str name: name of a parent object whose instance attributes we want to retrieve
			:returns: list of attribute names; empty if the parent object could not be found, or
				it happens to have no instance attributes
		"""
		if self.analyzer is None:
			return []
		lst = []
		attr_list = self.analyzer.find_attr_docs()
		for parent_name, attr_name in attr_list.keys():
			if parent_name == name:
				lst.append(attr_name)
		return lst

	def instance_attr_docs(self, name:str, attr:str) -> list[str]:
		""" Get a list of unprocessed docstrings for an instance attribute

			:param str name: name of a parent object whose instance attribute we're interested in
			:param str attr: the attribute we want documentation for
			:returns: list of docstrings; empty if the parent + attribute pair could not be found,
				or there is no documentation for it
		"""
		if self.analyzer is None:
			return None
		attrs = self.analyzer.find_attr_docs()
		return attrs.get((name,attr), [])

	def analyze_members(self):
		""" Finds importable members of the module """
		if super().analyze_members(): return
		with logger.indent():
			for var, val in inspect.getmembers(self.value):
				vv = self.package.add_variable(val)
				# add reference to this module
				if vv.add_ref(self, var):
					logger.verbose("Found %s", vv)
					self.add_member(var, vv)
				# this module imports another from the package
				if self.package in vv.refs:
					self.imports.add(vv)