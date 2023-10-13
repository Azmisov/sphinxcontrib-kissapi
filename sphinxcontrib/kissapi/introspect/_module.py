import inspect
from sphinx.pycode import ModuleAnalyzer, PycodeError

from ._utils import logger
from ._value import VariableValueAPI

class ModuleAPI(VariableValueAPI):
	""" Specialization of VariableValueAPI for module types. The main thing is it keeps track of the
		list of importable variables, those that were defined within the module and those that were not. This
		class also holds a ``ModuleAnalyzer``, which can be used to get documentation for class instance attributes.
	"""
	__slots__ = ["imports","maybe_imports","analyzer"]

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.imports = set()
		""" Set of modules this module imports; only includes modules part of the package """
		self.maybe_imports = set()
		""" Set of modules that were accessed in someway to reference a class or routine, e.g. ``from x import y``. We
			can't be 100% sure this module accessed directly, its just a guess. There's no way to detect these imports
			without analyzing the actual source code. For variable's whose source is ambiguous, we can use these
			possible imports to guess at which refs are the true source.
		"""
		# source code analysis; this gives you extra info like the estimated order variables were defined in source file
		try:
			self.analyzer = ModuleAnalyzer.for_module(self.name)
			""" autodoc ``ModuleAnalyzer`` object to introspect things not possible from code. This includes things like
				class instance documentation and variable definition source code ordering. 
			"""
			self.analyzer.analyze()
		except PycodeError as e:
			logger.warning("Could not analyze module %s", self.name, exc_info=e)
			self.analyzer = None

	@property
	def name(self) -> str:
		return self.module

	@property
	def qualified_name(self) -> str:
		return ""

	@property
	def module(self) -> str:
		return self.value.__name__

	def __repr__(self):
		return f"<ModuleAPI: {self.name}>"

	def member_order(self, name) -> float:
		""" Get the source ordering. This is not the line number, but a rank indicating the order variables
			were declared in the module.

			:returns: number which can be used as sort key; if the order cannot be determined (the variable
				was not found in the source module), +infinity is returned
		"""
		if self.analyzer is not None:
			return self.analyzer.tagorder.get(name, float("inf"))
		return float("inf")

	def instance_attrs(self, name) -> list:
		""" Get a list of documented instance-level attributes for object `name` (e.g. Class/enum/etc).
			This is called by ``:meth:~introspect.ClassAPI.analyze_members`` to get instance members
		"""
		if self.analyzer is None:
			return []
		lst = []
		attr_list = self.analyzer.find_attr_docs()
		# keys are tuples (name, attribute)
		for k in attr_list.keys():
			if k[0] == name:
				lst.append(k[1])
		return lst

	def instance_attr_docs(self, name, attr) -> list:
		""" Get list of (unprocessed) docstrings for an instance attribute from object `name` """
		if self.analyzer is None:
			return None
		attr_list = self.analyzer.find_attr_docs()
		return attr_list.get((name,attr), [])

	def analyze_members(self):
		""" Retrieves importable members of the module. Method should use the package to create/add varaibles, and then
			add a reference to this module in each variable's ``refs``
		"""
		if super().analyze_members(): return
		with logger.indent():
			for var, val in inspect.getmembers(self.value):
				vv = self.package.add_variable(val)
				# add reference to this module
				if vv.add_ref(self, var):
					logger.verbose("Found %s", vv)
					self.add_member(var, vv)
				# this module imports another in the package
				if isinstance(vv, ModuleAPI):
					self.imports.add(vv)