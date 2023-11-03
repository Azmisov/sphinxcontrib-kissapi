TODO
======

- new logic for setting best_ref
- what to do about routine nesting
- ClassAPI analyze members
- default_render tweaks
- documentation
- release


- undocumented, check if __doc__ is non-empty
- autodetect overridden methods, like __init__
- detect :private: in doc string to exclude private stuff
- "external" flag on VariableType.MODULE, if it isn't one of package modules
- Use GC instead to get references? rather than having the ``analyze_members`` methods that has to
  be specialized for each type; could potentially be a lot cleaner
- how to handle nested classes/functions? e.g. <locals> is in qualname; currently I'm ignoring them