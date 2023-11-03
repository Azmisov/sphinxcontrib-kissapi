from weakref import WeakSet
from functools import partial, partialmethod, cached_property
from types import (
	MethodType, FunctionType, WrapperDescriptorType, MethodDescriptorType, GetSetDescriptorType,
	MethodWrapperType, ClassMethodDescriptorType, BuiltinMethodType, MemberDescriptorType
)

from ._types import InstancePlaceholder
from ._value import VariableValueAPI
from ._utils import logger

class RoutineBinding:
	""" An entry in a chain of nested, bound routines. See `:meth:~RoutineAPI.analyze_bindings` """
	__slots__ = ["base","bound","replacement"]
	def __init__(self, base, bound:tuple, routine):
		self.base = base
		""" Object handling binding or calls, such as a descriptor or callable """
		self.bound: tuple = bound
		""" Tuple of bound prefixed arguments """
		self.routine = routine
		""" Indicates the actual object that will be called. If a class, it indicates placeholder
			type that will be created for an instance. ``None`` indicates passthrough to the next
			nesting level when accessed, for example ``staticmethod``.
		"""

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

	@property
	def source_fully_qualified_name_ext(self):
		""" Fully qualified name given in source definition of the base routine """
		v = self.base_function.value
		return v.__module__ + "::" + v.__qualname__

	@property
	def _old_source_ref(self):
		""" overrides VariableValueAPI.source_ref to retrieve the source of the base source function,
			rather than the bound method
		"""
		return self.base_function._source_ref

	@_old_source_ref.setter
	def _old_source_ref(self, val):
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
			elif isinstance(root, (partial, cached_property)):
				func = "func"
			else:
				func = "__func__"
			if not hasattr(root, func):
				break
			# the object its bound to
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
	
	@staticmethod
	def analyze_bindings(root, clazz=None, instance_placeholder:InstancePlaceholder=None) -> list[RoutineBinding]:
		""" Analyze the wrapped nesting of callables and bindings for a member
		
			:param root: the member value, representing the root of the analysis
			:param clazz: if not ``None``, it specifies that we should interpret ``root`` as being 
				accessed from an instance of ``clazz`` through ``__getattribute__``, resolving
				top-level descriptors that are encountered
			:param instance_placeholder: a value to use as a substitute for bindings to an
				instance of ``clazz``; if ``None`` a value will be autocreated if needed
			:returns: bindings ordered from least to most nested
		"""
		NULL = object()
		# wrappers will prefix arguments/bindings, so innermost gives first args
		stack: list[RoutineBinding] = []
		# set of roots we've analyzed, to avoid loops
		seen = WeakSet()
		
		while True:
			wrapped = NULL
			bound = None
			routine = NULL

			# candidate to go through descriptor interface and receive a binding
			if clazz is not None:
				delegate = False
				if instance_placeholder is None:
					instance_placeholder = InstancePlaceholder()

				if isinstance(root, classmethod):
					wrapped = root.__func__
					bound = (clazz,)
					routine = MethodType
				elif isinstance(root, staticmethod):
					wrapped = root.__func__
					bound = ()
					routine = None
				elif isinstance(root, property):
					wrapped = root.fget
					bound = (instance_placeholder,)
				elif isinstance(root, cached_property):
					wrapped = root.func
					bound = (instance_placeholder,)
				elif isinstance(root, partialmethod):
					wrapped = root.func
					bound = root.args
					routine = partial
					delegate = True
				# example: __slots__ descriptors, array.array.typecode, datetime.timedelta.days
				elif isinstance(root, (MemberDescriptorType, GetSetDescriptorType)):
					bound = (instance_placeholder,)
				# example: object.__str__
				elif isinstance(root, WrapperDescriptorType):
					# wrapped is a C/builtin function, so not available
					bound = (instance_placeholder,)
					routine = MethodWrapperType
				# examples: str.join, dict.__dict__["__contains__"]
				elif isinstance(root, MethodDescriptorType):
					# wrapped is a C/builtin function, so not available
					bound = (instance_placeholder,)
					routine = BuiltinMethodType
				# example: dict.__dict__["fromkeys"]
				elif isinstance(root, ClassMethodDescriptorType):
					# wrapped is a C/builtin function, so not available
					bound = (clazz,)
					routine = BuiltinMethodType
				# assumes BuiltinFunctionType are not descriptors
				elif isinstance(root, FunctionType):
					wrapped = root
					bound = (instance_placeholder,)
					routine = MethodType

				# wrapper can forward the descriptor call to a wrapped descriptor
				if not delegate:
					clazz = None

			# otherwise, a non-descriptor
			if bound is None:
				if isinstance(root, MethodType):
					wrapped = root.__func__
					bound = (root.__self__,)
				# example: dict().__contains__
				elif isinstance(root, BuiltinMethodType):
					bound = (root.__self__,)
				# example: object().__str__
				elif isinstance(root, MethodWrapperType):
					bound = (root.__self__,)
				elif isinstance(root, partial):
					wrapped = root.func
					bound = root.args
				# examples: cache, lru_cache, wraps
				else:
					wrapped = getattr(root, "__wrapped__", NULL)
					bound = ()

			if routine is NULL:
				routine = root
			stack.append(RoutineBinding(root, bound, routine))
			# enforce that wrappers are callables;
			# warnings where there could be a problem with our analysis, or a bug in the user's program
			if callable(wrapped):
				root = wrapped
				if root in seen:
					logger.warn("wrapped functions in binding chain form a loop: %s", root)
					break
				seen.add(root)
			else:
				if wrapped is not NULL:
					logger.warn("wrapped function in binding chain is not callable: %s", wrapped)
				break

		return stack