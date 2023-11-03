import re, inspect, typing, types
from inspect import Parameter
from functools import cached_property

from ._utils import logger
from ._types import ClassMember, ClassMemberBinding, ClassMemberType, InstancePlaceholder
from ._value import VariableValueAPI
from ._routine import RoutineAPI

if typing.TYPE_CHECKING:
	from ._module import ModuleAPI

class ClassAPI(VariableValueAPI):
	""" Specialization of VariableValueAPI for class types. This will autodetect methods and attributes """
	instance_finder = re.compile(r"\s+self\.(\w+)\s*=")
	""" RegEx for identifying instance variables from source code, e.g. ``self.var = 'value'``"""
	
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		# PackageAPI.add_variable would not create ClassAPI unless the class's __module__ was part of the package
		self.package.src_tbl[self.source_fully_qualified_name_ext] = self

	@property
	def source_fully_qualified_name_ext(self):
		""" Fully qualified name given in source definition of class """
		v = self.value
		return v.__module__ + "::" + v.__qualname__
	
	def analyze_members(self):
		""" Analyze attributes of the class. That includes static, class, and instance
			methods, classes, and variables
		"""
		if super().analyze_members(): return
		with logger.indent():
			cls = self.value

			# need to use the module analyzer to get instance attributes
			mod: ModuleAPI = self.package.src_tbl.get(self.value.__module__)
			if mod:
				raw_inst_attrs = mod.instance_attrs(self.value.__qualname__)

			# ModuleAnalyzer can't say if it is an "instance" method;
			# this does naive search in source code for "self.XXX"
			inst_eligible = set()
			init = getattr(self.value, "__init__", None)
			if init is not None:
				try:
					src = inspect.getsource(init)
					inst_eligible = set(ClassAPI.instance_finder.findall(src))
				except: pass

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

			# Note that when __slots__ is defined and doesn't contain __dict__, __dict__ will not be available on class
			# instances; however, we're introspecting on the *class* itself, not an instance; and __dict__ will always
			# be available in this case
			for name,value in cls.__dict__.items():
				# should include?
				vv = self.package.add_variable(value)
				if not vv.add_ref(self, name):
					continue

				is_slot = name in slots
				is_inner = isinstance(vv, ClassAPI) and vv.fully_qualified_name.startswith(self.fully_qualified_name+".")


				# TODO: first check that routineapi can handle the type

				bindings = RoutineAPI.analyze_bindings(value, cls)
				first = bindings[0].base

				# instance | singleton | class; else static
				bound = 0b000
				bound_count = 0
				for b in bindings:
					bound_count += len(b.bound)
					for arg in b.bound:
						if arg is cls:
							bound |= 0b1
						elif isinstance(arg, cls):
							bound |= 0b10
						elif isinstance(arg, InstancePlaceholder):
							bound |= 0b100
						# TODO: could classify as "inherited" if we find bound class that is unrelated to cls
						#	or perhaps just classes in __mro__
				
				# if function signature cannot accomodate `self` arg then it cannot behave as instance
				if bound & 0b100 and isinstance(first, types.FunctionType):
					try:
						params = inspect.signature(bindings[-1].base).parameters
					except ValueError:
						logger.warn("Couldn't inspect %s signature", name)
					else:
						# count of how many positional args there are
						count = 0
						for p in params:
							if p == Parameter.VAR_POSITIONAL:
								count = float("inf")
								break
							if p == Parameter.KEYWORD_ONLY or p == Parameter.VAR_KEYWORD:
								break
							count += 1
						# not enough parameters for `self` arg? if < bound_count-1, it also cannot accomodate,
						# but in this case it can't accomodate the other bound arguments either, so probably an
						# issue with the analyze_binding
						if count == bound_count-1:
							bound &= 0b11
						elif count < bound_count:
							logger.warn("Found %d parameters in signature, but %d bound arguments: %s", count, bound_count, name)

				# if member is seen used in self.xxx vals, we can assume it has instance binding
				if name in raw_inst_attrs and name in inst_eligible: # TODO: old code also checked if it was DATA type
					del raw_inst_attrs[name]
					bound |= 0b100

				# TODO: all else data static

			# additional attributes the module analyzer picked up
			for name in raw_inst_attrs:
				isinst = name in inst_eligible
				# TODO: assume DATA
				# technically, could be function, but we can't know for sure

			logger.verbose("Found %s, %s, %s (%s)", k, type.name, v["binding"].name, v["reason"])
			# cust_val = ClassMember(type, v["binding"], vv, v["reason"])
			self.add_member(name, vv, cust_val)
