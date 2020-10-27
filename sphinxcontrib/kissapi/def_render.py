import re
from collections import defaultdict
from .introspect import (
    VariableTypes, ClassMemberTypes, ClassMemberBindings, parse_fqn, logger,
    VariableValueAPI, RoutineAPI, ClassAPI, ModuleAPI
)

def capitalize(name):
    return name[0].upper() + name[1:]

def categorize_members(obj, cat_cbk, titles:list, include_imports=True, include_external=False):
    """ Takes a module and organizes its members into sections
        :param obj: object whose members we want to categorize
        :param cat_cbk: callback to categorize a member, cbk(vardata, member_info)
        :param title_cbk: category -> category title callback, cbk(category)
    """
    # {category: [{... vardata ...}]}
    data = defaultdict(list)
    for vv, extra in obj.member_values.items():
        # context filter
        if not include_external and vv.is_external():
            continue
        src = vv.source_ref
        if src is None and vv.type != VariableTypes.MODULE:
            logger.warning("No source for variable:", vv.name)
        imported = src is not obj
        if not include_imports and imported:
            continue
        assert obj in vv.refs, "Object should be in variables refs to be in members!"
        aliases = vv.refs[obj]
        name = aliases[0]
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
        fqn = vv.fully_qualified_name
        # source name, we'll use the last two objects (e.g. module+var, class+member)
        fqn_parsed = parse_fqn(fqn)
        source_name = fqn_parsed[-1]
        short_source = ".".join(fqn_parsed[-2:])

        var = {
            "name": name,
            "aliases": aliases[1:],
            "order": vv.order(obj, name),
            "source": vv.fully_qualified_name.replace("::","."),
            "source_name": source_name,
            "source_short": short_source,
            "defined": not imported,
            "summary": summary["summary"],
            "signature": summary["signature"],
            "value": vv,
            "doc": doc
        }
        # categorize the variable
        data[cat_cbk(var, extra)].append(var)

    sorted_cats = []
    for cat in sorted(list(data.keys())):
        vars = data[cat]
        # personally, I find alphabetical easier to navigate, so only use 2nd val of tuple
        # to use src ordering, take that out
        vars.sort(key=lambda v: v["order"][1])
        sorted_cats.append({
            "title": titles[cat],
            "category": cat,
            "vars": vars
        })
    return sorted_cats

def categorize_class(clazz, *args, **kwargs):
    titles = [
        "Inner Classes",
        "Static Attributes",
        "Class Attributes",
        "Instance Attributes",
        "Static Methods",
        "Class Methods",
        "Instance Methods"
    ]
    def get_cat(var, extra):
        cat_type = ({
            ClassMemberTypes.INNERCLASS: 0,
            ClassMemberTypes.DATA: 1,
            ClassMemberTypes.METHOD: 2
        })[extra.type]
        if cat_type:
            cat_binding = ({
                ClassMemberBindings.STATIC: 1,
                ClassMemberBindings.CLASS: 2,
                ClassMemberBindings.INSTANCE: 3
            })[extra.binding]
            return (cat_type-1)*3+cat_binding
        return 0
    return categorize_members(clazz, get_cat, titles, True, True)

def categorize_module(mod, *args, **kwargs):
    titles = [
        "Data",
        "Functions",
        "Classes",
        "Referenced Modules",
        "Referenced Data",
        "Referenced Functions",
        "Referenced Classes"
    ]
    def get_cat(var, extra):
        cat = ({
            VariableTypes.MODULE: -1,
            VariableTypes.DATA: 0,
            VariableTypes.ROUTINE: 1,
            VariableTypes.CLASS: 2
        })[var["value"].type]
        if not var["defined"]:
            cat += 4
        assert cat >= 0, "Module must be imported"
        return cat
    return categorize_members(mod, get_cat, titles, *args, **kwargs)

def class_template(kiss, clazz, subdir:list, toc:list=None):
    if toc is None: toc = []
    sections = categorize_class(clazz)
    autodoc = []

    out = kiss.write_template(
        "{}/{}.rst".format("/".join(subdir), clazz.name),
        "object.rst",
        {
            "title": capitalize(clazz.name)+" Class",
            "type": "class",
            "name": clazz.fully_qualified_name.replace("::","."),
            "sections": sections,
            "toc": toc
        }
    )
    return out


def module_template(kiss, mod, title, toc:list=None, include_imports:bool=False):
    """ Render template for module. """
    if toc is None: toc = []
    sections = categorize_module(mod, include_imports)
    autodoc = []
    for s in sections:
        cat = s["category"]
        # we assume variables (DATA) docs are short, so we can include in main module page
        if cat == 0:
            lst = list(v["source"] for v in s["vars"] if v["defined"])
            if lst:
                autodoc.append({
                    "title": s["title"],
                    "type": "autodata",
                    "list": lst
                })
        # classes defined in this module
        elif cat == 2:
            for var in s["vars"]:
                if var["defined"]:
                    toc.append(class_template(kiss, var["value"], [mod.name]))

    out = kiss.write_template(
        "{}.rst".format(mod.name),
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
    return out

def mod_title(mod):
    """ return pretty title for module """
    return capitalize(mod.name.rsplit(".",1)[-1])

def package_template(kiss, pkg):
    """ Render template for package """
    # first module is the "package" module
    pkg_mod = pkg.package

    mod_paths = []
    for mod in sorted(pkg.mods_tbl.values(), key=lambda m:m.name):
        if mod is pkg_mod:
            continue
        path = module_template(kiss, mod, mod_title(mod)+" Module")
        mod_paths.append(path)

    pkg_path = module_template(kiss, pkg_mod, mod_title(pkg_mod)+" Package", mod_paths, True)
    return ".. include:: {}".format(pkg_path)