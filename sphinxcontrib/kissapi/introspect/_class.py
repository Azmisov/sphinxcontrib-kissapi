import re, inspect
from inspect import Parameter
from functools import cached_property

from ._utils import logger
from ._types import ClassMember, ClassMemberBinding, ClassMemberType, InstancePlaceholder
from ._value import VariableValueAPI
from ._routine import RoutineAPI

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
			raw_attrs = self.classify_members()
			# need to use the module analyzer to get instance attributes
			cont_mod = self.package.src_tbl[self.value.__module__]
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
					if isinst and old["binding"] != ClassMemberBinding.INSTANCE and old["type"] == ClassMemberType.DATA:
						old["binding"] = ClassMemberBinding.INSTANCE
						old["reason"] = "moduleanalyzer"
				# not seen before
				else:
					raw_attrs[k] = {
						"type": ClassMemberType.DATA, # technically, could be function, but we can't know for sure
						"binding": ClassMemberBinding.INSTANCE if isinst else ClassMemberBinding.STATIC,
						"reason": "moduleanalyzer",
						"value": InstancePlaceholder()
					}
			# convert attributes to vvapis
			for k,v in raw_attrs.items():
				vv = self.package.add_variable(v["value"])
				if vv.add_ref(self, k):
					is_inner = isinstance(vv, ClassAPI) and vv.fully_qualified_name.startswith(self.fully_qualified_name+".")
					type = ClassMemberType.INNERCLASS if is_inner else v["type"]
					logger.verbose("Found %s, %s, %s (%s)", k, type.name, v["binding"].name, v["reason"])
					# classify_members gives some extra info that is redundant, so we create our own dict
					cust_val = ClassMember(type, v["binding"], vv, v["reason"])
					self.add_member(k, vv, cust_val)

	def classify_members(self):
		""" Retrieve attributes and their types from the class. This does a pretty thorough examination of the attribute
			types, and can handle things like: ``classmethod``, ``staticmethod``, ``property``, bound methods, methods
			defined outside the class, signature introspection, and slots.

			:returns: (dict) a mapping from attribute name to attribute type; attribute type is a dict with these items:

				- ``type``: "method", for routines; "data", for properties, classes, and other data values
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
		
		# Note that when __slots__ is defined and doesn't contain __dict__, __dict__ will not be available on class instances
		# However, we're introspecting on the *class* itself, not an instance; and __dict__ will always be available in this case
		for var,val in cls.__dict__.items():
			RoutineAPI.analyze_bindings(cls, var)
			
			
			# this goes through descriptor interface, which binds the underlying value to the class/instance if needed
			try:
				bound_val = getattr(cls, var)
			except AttributeError:
				# e.g. properties will be in __dict__, but are not accessible as attributes
				bound_val = val

			# figure out what type of attribute this is

			# cached_property is a routine to start, but then gets overridden in __dict__ with
			# the value of the property when first called
			if isinstance(val, cached_property):
				binding = {
					"type": ClassMemberType.DATA,
					"binding": ClassMemberBinding.INSTANCE,
					"reason":"cached_property"
				}
			# this gets functions, methods, and C extensions
			elif inspect.isroutine(val):
				# find the root function, and a list of bound parents
				parents = []
				root_fn = bound_val
				while True:
					if not hasattr(root_fn, "__func__"):
						break
					# __self__ gives binding 
					if hasattr(root_fn, "__self__"):
						parents.append(root_fn.__self__)
					root_fn = root_fn.__func__
				binding = {
					"type":ClassMemberType.METHOD
				}
				# bound to the class automatically
				if isinstance(val, classmethod):
					extras = {
						"binding":ClassMemberBinding.CLASS,
						"reason":"classmethod",
					}
				# will not be bound to the class/instance
				elif isinstance(val, staticmethod):
					# if it is bound to the class, then it is actually behaving like classmethod
					if cls in parents:
						extras = {
							"binding": ClassMemberBinding.CLASS,
							"reason": "bound_staticmethod",
						}
					else:
						extras = {
							"binding":ClassMemberBinding.STATIC,
							"reason":"staticmethod",
						}
				# a normal function, but bound to the class; it will behave like classmethod;
				# python won't bind it to an instance, as it is already bound
				elif cls in parents:
					extras = {
						"binding":ClassMemberBinding.CLASS,
						"reason":"bound"
					}
				# if it is bound to an instance, then it is sort of an instance/static hybrid, since
				# it won't be bound to new instances... just the initial one
				elif any(isinstance(p, cls) for p in parents):
					extras = {
						"binding":ClassMemberBinding.STATIC_INSTANCE,
						"reason":"bound"
					}
				# other bound parents that are unrelated to this class
				# could classify these as "inherited" if we find an inherited method with same type and name
				elif parents:
					extras = {
						"binding":ClassMemberBinding.STATIC,
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
					except ValueError:
						logger.error("Error inspecting %s signature; this is a bug in your code", var)
					allowed_kinds = [
						Parameter.VAR_POSITIONAL,
						Parameter.POSITIONAL_OR_KEYWORD,
						getattr(Parameter, "POSITIONAL_ONLY", None) # python 3.8+
					]
					# this will catch wrapper_descriptor, a CPython class that wraps builtin class
					# routines; wrapper_descriptor gets bound to instances as a method-wrapper
					if first_arg and first_arg.kind in allowed_kinds:
						extras = {
							"binding": ClassMemberBinding.INSTANCE,
							"reason": "unbound"
						}
					else:
						extras = {
							"binding": ClassMemberBinding.STATIC,
							"reason": "signature"
						}
				binding.update(extras)
			elif isinstance(val, property):
				binding = {
					"type": ClassMemberType.DATA,
					"binding": ClassMemberBinding.INSTANCE,
					"reason":"property",
				}
			elif var in slots:
				binding = {
					"type":ClassMemberType.DATA,
					"binding": ClassMemberBinding.INSTANCE,
					"reason":"slots"
				}
			else:
				binding = {
					"type":ClassMemberType.DATA,
					"binding":ClassMemberBinding.STATIC,
					"reason":"other"
				}
			binding["value"] = bound_val
			attrs[var] = binding

		return attrs
