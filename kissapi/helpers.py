import inspect
from inspect import Parameter

def get_class_members(cls):
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
    """
    attrs = {}
    for var,val in cls.__dict__.items():
        # this goes through descriptor interface, which binds the underlying value to the class/instance if needed
        bound_val = getattr(cls, var)

        # slots are instance only data vals; it will throw an error if slot conflicts with class variable
        # so no need to worry about overriding other slot vars
        # other details: https://docs.python.org/3/reference/datamodel.html?highlight=slots#notes-on-using-slots
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
                "type":"method",
                "source": source(root_fn),
                "parents": parents,
                "function": root_fn
            }
            # bound to the class automatically
            if isinstance(val, classmethod):
                extras = {
                    "binding":"class",
                    "reason":"classmethod",
                }
            # will not be bound to the class/instance
            elif isinstance(val, staticmethod):
                # if it is bound to the class, then it is actually behaving like classmethod
                if cls in parents:
                    extras = {
                        "binding": "class",
                        "reason": "bound_staticmethod",
                    }
                else:
                    extras = {
                        "binding":"static",
                        "reason":"staticmethod",
                    }
            # a normal function, but bound to the class; it will behave like classmethod
            # python won't bind it to an instance, as it is already bound
            elif cls in parents:
                extras = {
                    "binding":"class",
                    "reason":"bound"
                }
            # if it is bound to an instance, then it is sort of an instance/static hybrid, since
            # it won't be bound to new instances... just the initial one
            elif any(isinstance(p, cls) for p in parents):
                extras = {
                    "binding":"static_instance",
                    "reason":"bound"
                }
            # other bound parents that are unrelated to this class
            # could classify these as "inherited" if we find an inherited method with same type and name
            elif parents:
                extras = {
                    "binding":"static",
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
                        "binding": "instance",
                        "reason": "unbound"
                    }
                else:
                    extras = {
                        "binding": "static",
                        "reason": "signature"
                    }
            binding.update(extras)
        elif isinstance(val, property):
            binding = {
                "type":"data",
                "binding":"instance",
                "reason":"property",
                "source": source(val.fget)
            }
        elif var in slots:
            binding = {
                "type":"data",
                "binding":"instance",
                "reason":"slots"
            }
        else:
            binding = {
                "type":"data",
                "binding":"static",
                "reason":"other"
            }
        binding["value"] = bound_val
        attrs[var] = binding

    return attrs