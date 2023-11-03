from collections import defaultdict
from typing import TYPE_CHECKING, Callable
from .introspect import (
	VariableType, ClassMemberType, ClassMemberBinding, logger, ModuleAPI
)

if TYPE_CHECKING:
	from .manager import RenderManager
	from .introspect import ClassAPI, VariableValueAPI, PackageAPI

def capitalize(s: str) -> str:
	""" Capitalize the first letter of a string
	
		:param str s: the string to capitalize
		:returns: str, a capitalized string
	"""
	return s[0].upper() + s[1:]

def module_title(mod: ModuleAPI) -> str:
	""" Return pretty title for a module. This takes the tail part of the module path, converts
		underscores to spaces, and then capitalizes each word. For example: ``module.path.foo_bar``
		becomes ``Foo Bar``

		:param ModuleAPI mod: the module to get a title for
		:returns: str, a title for the module
	"""
	name = mod.name()
	tail = name.rsplit(".",1)[-1]
	words = tail.split("_")
	return " ".join(map(capitalize, words))

def categorize_members(
	obj:VariableValueAPI, cat_cbk: Callable, titles:list[str], include_imports:bool=True, include_external:bool=False
):
	""" Organizes the members of a variable into sections and extracts the useful information
		for generating documentation

		:param obj: Object whose members we want to categorize
		:param cat_cbk: Callback to categorize each member, ``cbk(VariableValueAPI, custom_member_data) -> int``.
			Each category is designated by a number, corresponding to an index into ``titles``
		:param titles: Titles for each category, where category is the index in the list
		:param bool include_imports: Set to ``True`` if you wish to include members where ``obj``
			is not the best reference (`:attr:~introspect.VariableValueAPI.best_ref`) for the member.
			This is usually because the member's value was imported from another location, or simply
			is referencing the definition from elsewhere.
		:param bool include_external: Set to ``True`` if you wish to include members found outside
			the package, and thus were probably defined externally
	"""
	# {category: [{... vardata ...}]}
	data = defaultdict(list)
	for vv, extra in obj.member_values.items():
		# external members
		if not include_external and vv.is_external():
			continue
		# variable imported / referenced from another source, or true source not set
		best_ref = vv.best_ref
		if best_ref is None:
			logger.warning("No best source set for variable: %s", vv)
		imported = best_ref is not obj
		if not include_imports and imported:
			continue

		# best source name, qualified as coming from obj
		name = vv.name(obj)
		# aliases, excluding the best name
		aliases = set(vv.aliases(obj))
		aliases.remove(name)
		# where value was actual defined
		source = vv.qualified_name()

		""" can pass in custom mod (and optionally name) to get documentation *specific* to this module;
			if src_ref is None, it may be module (source_ref doesn't make sense), or we don't know what the source
			was... a couple things in the logic cause this, for instance, the var was defined outside the
			package, or as a nested function/class that would require accessing <locals> 
		"""
		if src is None and not isinstance(vv, ModuleAPI):
			doc = vv.get_documenter(obj, name)
		else:
			# this gets docs of source_ref, where variable was first created
			doc = vv.get_documenter()
		summary = doc.summary()

		var = {
			"name": name,
			"aliases": aliases[1:],
			"order": vv.order(obj, name),
			# source_xxx gives link to actual source definition (which would be obj, if "defined" flag is set)
			"source": source.fully_qualified_name, # module.qualname
			"source_ext": source.fully_qualified_name_ext, # module::qualname, format needed by autodoc
			"source_name": source.name, # final path value in source
			# includes last two objects (e.g. module+var, class+member)
			"source_short": source.trimmed(2),
			"defined": not imported,
			"external": vv.is_external() or best_ref,
			"summary": summary["summary"],
			"signature": summary["signature"],
			"value": vv,
			"doc": doc
		}
		# categorize the variable
		data[cat_cbk(var, extra)].append(var)

	sorted_cats = []
	# sort by category index
	for cat in sorted(list(data.keys())):
		vars = data[cat]
		# personally, I find alphabetical easier to navigate, so only use 2nd val of tuple;
		# to use src ordering, take that out
		vars.sort(key=lambda v: v["order"][1])
		sorted_cats.append({
			"title": titles[cat],
			"category": cat,
			"vars": vars
		})
	return sorted_cats

# Organize class members
_class_category_titles = [
	"Inner Classes",
	"Static Attributes",
	"Class Attributes",
	"Instance Attributes",
	"Static Methods",
	"Class Methods",
	"Instance Methods",
	"Singleton Methods"
]
_class_member_types = {
	ClassMemberType.INNERCLASS: 0,
	ClassMemberType.DATA: 1,
	ClassMemberType.METHOD: 2
}
_class_binding_types = {
	ClassMemberBinding.STATIC: 1,
	ClassMemberBinding.CLASS: 2,
	ClassMemberBinding.INSTANCE: 3,
	ClassMemberBinding.SINGLETON: 4
}
def _class_categorize(var, extra):
	""" Maps class member to category; output index corresponds to index inside _class_category_titles """
	cat_type = _class_member_types[extra.type]
	if cat_type:
		cat_binding = _class_binding_types[extra.binding]
		return (cat_type-1)*3+cat_binding
	return 0
def categorize_class(clazz: ClassAPI):
	""" Specialization of `:func:~default_render.classify_members` for Class types

		:param clazz: the class whose members you want to categorize
	"""
	return categorize_members(clazz, _class_categorize, _class_category_titles, True, True)

# Organize module variables
_module_category_titles = [
	"Data",
	"Functions",
	"Classes",
	"Referenced Modules",
	"Referenced Data",
	"Referenced Functions",
	"Referenced Classes"
]
_module_variable_types = {
	VariableType.MODULE: -1,
	VariableType.DATA: 0,
	VariableType.ROUTINE: 1,
	VariableType.CLASS: 2
}
def _module_categorize(var, extra):
	cat = _module_variable_types[var["value"].type]
	if not var["defined"]:
		cat += 4
	assert cat >= 0, "Module must be imported"
	return cat
def categorize_module(mod, *args, **kwargs):
	""" Specialization of `:func:~default_render.classify_members` for Module types

		:param clazz: the module whose members you want to categorize
		:param args: forwarded to `:func:~default_render.classify_members`
		:param kwargs: forwarded to `:func:~default_render.classify_members`
	"""
	return categorize_members(mod, _module_categorize, _module_category_titles, *args, **kwargs)


def class_template(kiss:RenderManager, clazz:ClassAPI, subdir:list, toc:list=None):
	if toc is None: toc = []
	sections = categorize_class(clazz)
	autodoc = []
	for s in sections:
		cat = s["category"]
		# inner class
		if cat == 0:
			for var in s["vars"]:
				if var["defined"]:
					toc.append(class_template(kiss, var["value"], [*subdir, clazz.name]))
		# attribute/methods
		else:
			mode = "autoattribute" if cat <= 3 else "automethod"
			lst = list(v["source_ext"] for v in s["vars"] if v["defined"] or v["external"])
			if lst:
				autodoc.append({
					"title": s["title"],
					"type": mode,
					"list": lst
				})

	#print("writing class template: ", clazz.fully_qualified_name)
	out = kiss.write_template(
		"{}/{}.rst".format("/".join(subdir), clazz.name),
		"object.rst",
		{
			"title": capitalize(clazz.name)+" Class",
			"type": "class",
			"name": clazz.fully_qualified_name,
			#"module": module,
			"sections": sections,
			"autodoc": autodoc,
			"toc": toc
		}
	)
	return out

def module_template(kiss:RenderManager, mod:ModuleAPI, title:str, toc:list=None, include_imports:bool=False, write_file:bool=True) -> str:
	""" Render template for a module
	
		:param kiss: KissAPI RenderManager
		:param mod: module to document
		:param title: title of module
		:param toc: 
		:param bool include_imports:
		:param bool write_file: write rendered module documentation to a file
		:returns: str, the rendered documentation string if write_file is ``False``, otherwise the
			relative path to the written file
	"""
	if toc is None: toc = []
	sections = categorize_module(mod, include_imports)
	autodoc = []
	for s in sections:
		cat = s["category"]
		# we assume variables/function docs are short, so we can include in main module page
		if cat <= 1:
			mode = "autofunction" if cat else "autodata"
			# only include full documentation for vars defined in module
			lst = list(v["source_ext"] for v in s["vars"] if v["defined"])
			if lst:
				autodoc.append({
					"title": s["title"],
					"type": mode,
					"list": lst
				})
		# classes defined in this module
		elif cat == 2:
			for var in s["vars"]:
				if var["defined"]:
					toc.append(class_template(kiss, var["value"], [mod.name]))

	out = kiss.render_template(
		"object.rst",
		{
			"title": title,
			"type": "module",
			"name": mod.fully_qualified_name,
			"sections": sections,
			"autodoc": autodoc,
			# separate modules (for root package), classes, and functions docs
			"toc": toc
		}
	)
	# returns output filename
	if write_file:
		return kiss.write_file("{}.rst".format(mod.name), out)
	# returns rendered contents
	return out

def package_template(kiss:RenderManager, pkg:PackageAPI):
	""" Render template for package """
	# first module is the "package" module
	pkg_mod = pkg.package

	mod_paths = []
	for mod in sorted(pkg.mods_tbl.values(), key=lambda m:m.name()):
		if mod is pkg_mod:
			continue
		path = module_template(kiss, mod, module_title(mod)+" Module")
		mod_paths.append(path)

	api_entrypoint = module_template(kiss, pkg_mod, module_title(pkg_mod)+" Package", mod_paths, True, write_file=False)
	return api_entrypoint