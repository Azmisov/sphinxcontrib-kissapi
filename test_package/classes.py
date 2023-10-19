from types import MethodType
from functools import cached_property, partial, partialmethod, wraps, cache

def should_fail(fn, msg, etype=None):
	""" Test function and assert its failure """
	failed = False
	try:
		fn()
	except Exception as e:
		if etype is None or isinstance(e, etype):
			failed = True
	if not failed:
		assert False, msg

def wrapper(fn):
	""" Pass-through wrapper function """
	@wraps(fn)
	def wrapped(*args, **kwargs):
		return fn(*args, **kwargs)
	return wrapped

class Parent:
	attr_static_parent = None
	attr_static = 0

	def __init__(self):
		self.attr_instance_parent = 0
		self.attr_instance = 0

	@staticmethod
	def meth_static_parent(): pass
	@classmethod
	def meth_class_parent(cls): pass
	def meth_instance_parent(self): pass
	@property
	def attr_property_parent(self): pass

	@staticmethod
	def meth_static(*args):
		""" static method: ``@staticmethod`` """
		assert not len(args), "expected () args"

	@staticmethod
	@cache
	def meth_static_cached(*args):
		""" static method: cached ``@staticmethod`` """
		assert not len(args), "expected () args"

	@classmethod
	def meth_class(*args):
		""" class method: ``@classmethod`` """
		assert len(args) == 1 and args[0] is Parent, "expected (cls) args"

	@classmethod
	@cache
	def meth_class_cached(*args):
		""" class method: cached ``@classmethod`` """
		assert len(args) == 1 and args[0] is Parent, "expected (cls) args"

	def meth_instance(*args):
		""" instance method """
		assert len(args) == 1 and isinstance(args[0], Parent), "expected (self) args"

	@cache
	def meth_instance_cached(*args):
		""" instance method: cached """
		assert len(args) == 1 and isinstance(args[0], Parent), "expected (self) args"

	def meth_static_signature(*, foo=None, **kwargs):
		""" static method: no valid `self` argument """
		pass

	@property
	def attr_property(*args):
		""" instance attribute: ``@property`` """
		assert len(args) == 1 and isinstance(args[0], Parent), "expected (cls) args"

	@cached_property
	def attr_cached_property(*args):
		""" instance attribute: ``@cached_property`` """
		assert len(args) == 1 and isinstance(args[0], Parent), "expected (cls) args"

def _meth_class_prebound(*args):
	""" class method: bound ``MethodType`` """
	assert len(args) == 1 and args[0] is Parent, "expected (cls) args"
Parent.meth_class_prebound = MethodType(_meth_class_prebound, Parent)

def _meth_class_prebound_wrapped(*args):
	""" class method: bound ``MethodType``, wrapped with ``@classmethod`` """
	assert len(args) == 2 and args[0] is Parent and args[1] is Parent, "expected (cls,cls) args"
Parent.meth_class_prebound_wrapped = classmethod(MethodType(_meth_class_prebound_wrapped, Parent))

# you can also do staticmethod(classmethod(...)), but it will raise error run when you try to call it
def _meth_class_static_hybrid(*args):
	""" class method: bound ``MethodType``, wrapped with ``@staticmethod`` """
	assert len(args) == 1 and args[0] is Parent, "expected (cls) args"
Parent.meth_class_static_hybrid = staticmethod(MethodType(_meth_class_static_hybrid, Parent))

def _meth_instance_external(*args):
	""" instance method: defined externally """
	assert len(args) == 1 and isinstance(args[0], Parent), "expected (self) args"
Parent.meth_instance_external = _meth_instance_external

def _meth_instance_class_wrapper(*args):
	""" instance method: wrapper for bound ``MethodType`` """
	assert len(args) == 2 and args[0] is Parent and isinstance(args[1], Parent), "expected (cls,self) args"
Parent.meth_instance_class_wrapper = wrapper(MethodType(_meth_instance_class_wrapper, Parent))

def _meth_static_external(*args):
	""" static method: ``@staticmethod`` defined externally """
	assert not len(args), "expected () args"
Parent.meth_static_external = staticmethod(_meth_static_external)

def _meth_instance_cached_class(*args):
	""" instance method: cached bound ``MethodType`` """
	assert len(args) == 2 and args[0] is Parent and isinstance(args[1], Parent), "expected (cls,self) args"
Parent.meth_instance_cached_class = cache(MethodType(_meth_instance_cached_class, Parent))

def _meth_static_partial(*args):
	""" static method: plain function wrapped in partial """
	assert not len(args), "expected () args"
Parent.meth_static_partial = partial(_meth_static_partial)

def _meth_instance_partialmethod(*args):
	""" instance method: plain function wrapped in partialmethod """
	assert len(args) == 1 and isinstance(args[0], Parent), "expected (self) args"
Parent.meth_instance_partialmethod = partialmethod(_meth_instance_partialmethod)

def _meth_class_partial_bound(*args):
	""" class method: partial wrapping bound ``MethodType`` """
	assert len(args) == 1 and args[0] is Parent, "expected (cls) args"
Parent.meth_class_partial_bound = partial(MethodType(_meth_class_partial_bound, Parent))

def _meth_class_partialmethod_bound(*args):
	""" class method: partialmethod wrapping bound ``MethodType`` """
	assert len(args) == 1 and args[0] is Parent, "expected (cls) args"
Parent.meth_class_partialmethod_bound = partial(MethodType(_meth_class_partialmethod_bound, Parent))

def _meth_class_partialmethod(*args):
	""" class method: partialmethod wrapping ``@classmethod`` """
	assert len(args) == 1 and args[0] is Parent, "expected (cls) args"
Parent.meth_class_partialmethod = partialmethod(classmethod(_meth_class_partialmethod))

def _meth_static_partialmethod(*args):
	""" static method: partialmethod wrapping ``@staticmethod`` """
	assert not len(args), "expected () args"
Parent.meth_static_partialmethod = partialmethod(staticmethod(_meth_static_partialmethod))


# Test to verify behavior is what we expect
p = Parent()
Parent.meth_static()
p.meth_static()
Parent.meth_static_cached()
p.meth_static_cached()
Parent.meth_class()
p.meth_class()
Parent.meth_class_cached()
p.meth_class_cached()
p.meth_instance()
should_fail(Parent.meth_instance, "should be missing self arg", AssertionError)
p.meth_instance_cached()
Parent.meth_static_signature()
should_fail(p.meth_static_signature, "should not be able to call faux static method from instance", TypeError)
p.attr_property
p.attr_cached_property
Parent.meth_class_prebound()
p.meth_class_prebound()
Parent.meth_class_prebound_wrapped()
p.meth_class_prebound_wrapped()
Parent.meth_class_static_hybrid()
p.meth_class_static_hybrid()
p.meth_instance_external()
should_fail(Parent.meth_instance_external, "should be missing self arg", AssertionError)
p.meth_instance_class_wrapper()
should_fail(Parent.meth_instance_class_wrapper, "should be missing self arg", AssertionError)
Parent.meth_static_external()
p.meth_static_external()
p.meth_instance_cached_class()
should_fail(Parent.meth_instance_cached_class, "should be missing self arg", AssertionError)
Parent.meth_static_partial()
p.meth_static_partial()
should_fail(Parent.meth_instance_partialmethod, "partialmethod can't be reused for static", TypeError)
p.meth_instance_partialmethod()
Parent.meth_class_partial_bound()
p.meth_class_partial_bound()
Parent.meth_class_partialmethod_bound()
p.meth_class_partialmethod_bound()
Parent.meth_class_partialmethod()
p.meth_class_partialmethod()
Parent.meth_static_partialmethod()
p.meth_static_partialmethod()

# TODO:
# 	- classmethod(partial()) -> expect signature to be (foo, bar, cls)
#	- can wrapper call descriptor interface? e.g. could partial.func call func's descriptor?
	# TODO: can partial.func, methodtype.__func__ have descriptor as func?


class Child(Parent):
	""" Example of a subclass """
	attr_static_child = None
	""" a static data attribute that is not inherited """
	attr_static = 0
	""" an static data attribute that overrides :obj:`~test_package.classes.Parent.attr_static` """

	def __init__(self):
		self.attr_instance_parent = 0
		self.attr_instance = 0
		""" an instance data attribute that overrides :obj:`~test_package.classes.Parent.attr_instance` """

	@staticmethod
	def meth_static_parent(): pass
	@classmethod
	def meth_class_parent(cls): pass
	def meth_instance_parent(self): pass
	@property
	def attr_property_parent(self): pass

	@staticmethod
	def meth_static(): pass
	@classmethod
	def meth_class(cls): pass
	def meth_instance(self): pass
	@property
	def attr_property(self): pass

def external_method(*args):
	""" a non-class function with no args """
	pass
def external_method_args(*args):
	""" a class function with args """
	pass

# this tests some weirder cases
child = Child()
""" an instance of Child class """

Child.meth_static_ext3 = Parent.meth_static
Child.meth_static_ext4 = Parent.__dict__["meth_static"]
Child.meth_static_ext5 = MethodType(external_method_args, Parent)

Child.meth_class_ext = classmethod(external_method_args)
Child.meth_class_ext2 = MethodType(external_method_args, Child)
Child.meth_class_ext3 = staticmethod(MethodType(external_method_args, Child))
Child.meth_class_ext5 = classmethod(MethodType(external_method_args, child))

Child.meth_static_instance = MethodType(external_method_args, child)

Child.attr_property_ext = property(external_method_args)

