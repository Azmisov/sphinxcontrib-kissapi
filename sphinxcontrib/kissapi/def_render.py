from .introspect import VariableTypes

type_order = [
    # (type, title)
    (VariableTypes.MODULE, "Modules"),
    (VariableTypes.DATA, "Variables"),
    (VariableTypes.CLASS, "Classes"),
    (VariableTypes.ROUTINE, "Functions")
]
""" Ordering of VariableTypes for doc pages, along with the type titles """

def var_summaries(cur_mod, types, src_order=True):
    """ Get summarized information for variables, categorized and sorted by type """
    out = []
    for vtype,title in type_order:
        if vtype not in types: continue
        # list of variables of this type
        vlst = []
        for vv in types[vtype]:
            d = vv.documenter()
            s = d.summary()
            is_module = vtype == VariableTypes.MODULE
            # docs ordering; for module imports, alphabetical makes more sense
            s["order"] = d.order() if src_order and not is_module else s["name"]
            # path to the actual value definition
            s["qualified_name"] = vv.qualified_name
            # variable names from this module
            s["display_name"] = vv.refs[cur_mod][0]
            if len(vv.refs[cur_mod]) > 1:
                s["aliases"] = [x for x in vv.refs[cur_mod] if x != s["name"]]
            # defined in different module
            # if its a module with same name as module, we won't include this extra info
            if is_module and s["display_name"] != vv.name.rsplit(".", 1)[-1]:
                s["module"] = vv.name
                # this indicates that we don't need to put the source variable name, since source = variable
                s["is_module"] = True
            elif vv.best_ref and vv.best_ref != cur_mod:
                s["module"] = vv.best_ref.name
                # this allows it to put "Source: varname from module", if varname differs from cur_mod's varname
                s["is_module"] = False
            vlst.append(s)
        vlst.sort(key=lambda x: x["order"])
        out.append({
            "title": title,
            "type": vtype,
            "vars": vlst
        })
    return out

def function_template(kiss, fn):
    """ Render template for function """
    pass

def class_template(kiss, clazz):
    """ Render template for class """
    pass

def module_template(kiss, mod, title, toc=[]):
    """ Render template for module """
    # we assume variables (DATA) docs are short, so we can include in main module page
    autodoc = []
    # sorted by source for variables defined in module; imported vars sort by name
    def_vars = var_summaries(mod, mod.vars, True)
    for section in def_vars:
        if section["type"] != VariableTypes.DATA:
            continue
        autodoc.append({
            "title": section["title"],
            "type": "autodata",
            "list": [v["qualified_name"] for v in section["vars"]]
        })

    # for defined vars, we want to generate a separate folder with classes/functions
    #for vv in mod.vars.get(VariableTypes.CLASS, []):

    out = kiss.write_template(
        "{}.rst".format(mod.name),
        "module.rst",
        {
            "title": title,
            "module": mod.name,
            # summary information for variables
            "vars": def_vars,
            "aliased_vars": var_summaries(mod, mod.aliased_vars, False),
            "autodoc": autodoc,
            # separate modules (for root package), classes, and functions docs
            "toc": toc
        }
    )
    return out

def mod_title(mod):
    """ return pretty title for module """
    return mod.name.rsplit(".",1)[-1].title()

def package_template(kiss, pkg):
    """ Render template for package """
    mod_paths = []
    for mod in sorted(pkg.modules[1:], key=lambda m:m.name):
        path = module_template(kiss, mod, mod_title(mod)+" Module")
        mod_paths.append(path)

    # first module is the "package" module
    pkg_mod = pkg.modules[0]
    pkg_path = module_template(kiss, pkg_mod, mod_title(pkg_mod)+" Package", mod_paths)
    return ".. include:: {}".format(pkg_path)