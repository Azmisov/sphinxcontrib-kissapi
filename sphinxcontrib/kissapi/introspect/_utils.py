from types import MethodType
from sphinx.util import logging

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


logger = logging.getLogger("kissapi_extension")
""" Logger for the KissAPI Sphinx extension """

# monkey patch logger to add indentation for more "stack" friendly logging
_indent_str = "  "
logger._indent = 0
class _IndentManager:
	""" Context manager for """
	def __init__(self, logger):
		self.logger = logger
	def __enter__(self):
		self.logger._indent += 1
	def __exit__(self, *exc_args):
		self.logger._indent -= 1
def _indent(self):
	return _IndentManager(self)
logger.indent = MethodType(_indent, logger)
""" Context manager for logging indentation. Use like so:
	`with logger.indent(): ...`
"""
def _wrap_logger(level):
	wrapped = getattr(logger, level)
	def wrapper(self, msg, *args, **kwargs):
		# weird behavior when msg is not a str; just ignore that
		if isinstance(msg, str) and self._indent:
			msg = (_indent_str*self._indent) + msg
		return wrapped(msg, *args, **kwargs)
	setattr(logger, level, MethodType(wrapper, logger))
for _level in ("error","critical","warning","log","info","verbose","debug"):
	_wrap_logger(_level)