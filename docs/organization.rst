A **package** is made of set of modules that contain your code. You can designate certain modules as
**private**, and their contents will be analyzed, but will not be listed as accessible (e.g.
importable) from the package. Any other modules excluded from the package are considered
**external** and will not be analyzed in depth. Variables from external modules are enumerated
so we can identify them when they are referenced within the package.

The ``package_exclude`` callback defines what modules are accessible, private, or external to the
package to be analyzed. A default implementation is available as `:meth:~PackageAPI.default_package_exclude`.
It looks for modules that are prefixed by the package name, all others being marked as external;
underscores in a module mark it as private.

A `:class:ModuleAPI` object is created for all a package's modules, but the contents of private
modules will not be introspected. A `:class:ModuleAPI` is necessary for private modules to do source
code analysis of variables defined in the module, but accessible in a different, public module.
A `:attr:~PackageAPI.src_tbl` lookup can give the `:class:ModuleAPI` that a class or routine was
defined in, so source code analysis can be performed.

Classes
-------------
There is a good article on Python's descriptor mechanics in the `official docs
<https://elfi-y.medium.com/python-descriptor-a-thorough-guide-60a915f67aa9>`. As an oversimplified
summary, a member access ``object.member`` goes through ``__getattribute__``. This method will look
for ``member`` inside ``object.__dict__``. If the value happens to be a descriptor, meaning it has
``__get__`` defined on its class (*not the value itself*), that will be called to fetch the final
bound value. The output of a descriptor access could be completely different or unrelated to the
descriptor itself, so in general there isn't a fool-proof way to introspect its behavior. Beyond
descriptors, wrapping functions is a common pattern, and there are several builtin mechanisms for
doing so like ``partial``, ``cache``, or ``wrap``.

The libary currently implements logic to handle a fixed set of the most common descriptor and
wrapper scenarios. Currently supported are an arbitrary nesting of: staticmethod, classmethod,
property, cached_property, partial, partialmethod, method descriptors (wrappers for CPython
builtins), bound MethodType's, and wrappers marked with ``__wrapped__``. The easiest way to get
documentation to work for other types is to use the ``@wraps`` decorator to ensure ``__wrapped__``
has been populated.