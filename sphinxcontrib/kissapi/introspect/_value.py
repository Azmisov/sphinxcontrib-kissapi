from __future__ import annotations
import typing
from functools import cached_property

if typing.TYPE_CHECKING:
	from ._module import ModuleAPI
	from ._package import PackageAPI
	from ._documenter import Documenter

from ._types import VariableType, Immutable
from ._utils import logger, is_special, is_private

class VariableReference:
	""" Stores information about a specific, named reference to a variable. """
	def __init__(self, refs:list[VariableValueAPI], names:list[str], module_name:str=None):
		""" Construct a new variable name container

			:param refs: a list of references for `:attr:~VariableReference.refs` or ``None`` if
				references are not available
			:param names: a list of names; should be two less in length than ``refs``
			:param module_name: the root module name; can be ``None`` if the name is not
				fully qualified		
		"""
		self.refs = refs
		""" Chain of parent references to the variable, starting with the package and ending with
			the variable itself. The chain will be incomplete if `:attr:~module_name` is ``None``.
			This attribute could be ``None`` in general if references were not provided, notably,
			when parsing via `:meth:~VariableReference.parse`.
		"""
		self.names = names
		""" Ordered list of names making up the qualified name. The module name is not included """
		self.module_name: str = module_name
		""" Module name that the variable is importable from. If ``None``, a fully qualified name
			was not made available
		"""

	@staticmethod
	def parse(fqn:str) -> VariableReference:
		""" Parses a fully qualified name, as in the one given by `:attr:~VariableReference.fully_qualified_name`.
			It should be in a form like so: ``module::class.member.submember``. Object references
			will not be available in the output container
		"""
		split = fqn.split("::", 1)
		if split > 1:
			modname, qualname = split
		else:
			modname = split
			qualname = None
		if qualname is None:
			names = []
		else:
			names = qualname.split(".")
		return VariableReference(None, names, modname)
	
	def __repr__(self) -> str:
		""" String representation of the reference """
		if self.module_name is None:
			return self.qualified_name
		return self.fully_qualified_name

	@property
	def name(self) -> str:
		""" Unqualified name """
		return "" if not len(self.names) else self.names[-1]

	@cached_property
	def qualified_name(self) -> str:
		""" Qualified name, which is a chain of names separated by `"."`. The entire qualified name
			may not have been made available, depending on how the `:class:~VariableReference` was
			constructed. If the name is for a module, this will return an empty string
		"""
		return ".".join(self.names)
	
	def _full(self, joiner) -> str:
		if self.module_name is None:
			raise ValueError("A fully qualified name was not made available")
		return self.module_name+joiner+self.qualified_name
	
	@cached_property
	def fully_qualified_name(self) -> str:
		""" Fully qualified name, which is the module and qualified name separated by `"."` """
		return self._full(".")

	@cached_property
	def fully_qualified_name_ext(self) -> str:
		""" Fully qualified name, which is the module and qualified name separated by `"::"`.
			This format is less ambiguous than `:attr:~fully_qualified_name`, and is used by
			Sphinx autodoc.
		"""
		return self._full("::")
	
	def trimmed(self, n:int) -> str:
		""" Returns `:attr:fully_qualified_name` trimmed to a certain number of components

			:param int n: how many components to include, from the end
			:returns: str, the trimmed fully qualified name
		"""
		num_names = len(self.names)
		if n > num_names:
			return self.fully_qualified_name
		if n == num_names:
			return self.qualified_name
		if n == 1:
			return self.name
		if n <= 0:
			return ""
		return ".".join(self.names[-n:])
	
	@property
	def value(self) -> VariableValueAPI:
		""" The variable value wrapper """
		if not self.refs:
			raise ValueError("Object references not made available")
		return self.refs[-1]

	@property
	def parent(self) -> VariableValueAPI:
		""" The immediate parent reference of the variable """
		if not self.refs or len(self.refs) < 2:
			raise ValueError("Object references not made available")
		return self.refs[-2]

	@property
	def module(self) -> ModuleAPI:
		""" The module the variable is importable from """
		if not self.refs or len(self.refs) < 2:
			raise ValueError("Object references not made available")
		if self.module_name is None:
			raise ValueError("A fully qualified name was not made available")
		return self.refs[1]
	
	def documenter(self) -> Documenter:
		""" Fetch documenter object specific to this reference. The documenter
			object handles retreiving documentation, summary, and signature info.
		"""
		# deferred to avoid circular reference
		from ._documenter import Documenter
		return Documenter(self)

	def order(self) -> tuple[int,str]:
		""" Fetch the source ordering of this variable reference. It
			works using the sphinx ModuleAnalyzer attached to `:class:ModuleAPI`

			:returns: tuple for use in ordering, first is tag order (infinity if unknown)
				then second is variable reference name
		"""
		oidx = float("inf")
		try:
			oidx = self.module.member_order(self.qualified_name)
		except:
			logger.warn("Failed to determine source ordering of %s", self)
		return (oidx, self.name)


class VariableValueAPI:
	""" A variable's value and information about all its variable references """
	__slots__ = [
		"package","type","refs","ext_refs","members","member_values",
		"_value","id","_doc","_analyzed","_best_ref",
	]

	def __init__(self, val, package:PackageAPI, vtype:VariableType=None):
		if vtype is None:
			vtype = VariableType.detect_type(val)
		self.package: PackageAPI = package
		""" Parent package this variable belongs to """
		self.type: VariableType = vtype
		""" Variable type """
		self.refs: dict[VariableValueAPI|PackageAPI, list[str]] = {}
		""" Parent variables which reference this variable, and what reference names were used """
		self.ext_refs: list[str] = []
		""" Names of external modules that reference this variable """
		self.members = {}
		""" Mapping of variable name to values for sub-members of this object. The values can be in any custom format,
			in order to contain extra information about the member type and such. The custom format should contain some
			way to access the raw value somehow. For members with the same raw, underlying value, their custom format
			should be identical. A mapping from raw value to custom format is given by ``:attr:~member_values``
		"""
		self.member_values = {}
		""" Mapping from raw member value to custom value format in ``members`` dict. Exact same raw member values
			should have the same custom format, the only difference being the variable reference name. Whereas ``members``
			can have multiple references for each variable value, this will only have one. Note, that in the case of
			RoutineAPI, multiple entries will be given, even if the base source_ref is identical. This matches the
			behavior of immutables... while an integer "5" is indistinguishable for two variables, we treat it as two
			unique references.
		"""
		# immutable types should hash to different vals, which is why we wrap in Immutable class
		if Immutable.is_immutable(val):
			val = Immutable(val)
		self._value = val
		""" Raw value or Immutable wrapper of it """
		self.id = id(self._value)
		""" Unique identifier for this value """
		self._doc = None
		""" Cached autodoc documenter """
		self._analyzed = False
		""" Whether analyze_members has been called already """
		self._best_ref = None
	
	@property
	def value(self):
		""" The actual variable value """
		# immutable types have been wrapped, so need to extract
		if isinstance(self._value, Immutable):
			return self._value.val
		return self._value
	
	@property
	def best_ref(self) -> tuple[VariableValueAPI, str]:
		""" The best reference, representing the object the variable's value was probably defined in, or the primary way
			to access the value. If ``None``, no best reference has been defined. Otherwise, it is a tuple giving the
			best parent variable from `:attr:~refs`, followed by the best reference name used by that parent: ``(parent,
			name)``. The name can be ``None``, meaning an arbitrary reference name can be considered the best.
		"""
		return self._best_ref
	
	@best_ref.setter
	def best_ref(self, pair):
		# unset value
		if pair is None:
			self._best_ref = None
			return
		# set value; can be a tuple or single value
		ref = pair
		name = None
		if isinstance(pair, tuple):
			if len(pair) != 2:
				raise ValueError("Best reference must be a VariableValueAPI, or tuple of VariableValueAPI and name")
			ref, name = pair
		if ref not in self.refs:
			raise ValueError("Supplied best reference does not reference this value")
		if name is not None and name not in self.refs[ref]:
			raise ValueError("Supplied best reference does not refer to this value using the supplied name")
		self._best_ref = (ref, name)

	def add_ref(self, parent:VariableValueAPI|PackageAPI, name:str):
		""" Add a variable reference to this value. This will not add the reference if the variable exclude callback
			returns truthy. If the reference is added, you can call ``add_member` on the parent in whatever
			custom format you desire.

			:param VariableValueAPI parent: The parent variable that referenced this value
			:param str name: The reference name used inside ``parent``
			:returns: bool, ``True`` if the reference was added
		"""
		# package ref is for modules; they have gone through separate package_exclude already
		if parent is not self.package and self.package.var_exclude(self.package, parent, self, name):
			return False
		if parent not in self.refs:
			self.refs[parent] = [name]
		else:
			names = self.refs[parent]
			# shouldn't happen unless there's a bug in parent's analyze_members method
			if name in names:
				raise ValueError(f"Adding the same referenced name twice: {parent} -> {name}")
			names.append(name)
		return True
	
	def add_member(self, name:str, raw_value:VariableValueAPI, custom_value=None):
		""" Add a member to this value. This updates `:attr:~members` and `:attr:member_values`

			:param str name: the variable reference name
			:param VariableValueAPI raw_value: the raw value that this member represents
			:param custom_value: The custom format we want to save to members to include extra information. If set
				to None, it will use ``raw_value`` instead. In either case, if it detects the ``raw_value`` is already
				in ``member_values`` it will reuse that value instead.
		"""
		if raw_value in self.member_values:
			custom_value = self.member_values[raw_value]
		else:
			if custom_value is None:
				custom_value = raw_value
			self.member_values[raw_value] = custom_value
		self.members[name] = custom_value

	def name(
		self, ref:VariableValueAPI=None, *, random:bool=False, qualified:bool=False
	) -> str | tuple[VariableValueAPI, str]:
		""" Get a reference name for this variable value. As a value can be referenced by many parents and names, you
			can pass arguments to control which is retrieved. You can also manually search through `:attr:~refs` to
			get a specific name.
		
			:param VariableValueAPI ref: Use a reference name from this parent variable (currently the first name that
			    has been added). If the parent matches `:attr:~best_ref`, the best reference name will be tried if not
			    ``None``; otherwise an arbitrary name is returned. If ``None`` is passed, `:attr:~best_ref` will try to
			    be used for the parent. If `:attr:~best_ref` is also ``None``, a placeholder name in the form
			    ``"?<variable_value>"`` will be returned.
			:param bool random: Pass ``True`` to get an arbitrary, valid name if possible, rather than defaulting to a
			    placeholder name. You can also pass any object that supports ``__contains__`` with a set of refs to
			    exclude from the random selection. Currently, the first valid ref will be returned.
			:param bool qualified: Pass ``True`` to return the parent reference with the name in a tuple
			:returns: str, the reference name; if ``qualified`` flag is set, a tuple will be returned
				``(parent, name)``, where parent is ``None`` if a placeholder name is returned
		"""
		# TODO: overload for ModuleAPI; return module name instead? maybe instead have sys.modules
		#	be the refs for modules
		name = None
		if ref is None:
			ref = self.best_ref
			if ref is None:
				# arbitrary (first seen) ref
				if random is not False and len(self.refs):
					for candidate in self.refs:
						# random can be an exclusion set
						if random is True or candidate not in random:
							ref = candidate
							break
				# placeholder
				if ref is None:
					try:
						val_str = str(self._value)
						LEN_LIMIT = 50
						if len(val_str) > LEN_LIMIT:
							val_str = val_str[:LEN_LIMIT-1]+"…"
					# can't serialize value?
					except:
						val_str = "…"
					# question mark to indicate anonymous variable; makes it an invalid identifier
					# so doesn't conflict and won't accidentally get mistaken for real reference
					name = f"?<{val_str}>"
			# best reference
			else:
				ref, name = ref
		# user provided 
		elif ref not in self.refs:
			raise ValueError("Supplied reference does not reference this value")
		# TODO: use `order` to get first declared var name? (seems to be ordered correctly already though)
		if name is None:
			# try to use best_ref name
			best = self.best_ref
			if best is not None and best[0] is ref:
				name = best[1]
			# otherwise arbitrary (first seen) name
			if name is None:
				name = self.refs[ref][0]
		if qualified:
			return (ref, name)
		return name

	def qualified_name(
		self, *refchain:VariableValueAPI
	) -> VariableReference:
		""" Get a qualified name for this variable value. This recursively calls `:meth:~VariableValueAPI.name`, using
			``refchain`` to control which ancestor references to use for the qualified name. If a qualified name cannot
			be constructed (no parent reference can be used at some point in the chain), an exception is thrown.
			Arbitrary names are used for ancestors if not specified in ``refchain``, or they have no
			`:attr:~VariableValueAPI.best_ref` set; e.g. the ``random`` flag is ``True`` for
			`:meth:~VariableValueAPI.name`. You can always construct a qualified name yourself by walking up
			`:attr:~VariableValueAPI.refs`.

			:param refchain: Parent references to use when building the qualified name. Provide
				these in reverse order, e.g. most nested to least. These are passed as the
				``ref`` argument of `:meth:~VariableValueAPI.name`.
			:returns: `:class:~VariableReference`, which can be used to extract parts of the qualified name
		"""
		refs = []
		names = []
		vv = self
		nest = 0
		while True:
			refs.append(vv)
			ref = None if nest >= len(refchain) else refchain[nest]
			ref, name = vv.name(refchain, random=refs, qualified=True)
			# end when we get back to the root package;
			# TODO: do we want to allow refs that go back to external modules?
			if ref is self.package:
				break
			if ref is None:
				raise ValueError(f"Missing {nest+1} ancestor reference of {self}, so cannot build a qualified name")
			names.append(name)
			vv = ref
		# last ref was ModuleAPI
		module_name = names.pop()
		return VariableReference(list(reversed(refs)), list(reversed(names)), module_name)

	def __repr__(self):
		return "<{}: {}>".format(self.__class__.__qualname__, self.name(random=True))

	def is_special(self, *args, **kwargs) -> bool:
		""" Check if variable name is special/magic, e.g. begins with double underscore.
			Arguments are forwarded to `:meth:~name`

			:param args: forwarded to `:meth:~name`
			:param kwargs: forwarded to `:meth:~name`
			:returns: ``True`` if variable is special
		"""
		return is_special(self.name(*args, **kwargs))

	def is_private(self, *args, **kwargs) -> bool:
		""" Check if variable name is private, e.g. begins with single underscore. Note that
			Python does not have true private variables, the naming convention for private variables
			simply being a convention. Arguments are forwarded to `:meth:~name`

			:param args: forwarded to `:meth:~name`
			:param kwargs: forwarded to `:meth:~name`
			:returns: ``True`` if variable is private
		"""
		return is_private(self.name(*args, **kwargs))
	
	def is_external(self) -> bool:
		""" Check if this variable is referenced outside the package. In PackageAPI.var_exclude, we assume any variable
			included outside the package was defined outside the package, so shouldn't be included in the API.
			Class/function/modules that are plain VariableValueAPI objects are external, by virtue of their
			``__module__`` value.

			:returns: ``True`` if variable is referenced externally
		"""
		return (self.__class__ is VariableValueAPI and self.type != VariableType.DATA) or bool(self.ext_refs)
	
	def is_immutable(self) -> bool:
		""" Check if this is an immutable type, so there can only be one reference to this variable
			
			:returns: ``True`` if value is immutable
		"""
		return isinstance(self._value, Immutable)

	def aliases(self, ref: VariableValueAPI) -> list[str]:
		""" Given a parent reference to this variable, list the variable names it goes by.
			This just a convenient interface into `:attr:~VariableValueAPI.refs`

			:param ref: variable reference object (e.g. class, module, etc)
			:returns: list of names
		"""
		if ref is None or ref not in self.refs:
			raise ValueError("Supplied reference does not reference this value")
		return self.refs[ref]

	def analyze_members(self) -> bool:
		""" Analyze sub-members of this variable. This should be implemented by subclasses. By
			default, it only tracks whether analysis has been performed already.

			:returns: True if members have already been analyzed
		"""
		old = self._analyzed
		self._analyzed = True
		return old