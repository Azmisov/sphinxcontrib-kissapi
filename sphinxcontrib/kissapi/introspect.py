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
        - Use GC instead to get references, rather than having the ``analyze_members`` methods that has to be specialized
          for each type. I think it could end up being a lot cleaner.
        - how to handle nested classes/functions? e.g. <locals> is in qualname; currently I'm ignoring them
"""

import inspect, sys, types, weakref, enum, importlib, re
from typing import Union
from inspect import Parameter
from collections import defaultdict
from functools import partial, partialmethod

from sphinx.ext.autodoc import (
    PropertyDocumenter, ModuleDocumenter, AttributeDocumenter, SlotsAttributeDocumenter,
    InstanceAttributeDocumenter, MethodDocumenter, DataDocumenter, ClassDocumenter, FunctionDocumenter
)
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
        if inspect.isroutine(val) or isinstance(val, (partial, partialmethod, property)):
            return VariableTypes.ROUTINE
        if inspect.ismodule(val):
            return VariableTypes.MODULE
        if inspect.isclass(val):
            return VariableTypes.CLASS
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

def is_special(name) -> bool:
    """ If variable name begins with double underscore """
    return name.startswith("__")
def is_private(name) -> bool:
    """ If variable name begins with single underscore """
    return not is_special(name) and name.startswith("_")
def parse_fqn(name) -> list:
    """ Converts a fully qualified name into a list. First entry is always the module. Fully
        qualified name should be in a form like so: ``module::class.member.submember``
    """
    fqn_split = name.split("::")
    if len(fqn_split) > 1:
        vars = fqn_split[1].split(".")
        vars.insert(0, fqn_split[0])
        return vars
    return fqn_split

class VariableValueAPI:
    """ A variable's value, along with a list of variable names and import options """
    __slots__ = [
        "_value","_doc","_analyzed","package","type","refs",
        "_source_ref","ext_refs","members","member_values"
    ]
    def __init__(self, val, package:"PackageAPI", vtype:VariableTypes=None):
        if vtype is None:
            vtype = VariableTypes.detect_type(val)
        self.package = package
        """ (PackageAPI) the package this variable belongs to """
        self.type = vtype
        """ (VariableType) the type of this variable """
        self.refs = {}
        """ Parents which reference this variable. In the form ``{Module/ClassAPI/...: [varnames...]}`` """
        self.ext_refs = []
        """ module names not part of the package, but that include references to this variable value """
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
        """ raw value or Immutable wrapper of it """
        self._doc = None
        """ cached autodoc documenter """
        self._analyzed = False
        """ whether analyze_members has been called already """
        self._source_ref = None

    def id(self):
        # immutable types should hash to different vals, which is why we wrap in Immutable class
        return id(self._value)
    def add_ref(self, parent:"VariableValueAPI", name):
        """ Add a variable reference to this value. This will not add the reference if the variable exclude callback
            returns True. You can use the return value to call ``add_member` on the parent in whatever custom format
            you desire.

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
    def add_member(self, name, raw_value:"VariableValueAPI", custom_value=None):
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
        if self.type == VariableTypes.MODULE:
            return self.value.__name__
        # TODO: use ``order`` to get first declared var name? (seems to be ordered correctly already though)
        if self.source_ref is None or not self.refs:
            val_str = str(self._value)
            LEN_LIMIT = 25
            if len(val_str) > LEN_LIMIT:
                val_str = val_str[:LEN_LIMIT-3]+"..."
            name = "anonymous<{}>".format(val_str)
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
        if self.type == VariableTypes.MODULE:
            return self.name
        if self.source_ref is not None:
            return self.source_ref.module
        return ""
    @property
    def fully_qualified_name(self) -> str:
        """ A combination of module and qualified name separated by "::". If it is a module, just the module half
            is returned. If it has no ``__module__`` attribute, it uses the chain of source_ref's to mimic such,
            giving an empty string if a source_ref is missing.
        """
        mn = self.module
        qn = self.qualified_name
        if qn:
            return "{}::{}".format(mn, qn)
        return mn
    def __repr__(self):
        return "<class {}:{}>".format(self.__class__.__qualname__, self.name)

    def is_special(self) -> bool:
        """ If variable name begins with double underscore """
        return is_special(self.name)
    def is_private(self) -> bool:
        """ If variable name begins with single underscore """
        return is_private(self.name)
    def is_external(self) -> bool:
        """ If this variable is referenced outside the package. In PackageAPI.var_exclude, we assume any
            variable included outside the package was defined outside the package, so shouldn't be included in
            the API. Class/function/modules that are plain VariableValueAPI objects are external, by virtue
            of their ``__module__`` value.
        """
        return (self.__class__ is VariableValueAPI and self.type != VariableTypes.DATA) or bool(self.ext_refs)
    def is_immutable(self) -> bool:
        """ whether this is an immutable type, so there would never be references of this same variable """
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
                if allow_nosrc  and self.type == VariableTypes.MODULE:
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
        ref, name, qn, fqn = self._ref_qualified_name(ref, name)
        return Documenter(fqn, self, ref, name)

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

            Inside PackageAPI, we use __moodule__+__qualname__ to set the source_ref directly. However, may not meet
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
                idx = len(self.bound_selfs)
                # TODO: should we add bound index here? I think for now no, and we
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

class ClassAPI(VariableValueAPI):
    """ Specialization of VariableValueAPI for class types. This will autodetect methods and attributes """
    instance_finder = re.compile(r"\s+self\.(\w+)\s*=")
    __slots__ = ["attrs"]
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.attrs = {}
        """ Holds an index of attributes for this class. Each of them are a dict containing the following
            items: ``type``, ``binding``, ``reason``, and ``value``. See :meth:`~classify_members` for details
        """
        self.package.fqn_tbl[self.fully_qualified_name] = self
    @property
    def name(self) -> str:
        return self.qualified_name.rsplit(".",1)[-1]
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
        # ModuleAnalyzer can't say if it is an instance method
        # this parses source code looking for "self.XXX", and we'll assume those are instance attributes
        inst_eligible = set()
        init = getattr(self.value, "__init__", None)
        if init is not None:
            try:
                src = inspect.getsource(init)
                inst_eligible = set(ClassAPI.instance_finder.findall(src))
            except: pass
        for k in raw_inst_attrs:
            isinst = k in inst_eligible
            if k in raw_attrs:
                old = raw_attrs[k]
                # we know it is being used as instance variable now
                if isinst and old["binding"] != ClassMemberBindings.INSTANCE and old["type"] == ClassMemberTypes.DATA:
                    old["binding"] = ClassMemberBindings.INSTANCE
                    old["reason"] = "moduleanalyzer"
            # not seen before
            else:
                raw_attrs[k] = {
                    "type": ClassMemberTypes.DATA, # technically, could be function, but we can't know for sure
                    "binding": ClassMemberBindings.INSTANCE if isinst else ClassMemberBindings.STATIC,
                    "reason": "moduleanalyzer",
                    "value": InstancePlaceholder()
                }
        # convert attributes to vvapis
        for k,v in raw_attrs.items():
            vv = self.package.add_variable(v["value"])
            if vv.add_ref(self, k):
                is_inner = isinstance(vv, ClassAPI) and vv.fully_qualified_name.startswith(self.fully_qualified_name+".")
                # classify_members gives some extra info that is redundant, so we create our own dict
                cust_val = ClassMember(
                    ClassMemberTypes.INNERCLASS if is_inner else v["type"],
                    v["binding"], vv, v["reason"]
                )
                self.add_member(k, vv, cust_val)

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
        """ slots are instance only data vals; it will throw an error if slot conflicts with class variable
            so no need to worry about overriding other slot vars; slots create member descriptors on the class,
            which is why they show up when you iterate through __dict__
            other details: https://docs.python.org/3/reference/datamodel.html?highlight=slots#notes-on-using-slots
        """
        slots = set()
        raw_slots = getattr(cls, "__slots__", None)
        if raw_slots is not None:
            if isinstance(raw_slots, str):
                raw_slots = [raw_slots]
            # could be dict, list, or tuple I think
            for attr in iter(raw_slots):
                # these two are special and just indicate the attrs should not be *removed* from class definition
                if attr == "__dict__" or attr == "__weakref__":
                    continue
                slots.add(attr)
        def source(f):
            """ fully qualified name for function """
            return "{}::{}".format(f.__module__, f.__qualname__)
        # Note that when __slots__ is defined and doesn't contain __dict__, __dict__ will not be available on class instances
        # However, we're introspecting on the *class* itself, not an instance; and __dict__ will always be available in this case
        for var,val in cls.__dict__.items():
            # this goes through descriptor interface, which binds the underlying value to the class/instance if needed
            bound_val = getattr(cls, var)

            # figure out what type of attribute this is
            # this gets functions, methods, and C extensions
            if inspect.isroutine(val):
                # find the root function, and a list of bound parents
                parents = []
                root_fn = bound_val
                while True:
                    if not hasattr(root_fn, "__func__"):
                        break
                    if hasattr(root_fn, "__self__"):
                        parents.append(root_fn.__self__)
                    root_fn = root_fn.__func__
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
                    "type": ClassMemberTypes.DATA,
                    "binding": ClassMemberBindings.INSTANCE,
                    "reason":"property",
                    "source": source(val.fget)
                }
            elif var in slots:
                binding = {
                    "type":ClassMemberTypes.DATA,
                    "binding": ClassMemberBindings.INSTANCE,
                    "reason":"slots"
                }
            else:
                binding = {
                    "type":ClassMemberTypes.DATA,
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
    __slots__ = ["imports","maybe_imports","analyzer"]
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.imports = set()
        """ list of modules this module imports; only includes modules part of the package """
        self.maybe_imports = set()
        """ List of modules that were accessed in someway to reference a class or routine. We can't be 100% sure this
            module accessed directly, its just a guess. There's no way to detect ``from x import y`` without analyzing
            source code. For variable's whose source is ambiguous, we can use these possible imports to guess at which
            refs are the true source.
        """
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
    def name(self) -> str:
        return self.module
    @property
    def qualified_name(self) -> str:
        return ""
    @property
    def module(self) -> str:
        return self.value.__name__

    def __repr__(self):
        return "<class ModuleAPI:{}>".format(self.name)
    def member_order(self, name) -> float:
        """ Get the source ordering. This is not the line number, but a rank indicating which the order variables
            were declared in the module.

            :returns: tuple (order, name) which can be used as sort key; if the order cannot be determined (the variable
                was not found in the source module), +infinity is used instead
        """
        if self.analyzer is not None:
            return self.analyzer.tagorder.get(name, float("inf"))
        return float("inf")
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
            if vv.add_ref(self, var):
                self.add_member(var, vv)
            # this module imports another in the package
            if isinstance(vv, ModuleAPI):
                self.imports.add(vv)

class PackageAPI:
    __slots__ = ["name","package","mods_tbl","ext_tbl","var_tbl","fqn_tbl","var_exclude","_need_analysis"]
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

        noop_cbk = lambda *args: False
        def get_cbk(name, default):
            cbk = options.get(name, default)
            return noop_cbk if cbk is None else cbk

        package_exclude = get_cbk("package_exclude", PackageAPI.default_package_exclude)
        self.var_exclude = get_cbk("var_exclude", PackageAPI.default_var_exclude)

        # get package modules
        # examining modules can sometimes lazy load others
        mods_seen = set()
        while True:
            seen_new = False
            for k in list(sys.modules.keys()):
                if k == self.name or k in mods_seen:
                    continue
                mods_seen.add(k)
                seen_new = True
                should_exclude = package_exclude(self.name, k)
                if should_exclude:
                    if should_exclude is True:
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

        def dont_resolve(vv):
            # external, source_ref already set elsewhere (probably by class itself), no refs, or ModuleAPI
            return vv.is_external() or vv.source_ref is not None or not vv.refs or isinstance(vv, ModuleAPI)
        # we've indexed all variables and imports; now we guess source definition qualname of variables
        # Routine/ClassAPI are functions/classes defined within the package, so have qualname that can be used to set the best reference
        for vv in self.var_tbl.values():
            if dont_resolve(vv):
                continue
            if isinstance(vv, (RoutineAPI, ClassAPI)):
                # qualname is guaranteed to be within package's allowed modules, since otherwise we would have made it DATA type
                name = module = vv.module
                attr_split = vv.qualified_name.rsplit(".",1)
                if len(attr_split) > 1:
                    name += "::"+attr_split[0]
                # type is nested inside function, so not importable directly; it must have been referenced in some
                # accessible way, or is bound to a RoutineAPI which is accessible; so defer to source resolution to
                # figure out what the best ref is for these
                if "<locals>" not in name:
                    try:
                        if name not in self.fqn_tbl:
                            raise RuntimeError("Variable has importable qualname {}, but {} not found".format(vv.fully_qualified_name, name))
                        else:
                            vv.source_ref = self.fqn_tbl[name]
                    # will silently ignore; this error is caused if the __qualname__ referenced was excluded
                    # have to use a different reference instead, which will be resolved later
                    except Exception as e:
                        logger.debug("can't use qualname '%s' to set source_ref", name, exc_info=e)

                # update "maybe imports" for anything that referenced this variable
                vv_mod = self.mods_tbl[module]
                for r in vv.refs.keys():
                    m = r.module
                    if m and m != module and m in self.mods_tbl:
                        self.mods_tbl[m].maybe_imports.add(vv_mod)

        for vv in self.var_tbl.values():
            """ first, determine what the best source module is for a variable (where variable was defined);
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
        if vtype == VariableTypes.MODULE:
            if package_module:
                clazz = ModuleAPI
            else:
                package_module = False
        # class/function that are not one of the package's modules will not use subclass specialization
        elif vtype != VariableTypes.DATA:
            try:
                mod = self.mods_tbl.get(val.__module__, None)
                if mod is not None:
                    if vtype == VariableTypes.CLASS:
                        clazz = ClassAPI
                    elif vtype == VariableTypes.ROUTINE:
                        clazz = RoutineAPI
            except: pass
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
    def default_package_exclude(pkg_name:str, module_name:str):
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
            return "private"
        # ignore executable packages
        if not isinstance(sys.modules[module_name], types.ModuleType):
            return "executable"
    @staticmethod
    def default_var_exclude(pkg, parent, value, name):
        """ Default variable exclusion callback. This ignores variables that are:

            - private: variable begins with single underscore (see :meth:`is_private`)
            - external: variable found outside the package modules, and so we assume was defined outside as well
              (see :meth:`~VariableValueAPI.is_external`)
            - special: variable begins with double underscore (see :meth:`is_special`); this allows
              ``__all__`` and ``__version__`` within the ``__init__`` module, and also non-inherited class methods

            :param pkg: PackageAPI for this variable
            :param parent: the context in which we discovered this variable
            :param value: the value of the variable
            :param name: the name of the variable
        """
        class_mbr = isinstance(parent, ClassAPI)
        if is_private(name) or (not class_mbr and value.is_external()):
            return True

        # allow __all/version__ for the package module itself
        special_include = ["__all__","__version__"]
        if is_special(name):
            allowed_init = name in special_include and parent is pkg.package
            allowed_override = isinstance(value, RoutineAPI) and (class_mbr or name == "__func__")
            return not (allowed_init or allowed_override)
    @staticmethod
    def module_import_analysis(value, modules:list):
        """ module import analysis, for determining source declaration of variable
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
        """
        # already handled the first two cases in __init__, so all the rest are DATA type
        # those could be primitives, class instances, etc
        class AtomicNode:
            """ a node in the import graph """
            __slots__ = ["modapi","out_ct","in_ct","in_nodes"]
            def __init__(self, m):
                self.modapi = m
                self.out_ct = 0 # stuff that imports m
                self.in_ct = 0 # stuff m imports
                # this are AtomicNode/MetaNode's representing the imported stuff
                # it doesn't necessarily match up with in_ct, since things may have merged into MetaNode's
                self.in_nodes = set()
        class MetaNode:
            __slots__ = ["nodes", "in_nodes"]
            def __init__(self, merge):
                self.nodes = set()
                self.in_nodes = set()
                for n in merge:
                    # don't allow nested MetaNode's
                    if isinstance(n, MetaNode):
                        self.nodes.update(n.nodes)
                    else:
                        self.nodes.add(n)
                    self.in_nodes.update(n.in_nodes)
                self.in_nodes -= self.nodes

        graph_nodes = set()
        graph_nodes_lookup = {}
        # construct nodes and edges
        for m in modules:
            node = AtomicNode(m)
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
        obj = value
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

    __slots__ = ["ref","name","fqn","value","doc"]

    def __init__(self, fqn:str, value, ref, name:str):
        """ Retrieves the same kind of "Documenter" object that autodoc would use to document this variable

            :param fqn: fully qualified variable name
            :param value: VariableValueAPI of variable
            :param ref: parent reference for variable
            :param name: variable name used for reference; can be None, in which case it will just document value
                directly, as though it were not attached to any
        """
        if Documenter.directive is None or Documenter.options is None:
            raise RuntimeError("must call bind_directive before Documenter will work")

        self.ref = ref
        self.name = name
        self.fqn = fqn
        self.value = value

        """ How autodoc gets documentation for objects:
            Entry point is Documenter.add_content(additional_content, don't_include_docstring)
            AttributeDocumenter: calls with no docstring if not a data descriptor
                1. first check module analyzer, analyzer.find_attr_docs
                2. if not in attr_docs, use self.get_doc
                    getdoc + prepare_docstring methods
                    getdoc specialization:
                    - ClassDocumenter: it will get from __init__ or __new__ instead
                    - SlotsDocumenter: if slots is a dict, treats key as the docstring
                    Otherwise, getdoc is pretty simple actually, it grabs __doc__; if it is a partial method, it will
                    get from func instead; if inherited option is allowed, it walks up the mro and finds __doc__
                3. run self.process_doc on every line of docs, then append to StringList()
                    doesn't do much, but emits event so extensions can preprocess docs

                signature is the header:
                self.format_signature()
                self.add_directive_header(sig)

            Although the doc stuff looks fine, the format_signature method is specialized for almost all classes, and
            gets pretty complicated. So for now, I'll just use the autodoc classes
        """
        # determine autodoc documenter class type
        if isinstance(value.value, property):
            t = PropertyDocumenter
        elif isinstance(ref, ClassAPI) and name is not None:
            extra = ref.members[name]
            r = extra.reason
            if r == "moduleanalyzer" and extra.binding == ClassMemberBindings.INSTANCE:
                t = InstanceAttributeDocumenter
            elif r == "slots":
                t = SlotsAttributeDocumenter
            elif extra.type == ClassMemberTypes.METHOD and isinstance(value, RoutineAPI):
                t = MethodDocumenter
            elif extra.type == ClassMemberTypes.INNERCLASS and isinstance(value, ClassAPI):
                t = ClassDocumenter
            else:
                t = AttributeDocumenter
        elif isinstance(value, RoutineAPI):
            t = FunctionDocumenter
        elif isinstance(value, ClassAPI):
            t = ClassDocumenter
        elif isinstance(value, ModuleAPI):
            t = ModuleDocumenter
        else:
            t = DataDocumenter
        # TODO: specialization for enum and exception

        # The rest here was copied/adapted from autosummary source code
        self.doc = t(Documenter.options, fqn)
        if not self.doc.parse_name():
            raise RuntimeError("documenter parse_name failed: {}".format(fqn))
        if not self.doc.import_object():
            raise RuntimeError("documenter import_object failed: {}".format(fqn))

        mod_name = fqn.split("::", 1)[0]
        mod = value.package.fqn_tbl.get(mod_name, None)
        if isinstance(mod, ModuleAPI):
            self.doc.analyzer = mod.analyzer

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
            max_chars = max(5, max_name_chars-len(self.name))
            sig = mangle_signature(sig, max_chars=max_chars)
        # don't know what this line does, but if not there extract_summary doesn't work
        # guess extract_summary is writing docutils nodes to the autodoc documenter object
        Documenter.options.result = StringList()
        self.doc.add_content(None)
        summary = extract_summary(Documenter.options.result.data[:], Documenter.directive.state.document)
        # summary is being a little too lenient and giving long summaries sometimes
        trim = summary.find(". ")
        if trim != -1:
            summary = summary[:trim+1]
        return {
            "signature": sig,
            "summary": summary
        }

    def documentation(self):
        """ Returns full reST docs, as would be returned by autodoc """
        out = Documenter.options.result = StringList()
        self.doc.generate()
        return out

    def order(self, name=None):
        """ Get relative order index that this variable was declared

            :param name: optionally use the variable's module to lookup order of an arbitrary variable name,
                rather than the one passed into the Documenter constructor
            :returns: tuple, (src_order, name); if src_order can't be determined for whatever reason (probably the
                requested variable isn't part of the module), then it sets to +inf; with the tuple, it will fallback
                to ordering alphabetically in that case
        """
        if name is None:
            name = self.fqn
            ns = name.split("::",1)
            name = "" if len(ns) < 2 else ns[1]
        return (self.doc.analyzer.tagorder.get(name, float("inf")), self.name)

_pkg_memoize = {}
def analyze_package(pname:str, *args, **kwargs):
    """ Factory for PackageAPI. It memoizes previously analyzed packages
        and reuses the results if possible; otherwise, it will import the module
        and create a new PackageAPI

        .. Note::
            Memoizing does not consider differing PackageAPI ``options`` arguments
    """
    if pname in _pkg_memoize:
        return _pkg_memoize[pname]
    m = importlib.import_module(pname)
    api = PackageAPI(m, *args, **kwargs)
    _pkg_memoize[pname] = api
    return api