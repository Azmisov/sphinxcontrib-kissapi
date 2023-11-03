from __future__ import annotations
import enum, inspect, weakref, types
from typing import TYPE_CHECKING
from functools import partial, partialmethod

if TYPE_CHECKING:
	from _value import VariableValueAPI

class VariableType(enum.IntEnum):
	""" Variable types, categorized by those that autodoc can handle """
	MODULE = 0
	CLASS = 1
	ROUTINE = 2
	DATA = 3 # catchall for anything else

	@staticmethod
	def detect_type(val) -> VariableType:
		""" Determine `:class:introspect.VariableType` for a variable """
		# @cached_property, method_descriptor, @partialmethod, @cache/@lru_cache, and @wrap all
		# get caught by inspect call
		if inspect.isroutine(val) or isinstance(val, (partial, property)):
			return VariableType.ROUTINE
		if inspect.ismodule(val):
			return VariableType.MODULE
		if inspect.isclass(val):
			return VariableType.CLASS
		return VariableType.DATA

class ClassMemberType(enum.IntEnum):
	""" Class attribute types """
	METHOD = 0
	PROPERTY = 1
	INNERCLASS = 2
	DATA = 3

class ClassMemberBinding(enum.IntEnum):
	""" Which "form" of a class an attribute is bound to """
	STATIC = 0
	CLASS = 1
	SINGLETON = 2
	INSTANCE = 3

class ClassMember:
	""" Holds various information on a class attribute's form """
	__slots__ = ["type","binding","value","reason"]
	def __init__(self, type: ClassMemberType, binding:ClassMemberBinding, value:VariableValueAPI, reason:str):
		self.type = type
		self.binding = binding
		self.value = value
		self.reason = reason
		""" Details how the `:data:~ClassMember.binding` of this class member was detected """

class Immutable:
	def __init__(self, val):
		self.val = val

	@staticmethod
	def is_immutable(val):
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