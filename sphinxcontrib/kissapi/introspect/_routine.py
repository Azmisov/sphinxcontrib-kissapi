from functools import partial, partialmethod

from ._value import VariableValueAPI

class RoutineAPI(VariableValueAPI):
	""" Specialization of VariableValueAPI for routines. That includes things like function, lambda, method, c extension
		function, etc. It holds a reference to the underlying function along with the parents it is bound to
	"""
	__slots__ = ["bound_selfs","base_function"]

	def __init__(self, *args, **kwargs):
		self.bound_selfs = []
		""" objects the base_function was bound to, ordered """
		self.base_function = self
		""" the base callable before being bound """
		# since we override source_ref, we need to set up our own vars so it won't error
		super().__init__(*args, **kwargs)

		self.package.fqn_tbl[self.fully_qualified_name] = self
		self._source_ref = None

	@property
	def name(self) -> str:
		return self.qualified_name.rsplit(".", 1)[-1]

	@property
	def qualified_name(self) -> str:
		return self.base_function.value.__qualname__

	@property
	def module(self) -> str:
		return self.base_function.value.__module__

	@property
	def source_ref(self):
		""" overrides VariableValueAPI.source_ref to retrieve the source of the base source function,
			rather than the bound method
		"""
		return self.base_function._source_ref

	@source_ref.setter
	def source_ref(self, val):
		""" the source_ref may not actually be in refs yet for root functions of RoutineAPI; this happens
			if the actual ref (qualified name) is a bound method, and we're just pretending the true source
			is the underlying function

			When we set source_ref, we're indicating the source for the *base routine*, not the bound wrappers;
			any place that base routine is referenced/bound, documentation will point back to the original source definition
			of the routine; what is a valid source_ref?

			1) direct reference to the base routine (e.g. module, wrappers- which can include class methods and partials)
			2) direct references to the bound wrapper (RoutineAPI with this as its base function) (e.g. class)

			Inside PackageAPI, we use __module__+__qualname__ to set the source_ref directly. However, may not meet
			the 2 validity criteria mentioned. This can happen if the __qualname__ reference was ignored (e.g.
			PackageAPI.default_var_exclude). In any case, we'll throw an exception and anything downstream can catch
			it if it wants
		"""
		base = self.base_function
		assert not base.bound_selfs, "base_function should not be bound"
		if not (
			val in base.refs or
			any(val in r.refs for r in base.refs if (isinstance(r, RoutineAPI) and r.base_function is base))
		):
			raise RuntimeError("given source_ref does not reference, or is not bound to the routine (probably because the var was excluded)")
		base._source_ref = val

	def aliases(self, ref):
		""" Overridden method to account for routine's base_function """
		base = self.base_function
		if ref in base.refs:
			return base.refs[ref]
		# check binding wrappers
		for r in base.refs:
			if isinstance(r, RoutineAPI) and r.base_function is base:
				if ref in r.refs:
					return r.refs[ref]
		raise RuntimeError("given source_ref does not reference, or is not bound to the routine (probably because the var was excluded)")

	def analyze_members(self):
		""" Analyze root function of the routine. This extracts out any class/object bindings so that
			we can examine the base function that eventually gets called
		"""
		if super().analyze_members(): return
		root = self.value
		while True:
			# different types have different names for the underlying function
			if isinstance(root, property):
				func = "fget"
			elif isinstance(root, (partial, partialmethod)):
				func = "func"
			else:
				func = "__func__"
			if not hasattr(root, func):
				break
			if hasattr(root, "__self__"):
				parent = root.__self__
				par_vv = self.package.add_variable(parent)
				# don't have a variable name for this reference, but we do have bound index
				# idx = len(self.bound_selfs)
				# TODO: should we add bound index here? I think for now no
				par_vv.add_ref(self, "__self__")
				self.bound_selfs.append(par_vv)
			root = getattr(root, func)

		if root is not self.value:
			root_vv = self.package.add_variable(root)
			root_vv.add_ref(self, "__func__")
			self.base_function = root_vv

	def is_bound(self):
		""" Whether this is a bound method for another root function. For example, a class method
			is a function that is bound to the class; so there are two objets, the function and the bound method.
			When analyze_members is called on a bound routine, it will drill down to the root function and add
			it as a separate RoutineAPI variable, or plain VariableValueAPI if it was an external function.
		"""
		return self.base_function is not self