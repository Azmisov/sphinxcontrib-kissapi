from __future__ import annotations
import sys, types, inspect
from typing import Union
from collections import defaultdict

from ._types import VariableType, Immutable
from ._module import ModuleAPI
from ._class import ClassAPI
from ._routine import RoutineAPI
from ._value import VariableValueAPI
from ._utils import logger, is_private, is_special

class _AtomicNode:
	""" Used for module import analysis; a node in the import graph """
	__slots__ = ["modapi","out_ct","in_ct","in_nodes"]
	def __init__(self, m):
		self.modapi = m
		self.out_ct = 0 # stuff that imports m
		self.in_ct = 0 # stuff m imports
		# this are _AtomicNode/_MetaNode's representing the imported stuff
		# it doesn't necessarily match up with in_ct, since things may have merged into _MetaNode's
		self.in_nodes = set()

class _MetaNode:
	""" Used for module import analysis; 1+ grouped nodes in the import graph """
	__slots__ = ["nodes", "in_nodes"]
	def __init__(self, merge):
		self.nodes = set()
		self.in_nodes = set()
		for n in merge:
			# don't allow nested _MetaNode's
			if isinstance(n, _MetaNode):
				self.nodes.update(n.nodes)
			else:
				self.nodes.add(n)
			self.in_nodes.update(n.in_nodes)
		self.in_nodes -= self.nodes

class _CycleDetected(Exception):
	""" Used for module import analysis; Cycle detected in the module import graph"""
	pass

class PackageAPI:
	""" API for introspecting types and documentation inside a full python package """
	__slots__ = ["name","package","mods_tbl","ext_tbl","int_tbl","var_tbl","fqn_tbl","var_exclude","_need_analysis"]

	def __init__(self, pkg: types.ModuleType, options:dict={}):
		""" Parse a root module and extract the API for introspecting its types and documentation """
		# find all modules that are part of this package
		self.name: str = pkg.__name__
		""" Name of the package (that being the name of the main package's module) """
		self.ext_tbl: dict[int, list[str]] = defaultdict(list)
		""" Importable variables from external modules. It maps variable id's (e.g. ``id(var)``) to
			a list of modules the variable is available from
		"""
		self.int_tbl: dict[str, ModuleAPI] = {}
		""" Internal modules of the package, a superset of `:attr:~mods_tbl`: ``{module_name: ModuleAPI}`` """
		self.mods_tbl: dict[str, ModuleAPI] = {}
		""" Modules part of the package: ``{module_name: ModuleAPI}`` """
		self.var_tbl: dict[int, VariableValueAPI] = {}
		""" All importable variables from the package: ``{variable_id: VariableValueAPI}`` """
		self._need_analysis: list[int] = []
		""" List of vars from ``var_tbl`` that still need analysis """
		self.fqn_tbl: dict[str, VariableValueAPI] = {}
		""" Lookup table for fully qualified names, mapping to VariableValueAPI objects. This does not contain
			all variables, just those that encode raw qualified name data, like classes, functions, and modules. 
		"""
		self.package = self.add_variable(pkg, module=True, package=True)
		""" The module entry-point for the package. Also accessible as first element of `modules` """

		noop_cbk = lambda *args: False
		def get_cbk(name, default):
			cbk = options.get(name, default)
			return noop_cbk if cbk is None else cbk

		package_exclude = get_cbk("package_exclude", PackageAPI.default_package_exclude)
		self.var_exclude = get_cbk("var_exclude", PackageAPI.default_var_exclude)

		# get package modules;
		# examining modules can sometimes lazy load others, so keep repeating until no new modules are discovered
		mods_seen = set([self.name])
		while True:
			seen_new = False
			for k in list(sys.modules.keys()):
				if k in mods_seen:
					continue
				mods_seen.add(k)
				seen_new = True
				should_exclude = package_exclude(self.name, k)
				if should_exclude is True:
					logger.verbose("Excluding module's variables: %s", k)
					self.add_external_module(k)
					continue
				# internal; may or may not be included in package though
				should_include = not should_exclude
				self.add_variable(sys.modules[k], module=True, package=should_include, analyze=should_include)
			if not seen_new:
				break
		logger.verbose("%d internal modules", len(self.int_tbl))
		logger.verbose("%d accessible modules belong to package", len(self.mods_tbl))
		logger.verbose("%d exported variables defined in external modules", len(self.ext_tbl))

		# analyse all variables; repeat until no new sub-variables have been discovered
		# (e.g. can't analyze var's members until var has been added itself)
		analyze_pass = 1
		while True:
			logger.verbose("Analyzing nested variable members (pass %d)", analyze_pass)
			to_analyze = self._need_analysis
			self._need_analysis = []
			if not to_analyze:
				break
			with logger.indent():
				for vv in to_analyze:
					logger.verbose("Analyzing %s", vv)
					vv.analyze_members()
			analyze_pass += 1
		logger.verbose("Finished analyzing variable members")

		logger.verbose("Determining source definition of variables")
		def dont_resolve(vv):
			# external, source_ref already set elsewhere (probably by class itself), no refs, or ModuleAPI
			return vv.is_external() or vv.source_ref is not None or not vv.refs or isinstance(vv, ModuleAPI)
		
		with logger.indent():
			# we've indexed all variables and imports; now we guess source definition qualname of variables
			# Routine/ClassAPI are functions/classes defined within the package, so have qualname that can be used to set the best reference
			for vv in self.var_tbl.values():
				if dont_resolve(vv):
					continue
				if isinstance(vv, (RoutineAPI, ClassAPI)):
					# qualname is guaranteed to be within package's internal modules, since otherwise we would have made it DATA type
					name = module = vv.module
					# if source module was excluded from package (e.g. a private module), we'll need
					# further analysis to pick a "pretend" source module
					if module not in self.mods_tbl:
						continue
					attr_split = vv.qualified_name.rsplit(".",1)
					if len(attr_split) > 1:
						name += "::"+attr_split[0]
					# if type is nested inside function, not importable directly; it must have been referenced in some
					# accessible way, or is bound to a RoutineAPI which is accessible; so defer to source resolution to
					# figure out what the best ref is for these
					if "<locals>" not in name:
						try:
							if name not in self.fqn_tbl:
								raise RuntimeError("Variable has importable qualname {}, but {} not found".format(vv.fully_qualified_name, name))
							else:
								logger.verbose(f"Set source reference: {name} -> {vv}")
								vv.source_ref = self.fqn_tbl[name]
						# will silently ignore; this error is caused if the __qualname__ referenced was excluded
						# have to use a different reference instead, which will be resolved later
						except Exception as e:
							logger.verbose("Can't use qualname '%s' to set source_ref", name, exc_info=e)

					# update "maybe imports" for anything that referenced this variable
					if module not in self.mods_tbl:
						raise RuntimeError("missing module")
					vv_mod = self.mods_tbl[module]
					for r in vv.refs.keys():
						m = r.module
						if m and m != module and m in self.mods_tbl:
							self.mods_tbl[m].maybe_imports.add(vv_mod)

			for vv in self.var_tbl.values():
				""" Determine what the best source module is for a variable (where variable was defined);
					Unfortunately, it is not possible to really tell *where* a variable came from (see https://stackoverflow.com/questions/64257527/).
					A module's members may have been imported from another module, so you can't say exactly what module a variable
					belongs to. Or if a variable is the same across multiple classes, which class was it defined in initially?
					So you have to make some assumptions.
					
					- modules are their own source, so doesn't make sense to set it for module types
					- external variables don't need best source, since it is defined externally; user can choose to
					document each reference individually, or just mark it as external
					All that is left is DATA types, which we do an analysis of the graph of module imports
					
					DATA variable defined in multiple modules; have to do some more in depth analysis
					to determine which module is the "true" source of the variable.
					
					Some of cases needed to resolve:
					- variable set in multiple classes, modules, or both
					- data variables in external modules, e.g. val = ext_dict["attr"]; no way to detect that as from
					an external module
					- routines/classes defined in <locals>
					
					ref can be module, class, or routine; for now we'll ignore routine's, since those are either
					1) a bound self 2) external function 3) <locals> function. If user needs source_ref, they
					can pick one manually
				"""
				if dont_resolve(vv):
					continue
				crefs = list(r for r in vv.refs if isinstance(r, ClassAPI))
				mrefs = list(r for r in vv.refs if isinstance(r, ModuleAPI))
				# convert crefs to modules temporarily
				mod_refs = defaultdict(list)
				for m in mrefs:
					mod_refs[m].append(m)
				for c in crefs:
					if c.module in self.mods_tbl:
						mod_refs[self.mods_tbl[c.module]].append(c)
				mod_lst = list(mod_refs.keys())
				# can't specify source... probably bound to a routine or something, but not referenced elsewhere
				if not mod_lst:
					continue
				if len(mod_lst) > 1:
					best_mod = self.module_import_analysis(vv.value, mod_lst)
				else:
					best_mod = mod_lst[0]
				# we have our guess for module that it came from, now pick a class
				# if it is referenced in the actual module, then we'll assume it was defined there, and then
				# referenced inside the class, rather than the other way around
				refs = mod_refs[best_mod]
				try:
					if refs[0] is best_mod:
						vv.source_ref = best_mod
					elif len(refs) == 1:
						vv.source_ref = refs[0]
					else:
						# class which is referenced the most times
						refs.sort(key=lambda m: (-sum(len(l) for l in m.refs.values()), m.qualified_name))
						vv.source_ref = refs[0]
				# shouldn't happen, but may be some cases I haven't thought of
				except Exception as e:
					print("Failed to set source_ref after analyzing import graph and classes")
					print("value:", vv)
					print("candidate modules:", mod_lst)
					print("best module:", best_mod.name)
					print("refs within this module:", refs)
					raise e

	def add_external_module(self, mod_name:str):
		""" Mark all variables from the given module as "external" variables― outside the package.
			These external variables will not be introspected.

			:param str mod_name: the external module name to add
		"""
		try:
			mod = sys.modules[mod_name]
			# don't care about executable modules I guess
			if not isinstance(mod, types.ModuleType):
				logger.verbose("Skipping external executable module %s", mod_name)
				return
			mod_iter = inspect.getmembers(mod)
		except Exception as e:
			logger.verbose("Can't determine external refs in module %s", mod_name, exc_info=e)
			return
		for var, val in mod_iter:
			if not Immutable.is_immutable(val):
				self.ext_tbl[id(val)].append(mod_name)
		# modules itself is external
		self.ext_tbl[id(mod)].append(mod_name)

	def add_variable(
		self, val, *, module:bool=False, package:bool=False, analyze:bool=True
	) -> Union[ModuleAPI, ClassAPI, RoutineAPI, VariableValueAPI]:
		""" Construct an "API" interface for a variable, and add it to the package's variable
			lookup tables.
			
			Make sure to add all package modules first, before other variables

			:param val: variable value
			:param bool module: if True, this variable can be a `:class:~introspect.ModuleAPI`; if False,
				modules will be treated as plain `:class:~introspect.VariableValueAPI` instead
			:param bool package: if True, and the variable is a module, it will be added to the
			 	package's module table `:attr:~introspect.PackageAPI.mods_tbl`
			:param bool analyze: if True, mark this variable for member analysis				 
			:returns: the "API" class for this value, possibly newly created if it hasn't been seen before
		"""
		if id(val) in self.var_tbl:
			return self.var_tbl[id(val)]

		vtype = VariableType.detect_type(val)
		clazz = VariableValueAPI
		if vtype == VariableType.MODULE:
			if module:
				clazz = ModuleAPI
		# class/function that are not in one of the package's modules will not use subclass specialization
		elif vtype != VariableType.DATA:
			try:
				if val.__module__ in self.int_tbl:
					if vtype == VariableType.CLASS:
						clazz = ClassAPI
					elif vtype == VariableType.ROUTINE:
						clazz = RoutineAPI
			except: pass
		# create
		vv = clazz(val, self, vtype)
		vv_id = vv.id()
		self.var_tbl[vv_id] = vv
		# check if any external modules reference this variable
		if vv_id in self.ext_tbl:
			vv.ext_refs = self.ext_tbl[vv_id]
		# internal module
		if clazz is ModuleAPI:
			self.int_tbl[vv.name] = vv
			# module belongs to package
			if package:
				self.mods_tbl[vv.name] = vv
		if analyze:
			self._need_analysis.append(vv)
		return vv

	# Following methods are default options
	@staticmethod
	def default_package_exclude(pkg_name:str, module_name:str) -> Union[bool, str]:
		""" Default package module exclusion callback. This ignores modules that are:

			- outside ``pkg_name`` scope: don't begin with ``"[pkg_name]."``
			- private: where there is some module in the path prefixed by an underscore
			- "executable": where the module is not a ``ModuleType``

			:returns: One of the following:
			 - ``True``: excluded external module
			 - ``False``: included internal module
			 - ``"private"``: internal module excluded due to being private
			 - ``"executable"``: internal module excluded due to being executable
		"""
		# ignore if not in same namespace as package
		if not module_name.startswith(pkg_name+"."):
			return True
		# ignore private modules
		if any(x.startswith("_") for x in module_name[len(pkg_name)+1:].split(".")):
			logger.verbose("Excluding %s module (private)", module_name)
			return "private"
		# ignore executable packages
		if not isinstance(sys.modules[module_name], types.ModuleType):
			logger.verbose("Excluding %s module (executable)", module_name)
			return "executable"
		# don't exclude
		return False

	@staticmethod
	def default_var_exclude(pkg: PackageAPI, parent: VariableValueAPI, value: VariableValueAPI, name: str) -> bool:
		""" Default variable exclusion callback. This ignores variables that are:

			- private: variable begins with single underscore (see :meth:`is_private`)
			- external: variable found outside the package modules, and so we assume was defined outside as well
			  (see :meth:`~VariableValueAPI.is_external`)
			- special: variable begins with double underscore (see :meth:`is_special`); this allows
			  ``__all__`` and ``__version__`` within the ``__init__`` module, and also non-inherited class methods

			:param pkg: PackageAPI for this variable
			:param VariableValueAPI parent: the context in which we discovered this variable
			:param VariableValueAPI value: the value of the variable
			:param name: the name of the variable
			:returns: bool, ``True`` if the value should be excluded
		"""
		class_mbr = isinstance(parent, ClassAPI)
		if is_private(name):
			logger.verbose("Excluding %s (private); %s", name, value)
			return True
		if not class_mbr and value.is_external():
			logger.verbose("Excluding %s (external); %s", name, value)
			return True

		# allow __all/version__ for the package module itself
		special_include = ["__all__","__version__"]
		if is_special(name):
			allowed_init = name in special_include and parent is pkg.package
			allowed_override = isinstance(value, RoutineAPI) and (class_mbr or name == "__func__")
			if not (allowed_init or allowed_override):
				logger.verbose("Excluding %s (special); %s", name, value)
				return True

	@staticmethod
	def module_import_analysis(value, modules:list):
		""" Module import analysis, for determining source declaration of variable
			- if there's just one module, that is the one that it belongs to
			- construct a graph of module imports using the list of refs we collected
			- merge cycles into meta-nodes
			- (meta)nodes with no incoming edges (zero (meta)dependencies) are candidates for the source
			- if there's just one, that is the one it belongs to
			- If there are more than one (meta)node, it might mean there is another module which created the variable,
			  but did not export it; for example, `from x import y; z = y.attribute`, here y.attribute is not exported
			  explicitly. What to do in this scenario? You probably want to reference the module that exported it, not
			  the actual source of the variable, which you wouldn't be able to import from. I think best thing to
			  do is pretend there were a cycle between all resulting (meta)node's and treat them together
			- Now we have a collection of equally valid modules and need to decide which to say is the source import:
				1. if the variable type/class/bound function/etc has __module__ which is one of the candidate nodes, say
				   it is that one
				2. pick the module with the most outgoing edges (imported by the most modules)
				3. being equal, go by least incoming edges (has fewest dependencies)
				4. pick an arbitrary module (perhaps the one that comes first alphabetically to be deterministic)

			TODO: filter the candidate modules by those that contain documentation on the variable?

			:param value: value which we want to determine the source declaration module
			:param modules: list of modules which import `value`
		"""
		# already handled the first two cases in __init__, so all the rest are DATA type
		# those could be primitives, class instances, etc

		graph_nodes = set()
		graph_nodes_lookup = {}
		# construct nodes and edges
		for m in modules:
			node = _AtomicNode(m)
			graph_nodes.add(node)
			graph_nodes_lookup[m] = node
		for node in graph_nodes:
			for mi in (node.modapi.imports | node.modapi.maybe_imports):
				if mi in graph_nodes_lookup:
					mi_node = graph_nodes_lookup[mi]
					mi_node.out_ct += 1
					node.in_ct += 1
					node.in_nodes.add(mi_node)

		# To detect cycles, we do depth first traversal; if we have already seen a node, then everything in the
		# stack between that node makes up a cycle. We merge those into a _MetaNode (making sure to update edges as well)
		# Keep doing that until we can get through the whole graph without detecting a cycle
		def dfs_traversal(node, visited, stack):
			if node in visited:
				if node in stack:
					stack.append(node)
					raise _CycleDetected()
			visited.add(node)
			if node.in_nodes:
				stack.append(node)
				for edge in node.in_nodes:
					dfs_traversal(edge, visited, stack)
				stack.pop()

		while True:
			visited = set()
			stack = []
			try:
				# in case not fully connected, have to loop through all nodes
				for node in graph_nodes:
					dfs_traversal(node, visited, stack)
				break
			except _CycleDetected:
				# remove the cycle
				cycle = stack[stack.index(stack[-1]) : -1]
				graph_nodes -= cycle
				cnode = _MetaNode(cycle)
				# replace in_node edges to refer to cnode now
				for other in graph_nodes:
					if not other.in_nodes.isdisjoint(cnode.nodes):
						other.in_nodes -= cnode.nodes
						other.in_nodes.add(cnode)
				graph_nodes.add(cnode)

		# okay all cycles have been removed; now merge all nodes that have zero imports
		candidates = set()
		for node in graph_nodes:
			if not node.in_nodes:
				if isinstance(node, _MetaNode):
					candidates.update(node.nodes)
				else:
					candidates.add(node)
		assert candidates, "There are no graph nodes that have zero dependencies; this shouldn't happen"

		# best case scenario, there is one root module that all the others imported from
		if len(candidates) == 1:
			return candidates.pop().modapi

		# otherwise we had cyclical module dependencies, or var came from a non-exported var of a shared module
		# first check if a the variable type came from one of the modules
		cnames = {m.modapi.name : m.modapi for m in candidates}
		obj = value
		while type(obj) != type:
			obj = type(obj)
			if hasattr(obj, "__module__") and obj.__module__ in cnames:
				return cnames[obj.__module__]

		# if that fails, then we go by imported by counts, falling back to import counts or name
		candidates = list(candidates)
		candidates.sort(key=lambda m: (-m.out_ct, m.in_ct, m.modapi.name))
		return candidates[0].modapi