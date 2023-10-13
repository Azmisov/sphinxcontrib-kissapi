from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from ._package import PackageAPI
	from ._value import VariableValueAPI

from ._types import VariableType, Immutable
from ._utils import is_special, is_private

class VariableValueAPI:
	""" A variable's value, along with a list of variable names and import options """
	__slots__ = [
		"_value","_doc","_analyzed","package","type","refs",
		"_source_ref","ext_refs","members","member_values"
	]

	def __init__(self, val, package:PackageAPI, vtype:VariableType=None):
		if vtype is None:
			vtype = VariableType.detect_type(val)
		self.package: PackageAPI = package
		""" The package this variable belongs to """
		self.type: VariableType = vtype
		""" The type of this variable """
		self.refs: dict[VariableValueAPI, list[str]] = {}
		""" Parents which reference this variable, and the variable names they referenced """
		self.ext_refs: list[str] = []
		""" Module names not part of the package, but include references to this variable value """
		self.members = {}
		""" Mapping of variable name to values for sub-members of this object. The values can be in any custom format,
			in order to contain extra information about the member type and such; just depends what the subclass wants.
			But the custom format should have some way to access the raw variable still.
		"""
		self.member_values = {}
		""" Unique values represented in ``members`` dict. Exact same raw member values should have the same member
			type, the only difference being the variable reference name. This stores mappings of those raw values to 
			the custom format stored in ``members``. Whereas ``members`` can have multiple references for each variable
			value, this will only have one. Note, that in the case of RoutineAPI, multiple entries will be given, even
			if the base source_ref is identical. This matches the behavior of immutables... while an integer "5" is
			indistinguishable for two variables, we treat it as two unique references.
		"""
		if Immutable.is_immutable(val):
			val = Immutable(val)
		self._value = val
		""" Raw value or Immutable wrapper of it """
		self._doc = None
		""" Cached autodoc documenter """
		self._analyzed = False
		""" Whether analyze_members has been called already """
		self._source_ref = None

	def id(self):
		# immutable types should hash to different vals, which is why we wrap in Immutable class
		return id(self._value)
	
	def add_ref(self, parent:VariableValueAPI, name):
		""" Add a variable reference to this value. This will not add the reference if the variable exclude callback
			returns True. If the reference is added (returns True), you can call ``add_member` on the parent in whatever
			custom format you desire.

			:param parent: the context that the value was referenced
			:param name: the variable name inside ``parent``
			:returns: (bool) True if the reference was added
		"""
		if self.package.var_exclude(self.package, parent, self, name):
			return False
		if parent not in self.refs:
			self.refs[parent] = [name]
		else:
			self.refs[parent].append(name)
		return True
	
	def add_member(self, name, raw_value:VariableValueAPI, custom_value=None):
		""" Add a member to this value. This updates ``members`` and ``member_values``

			:param name: the variable reference name
			:param raw_value: the raw value that this member represents
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

	@property
	def source_ref(self):
		""" The best source parent out of ``refs``. This is the object the variable's value was probably defined in;
			the chain of `source_ref`'s makes up the fully qualified name
		"""
		return self._source_ref
	
	@source_ref.setter
	def source_ref(self, val):
		if not (val is None or val in self.refs):
			raise RuntimeError("source_ref must be inside refs")
		self._source_ref = val
	
	@property
	def value(self):
		""" The actual variable value """
		# immutable types have been wrapped, so need to extract
		if isinstance(self._value, Immutable):
			return self._value.val
		return self._value
	
	@property
	def name(self) -> str:
		""" Return primary variable name (from source_ref), or the module name if it is a module. If no source_ref
			is set and so we can't get the variable name, a placeholder ``"anonymous<...>"`` name is returned instead.
		"""
		if self.type == VariableType.MODULE:
			return self.value.__name__
		# TODO: use ``order`` to get first declared var name? (seems to be ordered correctly already though)
		if self.source_ref is None or not self.refs:
			val_str = str(self._value)
			LEN_LIMIT = 50
			if len(val_str) > LEN_LIMIT:
				val_str = val_str[:LEN_LIMIT-3]+"..."
			# question mark to indicate anonmyous variable
			name = "?<{}>".format(val_str)
			#logger.error("No source_ref set for %s", name)
			return name
		v = self.refs[self.source_ref]
		return v[0]
	
	@property
	def qualified_name(self) -> str:
		""" Get qualified name, not including module. This uses the chain of source_ref's to mimic a qualified name.
			If a source_ref is missing, an empty string is returned.
		"""
		if self.source_ref is None:
			return ""
		n = self.name
		qn = self.source_ref.qualified_name
		if qn:
			return qn+"."+n
		return n
	
	@property
	def module(self) -> str:
		""" Get module name for the variable. If this is not a module, it uses the chain of source_ref's to search
			for the underlying module; if a source_ref is missing, an empty string is returned.
		"""
		if self.type == VariableType.MODULE:
			return self.name
		if self.source_ref is not None:
			return self.source_ref.module
		return ""
	
	@property
	def fully_qualified_name(self) -> str:
		""" A combination of module and qualified name separated by "::". If it is a module, just the module half is
			returned. If it has no ``__module__`` attribute, it uses the chain of source_ref's to mimic such, giving an
			empty string if a source_ref is missing.
		"""
		mn = self.module
		qn = self.qualified_name
		if qn:
			return "{}::{}".format(mn, qn)
		return mn
	
	def __repr__(self):
		return "<{}: {}>".format(self.__class__.__qualname__, self.name)

	def is_special(self) -> bool:
		""" Check if variable name begins with double underscore """
		return is_special(self.name)

	def is_private(self) -> bool:
		""" Check if variable name begins with single underscore """
		return is_private(self.name)
	
	def is_external(self) -> bool:
		""" Check if this variable is referenced outside the package. In PackageAPI.var_exclude, we assume any variable
			included outside the package was defined outside the package, so shouldn't be included in the API.
			Class/function/modules that are plain VariableValueAPI objects are external, by virtue of their
			``__module__`` value.
		"""
		return (self.__class__ is VariableValueAPI and self.type != VariableType.DATA) or bool(self.ext_refs)
	
	def is_immutable(self) -> bool:
		""" Check if this is an immutable type, so there would never be references of this same
			variable
			
			:returns: True if immutable
		"""
		return isinstance(self._value, Immutable)

	def aliases(self, ref) -> list:
		""" Given a reference to this variable, list the variable names it goes by

			:param ref: variable reference object (e.g. class, module, etc)
		"""
		if ref is None or ref not in self.refs:
			raise ValueError("ref is not valid for this variable")
		return self.refs[ref]

	def _ref_qualified_name(self, ref=None, name:str=None, allow_nosrc:bool=True):
		# TODO: ugh, should probably cleanup this interface; maybe make qualified_name/module be methods instead of
		#   properties and have them accept ref/name args
		if ref is None:
			ref = self.source_ref
			# this only works for get_documenter
			if ref is None:
				if allow_nosrc  and self.type == VariableType.MODULE:
					return (None, None, "", self.fully_qualified_name)
				raise ValueError("the variable has no source_ref so can't get ref qualified name")
		# will raise error if user-provided ref is bad
		names = self.aliases(ref)
		if name is None:
			name = names[0]
		elif name not in names:
			raise ValueError("The ref/name combination not found in the variables refs")
		# qualified name for this reference
		pname = ref.qualified_name
		mod = ref.module
		qn = name
		if pname:
			qn = pname+"."+qn
		fqn = mod+"::"+qn
		return (ref, name, qn, fqn)

	def order(self, ref=None, name:str=None):
		""" This gives the source ordering of this variable. It will differ for each reference of the variable. It
			works using the sphinx ModuleAnalyzer attached to ModuleAPI, so we first need a reliable ``source_ref``
			and qualified name for the ref

			:param ref: the parent reference where we're trying to get tag order; if ``None``, it uses the ``source_ref``
			:param name: the variable name of the reference in ``ref``; if ``None``, it uses the first variable name
				of ``ref``
			:returns: ``tuple (int, str)``, which can be used for ordering first by tag order then var name
		"""
		# deferred, to avoid circular import
		from ._module import ModuleAPI
		ref, name, qn, fqn = self._ref_qualified_name(ref, name, False)
		modapi = self.package.fqn_tbl.get(fqn,None)
		oidx = float("inf")
		if isinstance(modapi, ModuleAPI):
			oidx = modapi.member_order(qn)
		return (oidx, name)

	def get_documenter(self, ref=None, name:str=None):
		""" Get a sphinx documenter object for this value.

			:param ref: the parent reference where we're trying to get documentaiton for; if ``None``, it uses the ``source_ref``
			:param name: the variable name of the reference in ``ref``; if ``None``, it uses the first variable name
				of ``ref``
			:returns: Documenter object
		"""
		# deferred, to avoid circular import
		from ._documenter import Documenter
		ref, name, qn, fqn = self._ref_qualified_name(ref, name)
		return Documenter(fqn, self, ref, name)

	def analyze_members(self) -> bool:
		""" Analyze sub-members of this variable. This should be implemented by subclasses.

			:returns: True if members have already been analyzed
		"""
		old = self._analyzed
		self._analyzed = True
		return old