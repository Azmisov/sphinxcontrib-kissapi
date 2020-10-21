""" This module contains all the "API" classes. The entry point is PackageAPI, which is passed the root module
    of the package you're interested in. It considers any module that is prefixed by the root module's name
    to be part of the package. Among those modules, it will take care of all the introspection to list
    modules, classes, variables, functions, etc.

    Idea here is that we can just have our own simple "documentation API" for accessing the package's API.
    Then you can do whatever you want with that, and generate API docs in your own format and style.

    Introspection has three main steps:
    1. Find target modules that are part of the package we want to analyze
    2. Build a table of all variables, as well as class attributes. Adding variables is broken into two steps, so
       that it can handle cyclical variable references:
        - Add variable to the table if not already (PackageAPI.add_variable)
        - Recursively analyze members or other nested variable information, then repeat (VariableValueAPI.analyze_members)
    3. Determine the best source (e.g. qualified name) for each of the variables; all other references to the
       variable value are considered aliases of the best source.

    TODO:
    - get_module for VVAPI
    - fully_qualified_name + qualified_name for VVAPI needs to be recursive
    - best_ref should not just get a module, but best parent
    - how to handle nested classes/functions? e.g. <locals> is in qualname
"""

import inspect, sys, types, weakref, enum, importlib
from typing import Union
from inspect import Parameter
from collections import defaultdict

from sphinx.ext.autosummary import get_documenter, mangle_signature, extract_summary
from sphinx.ext.autosummary import DocumenterBridge, Options
from sphinx.pycode import ModuleAnalyzer, PycodeError
from docutils.statemachine import StringList
from sphinx.util import logging
logger = logging.getLogger("kissapi_extension")

class VariableTypes(enum.IntEnum):
    """ variable types, categorized by those that autodoc can handle """
    MODULE = 0
    CLASS = 1
    ROUTINE = 2
    DATA = 3 # catchall for anything else
    @staticmethod
    def detect_type(val):
        if inspect.ismodule(val):
            return VariableTypes.MODULE
        if inspect.isclass(val):
            return VariableTypes.CLASS
        if inspect.isroutine(val):
            return VariableTypes.ROUTINE
        return VariableTypes.DATA

class ClassMemberTypes(enum.IntEnum):
    """ The types of class attributes """
    METHOD = 0
    DATA = 1
    INNERCLASS = 2

class ClassMemberBindings(enum.IntEnum):
    """ What form of the class is a class attribute bound to """
    STATIC = 0
    CLASS = 1
    INSTANCE = 2
    STATIC_INSTANCE = 3

class ClassMember:
    """ Holds various information on a class attribute's form """
    __slots__ = ["type","binding","value","reason"]
    def __init__(self, type: ClassMemberTypes, binding:ClassMemberBindings, value:"VariableValueAPI", reason:str):
        self.type = type
        self.binding = binding
        self.value = value
        self.reason = reason

class Immutable:
    def __init__(self, val):
        self.val = val
    @staticmethod
    def is_immutable(val, readonly=False):
        """ Truly immutable values, e.g. ``x is y`` is always true for two vars of these types
            But besides the "is" test, we want things that intuitively you would think copy when assigned,
            rather than referenced on assignment. The three main ones for that are:
                type, weakref.ref, types.BuiltinFunctionType
            which pass the "is" test, but they're basically referencing a fixed object, rather than copying
        """
        return isinstance(val, (str, int, float, bool, type(None), type(NotImplemented), type(Ellipsis)))
    @staticmethod
    def is_readonly(val):
        """ A number of types are considered immutable, but they fail the is_immutable test, so I'll call those "readonly".
            Currently method is not used, but keeping it for reference in case it is needed in future.
        """
        ro = isinstance(val, (
            complex, tuple, range, slice, frozenset, bytes, types.FunctionType, property,
            type, weakref.ref, types.BuiltinFunctionType
        ))
        if not ro:
            t = getattr(types, "CodeType", None)
            if t is not None:
                return isinstance(val, t)
        return ro

class InstancePlaceholder:
    """ This is a placeholder for variables that are not accessible, since they are actually
        class instance variables.
    """
    pass

class VariableValueAPI:
    """ A variable's value, along with a list of variable names and import options """
    __slots__ = ["_value","_doc","_analyzed","package","type","refs","best_ref","ext_refs","members"]
    def __init__(self, val, package:"PackageAPI", type:VariableTypes=None):
        if type is None:
            type = VariableTypes.detect_type(val)
        self.package = package
        """ (PackageAPI) the package this variable belongs to """
        self.type = type
        """ (VariableType) the type of this variable """
        self.refs = {}
        """ Parents which reference this variable. In the form ``{Module/ClassAPI/...: [varnames...]}`` """
        self.ext_refs = []
        """ module names not part of the package, but that include references to this variable value """
        self.best_ref = None
        """ The best source parent out of ``refs``. This is the object the variable's value was probably defined in;
            the chain of `best_ref`'s makes up the fully qualified name
        """
        self.members = {}
        """ mapping of variable name and values for sub-members of this object """

        if Immutable.is_immutable(val):
            val = Immutable(val)
        self._value = val
        """ raw value or Immutable wrapper of it """
        self._doc = None
        """ cached autodoc documenter """
        self._analyzed = False
        """ whether analyze_members has been called already """
    def id(self):
        # immutable types should hash to different vals, which is why we wrap in Immutable class
        return id(self._value)
    def add_ref(self, parent:"VariableValueAPI", name):
        """ Add a variable reference to this value. The parent should add the variable to its ``members`` dict, though
            the format that it saves it there can be customized.

            :param parent: the context that the value was referenced
            :param name: the variable name inside ``parent``
        """
        if parent not in self.refs:
            self.refs[parent] = [name]
        else:
            self.refs[parent].append(name)
    @property
    def value(self):
        """ The actual variable value """
        # immutable types have been wrapped, so need to extract
        if isinstance(self._value, Immutable):
            return self._value.val
        return self._value
    @property
    def name(self):
        """ Return primary variable name (from best_ref), or the module name if it is a module """
        if self.type == VariableTypes.MODULE:
            return self.value.__name__
        # TODO: use documenter to get first declared var name? (seems to be ordered correctly already though)
        if self.best_ref is None or not self.refs:
            logger.error("No best_ref set for %s, %s", str(self.value), str(self.type))
            return "NOREF({})".format(self._value)
        v = self.refs[self.best_ref]
        return v[0]
    def __repr__(self):
        return "<class {}:{}>".format(self.__class__.__qualname__, self._value)
    @property
    def qualified_name(self) -> str:
        """ get full qualified name, module + variable name """
        n = self.name
        if self.best_ref is not None:
            n = "{}.{}".format(self.best_ref.name, n)
        return n
    @property
    def fully_qualified_name(self) -> str:
        return "{}::{}".format(self.module, self.qualified_name)
    def is_special(self) -> bool:
        """ If variable name begins with double underscore """
        return self.name.startswith("__")
    def is_private(self) -> bool:
        """ If variable name begins with single underscore """
        return not self.is_special() and self.name.startswith("_")
    def is_external(self) -> bool:
        """ If this variable is referenced outside the package. In PackageAPI.var_exclude, we assume any
            variable included outside the package was defined outside the package, so shouldn't be included in
            the API
        """
        return bool(self.ext_refs)
    def is_immutable(self) -> bool:
        """ whether this is an immutable type, so there would never be references of this same variable """
        return isinstance(self._value, Immutable)
    def documenter(self) -> "Documenter":
        """ Get a Documenter object for this variable """
        if self._doc is None:
            if self.best_ref is None:
                self._doc = Documenter(self.name)
            # TODO: return a documenter for all references, rather than just the best_ref one?
            else:
                self._doc = Documenter(self.best_ref.name, self.name)
        return self._doc
    def analyze_members(self):
        """ Analyze sub-members of this variable. This should be implemented by subclasses """
        old = self._analyzed
        self._analyzed = True
        return old

class RoutineAPI(VariableValueAPI):
    """ Specialization of VariableValueAPI for routines. That includes things like function, lambda, method, c extension
        function, etc. It holds a reference to the underlying function along with the parents it is bound to
    """
    __slots__ = ["bound_selfs","base_function"]
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bound_selfs = []
        """ objects the base_function was bound to, ordered"""
        self.base_function = self
        """ the base callable before being bound """
        self.package.fqn_tbl[self.fully_qualified_name] = self
    @property
    def qualified_name(self) -> str:
        return self.base_function.value.__qualname__
    @property
    def module(self) -> str:
        return self.base_function.value.__module__
    def analyze_members(self):
        """ Analyze root function of the routine. This extracts out any class/object bindings so that
            we can examine the base function that eventually gets called
        """
        if super().analyze_members(): return
        root = self.value
        while True:
            parent = getattr(root, "__self__", None)
            if parent is None:
                break
            par_vv = self.package.add_variable(parent)
            # don't have a variable name for this reference, but we do have bound index
            idx = len(self.bound_selfs)
            par_vv.add_ref(self, idx)
            self.bound_selfs.append(par_vv)
            root = getattr(root, "__func__")
        if root is not self.value:
            root_vv = self.package.add_variable(root)
            root_vv.add_ref(self, "__func__")
            self.base_function = root_vv

class ClassAPI(VariableValueAPI):
    """ Specialization of VariableValueAPI for class types. This will autodetect methods and attributes """
    __slots__ = ["attrs"]
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.attrs = {}
        """ Holds an index of attributes for this class. Each of them are a dict containing the following
            items: ``type``, ``binding``, ``reason``, and ``value``. See :meth:`~classify_members` for details
        """
        self.package.fqn_tbl[self.fully_qualified_name] = self
    @property
    def qualified_name(self) -> str:
        return self.value.__qualname__
    @property
    def module(self) -> str:
        return self.value.__module__
    def analyze_members(self):
        """ Analyze attributes of the class. That includes static, class, and instance
            methods, classes, and variables
        """
        if super().analyze_members(): return
        raw_attrs = self.classify_members()
        cont_mod = self.package.fqn_tbl[self.module]
        raw_inst_attrs = cont_mod.instance_attrs(self.qualified_name)
        for k in raw_inst_attrs:
            # this may override __slots__ members, but that's okay
            # we'll assume ModuleAnalyzer has better info about the docs, so we'll let it take priority
            raw_attrs[k] = {
                "type": "data", # technically, could be function, but we can't know for sure
                "binding": "instance",
                "reason": "moduleanalyzer",
                "value": InstancePlaceholder()
            }
        # convert attributes to vvapis
        for k,v in raw_attrs.items():
            vv = self.package.add_variable(v["value"])
            vv.add_ref(self, k)
            is_inner = isinstance(vv, ClassAPI) and vv.fully_qualified_name.startswith(self.fully_qualified_name+".")
            # classify_members gives some extra info that is redundant, so we create our own dict
            self.members[k] = ClassMember(
                ClassMemberTypes.INNERCLASS if is_inner else v["type"],
                v["binding"], vv, v["reason"]
            )

    def classify_members(self):
        """ Retrieve attributes and their types from a class. This does a pretty thorough examination of the attribute
            types, and can handle things like: `classmethod`, `staticmethod`, `property`, bound methods, methods defined
            outside the class, signature introspection, and slots.

            :param cls: the class to examine
            :returns: (dict) a mapping from attribute name to attribute type; attribute type is a dict with these items:

                - ``type``: "method", for routines; "data", for properties, classes, and other data values
                - ``source``: (optional) if the attribute was a routine or property, the fully qualified name to the source code
                - ``parents``: ("method" only) parents that were bound to the routine
                - ``function``: ("method" only) the root function, before being bound to ``parents``
                - ``binding``: "static", "class", "instance", or "static_instance"; the distinctions are not super clear-cut
                  in python, so this is more of a loose categorization of what scope the attribute belongs to; here is a
                  description of each:
                    - "static": attributes that are not defined in slots or properties; for routines, those which are
                      explicitly static methods, bound to other non-class types, or unbound but has a signature that is
                      unable to be called on instances
                    - "class": routines which are bound to the class
                    - "instance": attributes defined in slots; for routines, unbound methods which would accept a `self`
                      first argument
                    - "static_instance": this is the outlier case, where a routine was bound to a class instance, so while
                      technically it is an instance method, it is only bound to that one instance (perhaps a singleton), so
                      may be better categorized as a static member
                - ``reason``: a string indicating a reason why we categorized it under that ``binding`` type
                - ``value``: bound value for this attribute
        """
        attrs = {}
        cls = self.value
        for var,val in cls.__dict__.items():
            # this goes through descriptor interface, which binds the underlying value to the class/instance if needed
            bound_val = getattr(cls, var)

            """ slots are instance only data vals; it will throw an error if slot conflicts with class variable
                so no need to worry about overriding other slot vars; slots create member descriptors on the class,
                which is why they show up when you iterate through __dict__
                other details: https://docs.python.org/3/reference/datamodel.html?highlight=slots#notes-on-using-slots
            """
            slots = set()
            raw_slots = getattr(cls, "__slots__", None)
            if raw_slots is not None and len(slots):
                for attr in iter(slots):
                    if attr == "__dict__" or attr == "__weakref__":
                        continue
                    slots.add(attr)

            # figure out what type of attribute this is
            binding = None
            def source(f):
                """ fully qualified name for function """
                return "{}::{}".format(f.__module__, f.__qualname__)
            # this gets functions, methods, and C extensions
            if inspect.isroutine(val):
                # find the root function, and a list of bound parents
                parents = []
                root_fn = bound_val
                while True:
                    parent = getattr(root_fn, "__self__", None)
                    if parent is None:
                        break
                    parents.append(parent)
                    root_fn = getattr(root_fn, "__func__")
                binding = {
                    "type":ClassMemberTypes.METHOD,
                    "source": source(root_fn),
                    "parents": parents,
                    "function": root_fn
                }
                # bound to the class automatically
                if isinstance(val, classmethod):
                    extras = {
                        "binding":ClassMemberBindings.CLASS,
                        "reason":"classmethod",
                    }
                # will not be bound to the class/instance
                elif isinstance(val, staticmethod):
                    # if it is bound to the class, then it is actually behaving like classmethod
                    if cls in parents:
                        extras = {
                            "binding": ClassMemberBindings.CLASS,
                            "reason": "bound_staticmethod",
                        }
                    else:
                        extras = {
                            "binding":ClassMemberBindings.STATIC,
                            "reason":"staticmethod",
                        }
                # a normal function, but bound to the class; it will behave like classmethod
                # python won't bind it to an instance, as it is already bound
                elif cls in parents:
                    extras = {
                        "binding":ClassMemberBindings.CLASS,
                        "reason":"bound"
                    }
                # if it is bound to an instance, then it is sort of an instance/static hybrid, since
                # it won't be bound to new instances... just the initial one
                elif any(isinstance(p, cls) for p in parents):
                    extras = {
                        "binding":ClassMemberBindings.STATIC_INSTANCE,
                        "reason":"bound"
                    }
                # other bound parents that are unrelated to this class
                # could classify these as "inherited" if we find an inherited method with same type and name
                elif parents:
                    extras = {
                        "binding":ClassMemberBindings.STATIC,
                        "reason":"bound"
                    }
                # unbound method; these are candidates for instance methods
                else:
                    # if it has no arguments, we should treat it as static instead
                    # this could throw ValueError if signature is invalid for this binding; that would be a user bug though
                    first_arg = None
                    try:
                        sig = inspect.signature(root_fn).parameters
                        first_arg = next(iter(sig.values()),None)
                    except ValueError: pass
                    allowed_kinds = [
                        Parameter.VAR_POSITIONAL,
                        Parameter.POSITIONAL_OR_KEYWORD,
                        getattr(Parameter, "POSITIONAL_ONLY", None) # python 3.8+
                    ]
                    if first_arg and first_arg.kind in allowed_kinds:
                        extras = {
                            "binding": ClassMemberBindings.INSTANCE,
                            "reason": "unbound"
                        }
                    else:
                        extras = {
                            "binding": ClassMemberBindings.STATIC,
                            "reason": "signature"
                        }
                binding.update(extras)
            elif isinstance(val, property):
                binding = {
                    "type": ClassMemberTypes.METHOD,
                    "binding": ClassMemberBindings.INSTANCE,
                    "reason":"property",
                    "source": source(val.fget)
                }
            elif var in slots:
                binding = {
                    "type":ClassMemberTypes.METHOD,
                    "binding": ClassMemberBindings.INSTANCE,
                    "reason":"slots"
                }
            else:
                binding = {
                    "type":ClassMemberTypes.METHOD,
                    "binding":ClassMemberBindings.STATIC,
                    "reason":"other"
                }
            binding["value"] = bound_val
            attrs[var] = binding

        return attrs

class ModuleAPI(VariableValueAPI):
    """ Specialization of VariableValueAPI for module types. The main thing is it keeps track of the
        list of importable variables, those that were defined within the module and those that were not. This
        class also holds a ``ModuleAnalyzer``, which can be used to get documentation for class instance attributes.
    """
    __slots__ = ["imports","analyzer"]
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.imports = set()
        """ list of modules this module imports; only includes modules part of the package """
        # source code analysis; this gives you extra info like the estimated order variables were defined in source file
        try:
            self.analyzer = ModuleAnalyzer.for_module(self.name)
            """ autodoc ModuleAnalyzer object to introspect things not possible from code. This includes things like
                class instance documentation and variable definition source code ordering. 
            """
            self.analyzer.parse()
        except PycodeError as e:
            logger.warning("could not analyze module %s", self.name, exc_info=e)
            self.analyzer = None

    @property
    def fully_qualified_name(self) -> str:
        return self.module
    @property
    def qualified_name(self) -> str:
        return self.module
    @property
    def module(self) -> str:
        return self.value.__name__

    def __repr__(self):
        return "<class ModuleAPI:{}>".format(self.name)
    def order(self, name) -> tuple:
        """ Get the source ordering. This is not the line number, but a rank indicating which the order variables
            were declared in the module.

            :returns: tuple (order, name) which can be used as sort key; if the order cannot be determined (the variable
                was not found in the source module), +infinity is used instead
        """
        if self.analyzer is not None:
            o = self.analyzer.tagorder.get(name, float("inf"))
        else:
            o = float("inf")
        return (o, name)
    def instance_attrs(self, name) -> list:
        """ Get a list of documented instance-level attributes for object `name` (e.g. Class/enum/etc) """
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
        for var, val in inspect.getmembers(self.value):
            vv = self.package.add_variable(val)
            # add reference to this module
            vv.add_ref(self, var)
            # this module imports another in the package
            if isinstance(vv, ModuleAPI):
                self.imports.add(vv)

class PackageAPI:
    __slots__ = ["name","package","mods_tbl","ext_tbl","var_tbl","fqn_tbl","_need_analysis"]
    def __init__(self, pkg: types.ModuleType, options:dict={}):
        """ Parse a root module and extract the API for it inside [XXX]API classes """
        # find all modules that are part of this package
        self.name = pkg.__name__
        """ Name of the package (that being the name of the main package's module) """
        self.ext_tbl = defaultdict(list)
        """ importable variables from external modules, ``{variable_id: [module_name]}`` """
        self.mods_tbl = {}
        """ modules part of the package, ``{module_name: ModuleAPI}`` """
        self.var_tbl = {}
        """ all importable variables from the package, ``{variable_id: VariableValueAPI}`` """
        self._need_analysis = []
        """ list of vars from var_tbl that still need analysis """
        self.fqn_tbl = {}
        """ Lookup table for fully qualified names, mapping to VariableValueAPI objects. This does not contain
            all variables, just those that encode raw qualified name data, like classes, functions, and modules. 
        """
        self.package = self.add_variable(pkg, True)
        """ The module entry-point for the package. Also accessible as first element of `modules` """

        # get package modules
        package_exclude = options.get("package_exclude",PackageAPI.package_exclude)
        # examining modules can sometimes lazy load others
        mods_seen = set()
        while True:
            seen_new = False
            for k in list(sys.modules.keys()):
                if k == self.name or k in mods_seen:
                    continue
                mods_seen.add(k)
                seen_new = True
                if package_exclude(self.name, k):
                    self.add_external_module(k)
                    continue
                self.add_variable(sys.modules[k], True)
            if not seen_new:
                break

        # analyse all variables recursively; this happens in two steps, so that we can handle circular references
        # (e.g. can't analyze var members until var has been added itself)
        while True:
            to_analyze = self._need_analysis
            self._need_analysis = []
            if not to_analyze:
                break
            for vv in to_analyze:
                vv.analyze_members()

        # we've indexed all variables and imports; now we guess source definition qualname of variables
        var_exclude = options.get("var_exclude",PackageAPI.var_exclude)
        source_object = options.get("source_object", PackageAPI.source_object)
        for vv in self.var_tbl.values():
            """ first, determine what the best source module is for a variable (where variable was defined);
                modules are their own source, so doesn't make sense to set it for module types;
                class/functions have __module/qualname__ reference they use to set best reference, so all that's left
                is DATA types
            """
            need_source_resolution = vv.type == VariableTypes.DATA
            if isinstance(vv, (RoutineAPI, ClassAPI)):
                # qualname is guaranteed to be within package's allowed modules, since otherwise we would have made it DATA type
                name = vv.module
                attr_split = vv.qualified_name.rsplit(".",1)
                if len(attr_split) > 1:
                    name += "::"+attr_split[0]
                # type is nested inside function, so not importable directly; it must have been referenced in some
                # accessible way, or is bound to a RoutineAPI which is accessible; so defer to source resolution to
                # figure out what the best ref is for these
                if "<locals>" in name:
                    print("FOUND LOCALS IN THINGY", name)
                    need_source_resolution = True
                elif name not in self.fqn_tbl:
                    raise RuntimeError("Variable has importable qualname {}, but {} not found".format(vv.fully_qualified_name, name))
                else:
                    vv.best_ref = self.fqn_tbl[name]
                    print("Found: /{}/, /{}/".format(vv.fully_qualified_name, vv.best_ref))
                    if vv.best_ref not in vv.refs:
                        print("Bad")
                        print(vv.best_ref)
                        print(vv.refs)
                        raise RuntimeError("not in refs though")
            if need_source_resolution:
                # DATA variable defined in multiple modules; have to do some more in depth analysis
                # to determine which module is the "true" source of the variable
                if len(vv.refs) > 1:
                    vv.best_ref = source_object(self.name, vv)
                else:
                    vv.best_ref = next(iter(vv.refs))
            """ we have a module assignment, so we can get the "official" variable name and docs;
                user can use those to determine if the variable should be included or not
                user already accepted package modules for inclusion in package_exclude callback
                
                TODO: move this to member iterator methods
            """
            #if not isinstance(vv, ModuleAPI) and var_exclude(self.name, vv):

    def add_external_module(self, mod_name:str):
        """ Mark all variables from the module given as "external" variables, outside the package

            :param str mod_name: the external module name
        """
        try:
            mod = sys.modules[mod_name]
            # don't care about executable modules I guess
            if not isinstance(mod, types.ModuleType):
                return
            mod_iter = inspect.getmembers(mod)
        except Exception as e:
            logger.debug("Can't determine external refs in module %s", mod_name, exc_info=e)
            return
        for var, val in mod_iter:
            if not Immutable.is_immutable(val):
                self.ext_tbl[id(val)].append(mod_name)
        # modules itself is external
        self.ext_tbl[id(mod)].append(mod_name)

    def add_variable(self, val, package_module: bool = False):
        """ Add a variable to the variable lookup table. This is the factory method for all other ``[type]API`` classes.

            :param val: variable value
            :param bool package_module: if True, add to mods_tbl as a ModuleAPI object; make sure to add all these
                package modules first, before other variables
            :returns: the ``[type]API`` for this value, possibly newly created if it hasn't been seen before
        """
        if id(val) in self.var_tbl:
            return self.var_tbl[id(val)]

        vtype = VariableTypes.detect_type(val)
        clazz = VariableValueAPI
        # class/function have __module__ we can set as best_ref
        best_ref = None
        if vtype == VariableTypes.MODULE:
            if package_module:
                clazz = ModuleAPI
            else:
                package_module = False
        # class/function that are not one of the package's modules will be converted
        # to DATA type, since we won't want to document externally defined stuff in this package
        elif vtype != VariableTypes.DATA:
            try:
                best_ref = self.mods_tbl.get(val.__module__, None)
                if best_ref is None:
                    vtype = VariableTypes.DATA
            except:
                vtype = VariableTypes.DATA
        if vtype == VariableTypes.CLASS:
            clazz = ClassAPI
        elif vtype == VariableTypes.ROUTINE:
            clazz = RoutineAPI
        # create
        vv = clazz(val, self, vtype)
        vv_id = vv.id()
        self.var_tbl[vv_id] = vv
        # check if any external modules reference this variable
        if vv_id in self.ext_tbl:
            vv.ext_refs = self.ext_tbl[vv_id]
        # module part of package
        if package_module:
            self.mods_tbl[vv.name] = vv
        self._need_analysis.append(vv)
        # add fully qualified name
        if isinstance(vv, (ModuleAPI, ClassAPI, RoutineAPI)):
            self.fqn_tbl[vv.fully_qualified_name] = vv
        return vv

    # Following methods are default options
    @staticmethod
    def package_exclude(pkg_name:str, module_name:str):
        """ Default package module exclusion callback. This ignores modules that are:

            - outside pkg_name scope: don't begin with "[pkg_name]."
            - private: where there is some module in the path prefixed by underscore
            - "executable": the module is not a ModuleType
        """
        # ignore if not in same namespace as package
        if not module_name.startswith(pkg_name+"."):
            return True
        # ignore private modules
        if any(x.startswith("_") for x in module_name[len(pkg_name)+1:].split(".")):
            return True
        # ignore executable packages
        if not isinstance(sys.modules[module_name], types.ModuleType):
            return True
    @staticmethod
    def var_exclude(pkg_name:str, var:VariableValueAPI):
        """ Default variable exclusion callback. This ignores variables that are:

            - private: variable begins with single underscore (see :meth:`~VariableValueAPI.is_private`)
            - external: variable found outside the package modules, and so we assume was defined outside as well
              (see :meth:`~VariableValueAPI.is_external`)
            - special: variable begins with double underscore (see :meth:`~VariableValueAPI.is_special`); this allows
              two exceptions, ``__all__`` and ``__version__`` within the ``pkg_name`` module
        """
        if var.is_private() or var.is_external():
            return True
        # allow __all/version__ for the package module itself
        special_include = ["__all__","__version__"]
        if var.is_special():
            allowed = var.name in special_include and any(mod.name == pkg_name for mod in var.refs)
            return not allowed
    @staticmethod
    def source_object(pkg_name:str, vv:VariableValueAPI):
        """ Default source object resolution callback.
            Unfortunately, it is not possible to really tell *where* a variable came from (see https://stackoverflow.com/questions/64257527/).
            A module's members may have been imported from another module, so you can't say exactly what module a variable
            belongs to. Or if a variable is the same across multiple classes, which class was it defined in initially?
            So you have to make some assumptions. Here's what I'll do:

            - assume any ModuleType's inside a module, that are also in sys.modules, were imported
            - if a function or class has __module__ attribute, then assume variable came from that module
            - if variable is referenced in both a ClassAPI and ModuleAPI:
                - if the variable is bound to a class (__self__
              manually set as a class instance, e.g. via
            - for all other variables, keep a list of references to the modules in sys.modules which contain that variable
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
            TODO: prefer ModuleAPI over ClassAPI if defined in both
        """
        # already handled the first two cases in __init__, so all the rest are DATA type
        # those could be primitives, class instances, etc
        class AtomicNode:
            """ a node in the import graph """
            def __init__(self, m):
                self.modapi = m
                self.out_ct = 0 # stuff that imports m
                self.in_ct = 0 # stuff m imports
                # this are AtomicNode/MetaNode's representing the imported stuff
                # it doesn't necessarily match up with in_ct, since things may have merged into MetaNode's
                self.in_nodes = set()
        class MetaNode:
            def __init__(self, merge):
                self.nodes = set()
                self.in_nodes = set()
                for n in merge:
                    # don't allow nested MetaNode's
                    if isinstance(n, MetaNode):
                        self.nodes.update(n.nodes)
                    else:
                        self.nodes.add(n)
                    self.in_nodes.update(n.edge_in)
                self.in_nodes -= self.nodes

        graph_nodes = set()
        graph_nodes_lookup = {}
        # construct nodes and edges
        for m in vv.refs.keys():
            node = AtomicNode(m)
            graph_nodes.add(node)
            graph_nodes_lookup[m] = node
        for node in graph_nodes:
            for mi in node.modapi.imports:
                if mi in graph_nodes_lookup:
                    mi_node = graph_nodes_lookup[mi]
                    mi_node.out_ct += 1
                    node.in_ct += 1
                    node.in_nodes.add(mi_node)

        # To detect cycles, we do depth first traversal; if we have already seen a node, then everything in the
        # stack between that node makes up a cycle. We merge those into a MetaNode (making sure to update edges as well)
        # Keep doing that until we can get through the whole graph without detecting a cycle
        class CycleDetected(Exception): pass
        def dfs_traversal(node, visited, stack):
            if node in visited:
                if node in stack:
                    stack.append(node)
                    raise CycleDetected()
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
            except CycleDetected:
                # remove the cycle
                cycle = stack[stack.index(stack[-1]) : -1]
                graph_nodes -= cycle
                cnode = MetaNode(cycle)
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
                if isinstance(node, MetaNode):
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
        obj = vv.value
        while type(obj) != type:
            obj = type(obj)
            if hasattr(obj, "__module__") and obj.__module__ in cnames:
                return cnames[obj.__module__]

        # if that fails, then we go by imported by counts, falling back to import counts or name
        candidates = list(candidates)
        candidates.sort(key=lambda m: (-m.out_ct, m.in_ct, m.modapi.name))
        return candidates[0].modapi

class Documenter:
    directive = None
    """ a reference to a sphinx directive """
    options = None
    """ autodoc documenter params; copied from autosummary """
    @classmethod
    def bind_directive(cls, directive):
        """ autodoc stuff needs to reference sphinx directive for something internal
            So this sets up the link to a sphinx directive object. At very least, the sphinx app
            gives you a registry of all the autodoc Documenter classes that are available.
        """
        cls.directive = directive
        cls.options = DocumenterBridge(directive.env, directive.state.document.reporter, Options(), directive.lineno, directive.state)

    __slots__ = ["module_name","var_name","short_name","module","value","doc"]

    def __init__(self, module_name:str, var_name:str=None):
        """ Retrieves the same kind of "Documenter" object that autodoc would use to document this variable
            Pass in the directive class, which autodoc wants, which should includde an additional "bridge"
            attribute with DocumenterBridge options
        """
        if Documenter.directive is None or Documenter.options is None:
            raise RuntimeError("must call bind_directive before Documenter will work")

        # if var_name not given, then we document the module instead
        self.module_name = module_name
        self.var_name = var_name
        self.short_name = var_name or module_name
        self.module = sys.modules[module_name]
        if var_name is None:
            self.value = None
            value = self.module
            parent = None
            full_name = self.module_name
        else:
            self.value = getattr(self.module, var_name)
            value = self.value
            parent = self.module
            full_name = "{}::{}".format(module_name, var_name)

        # The rest here was copied/adapted from autosummary source code
        doc_type = get_documenter(Documenter.directive.env.app, value, parent)
        self.doc = doc_type(Documenter.options, full_name)
        if not self.doc.parse_name():
            raise RuntimeError("documenter parse_name failed: {}".format(full_name))
        if not self.doc.import_object():
            raise RuntimeError("documenter import_object failed: {}".format(full_name))
        # autosummary does a check to make sure it is exported; we know it is already
        # source code analysis; this gives you extra info like the estimated order variables were defined in source file
        try:
            # we have more intelligent info on the target module
            self.doc.analyzer = ModuleAnalyzer.for_module(self.module_name)
            self.doc.analyzer.find_attr_docs()
        except PycodeError as e:
            logger.warning("could not analyze module for documenter: %s", full_name, exc_info=e)
            self.doc.analyzer = None

    def summary(self, max_name_chars:int=50):
        """ Gets doc summary, as would be returned by autosummary extension

            :param max_name_chars: do not give full signature if it would cause the full name+signature to exceed this
                number of characters, instead using "..." to fill in missing params; at minimum we will return "(...)"
                if there is a signature
        """
        # this is copied from autosummary source
        try:
            sig = self.doc.format_signature(show_annotation=False)
        except TypeError:
            sig = self.doc.format_signature()
        if not sig:
            sig = ''
        else:
            max_chars = max(5, max_name_chars-len(self.short_name))
            sig = mangle_signature(sig, max_chars=max_chars)
        # don't know what this line does, but if not there extract_summary doesn't work
        # guess extract_summary is writing docutils nodes to the autodoc documenter object
        Documenter.options.result = StringList()
        self.doc.add_content(None)
        summary = extract_summary(Documenter.options.result.data[:], Documenter.directive.state.document)
        return {
            "name": self.short_name,
            "signature": sig,
            "summary": summary
        }

    def order(self, name=None):
        """ Get relative order index that this variable was declared

            :param name: optionally use the variable's module to lookup order of an arbitrary variable name,
                rather than the one passed into the Documenter constructor
            :returns: tuple, (src_order, name); if src_order can't be determined for whatever reason (probably the
                requested variable isn't part of the module), then it sets to +inf; with the tuple, it will fallback
                to ordering alphabetically in that case
        """
        if name is None:
            name = self.var_name
        return (self.doc.analyzer.tagorder.get(name, float("inf")), self.short_name)

"""
# this looks like how it can generate docs for self.xxx of class
analyzer = ModuleAnalyzer.for_module(self.modname)
attr_docs = analyzer.find_attr_docs()

# if object is not in attr_docs, it falls back to getdoc method
# that I think just gets __doc__ string

# autodoc Documenters want to generate reST content
doc.add_content(additional_content, don't_include_docstring)

# will return 
doc.process_doc

"""


pkg_memoize = {}
def analyze_package(pname:str, *args, **kwargs):
    """ Factory for PackageAPI. It memoizes previously analyzed packages
        and reuses the results if possible; otherwise, it will import the module
        and create a new PackageAPI

        .. Note::
            Memoizing does not consider differing PackageAPI ``options`` arguments
    """
    if pname in pkg_memoize:
        return pkg_memoize[pname]
    m = importlib.import_module(pname)
    api = PackageAPI(m, *args, **kwargs)
    pkg_memoize[pname] = api
    return api