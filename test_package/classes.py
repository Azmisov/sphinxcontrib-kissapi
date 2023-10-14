import types
from functools import cached_property

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
    def meth_static():
        """ a static method created with ``staticmethod`` decorator """
        pass
    @classmethod
    def meth_class(cls):
        """ a class method created with ``classmethod`` decorator """
        pass
    def meth_instance(self):
        """ a regular instance method """
        pass
    def meth_static_signature():
        """ a static method, due to having no valid `self` argument """
        pass
    @property
    def attr_property(self):
        """ an instance data attribute creating using ``property`` decorator """
        pass
    @cached_property
    def attr_property(self):
        """ an instance data attribute creating using ``cached_property`` decorator """
        pass

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

def external_method():
    """ a non-class function with no args """
    pass
def external_method_args(*args):
    """ a class function with args """
    pass

# this tests some weirder cases
child = Child()
""" an instance of Child class """

Child.meth_static_ext = external_method
Child.meth_static_ext2 = staticmethod(external_method)
Child.meth_static_ext3 = Parent.meth_static
Child.meth_static_ext4 = Parent.__dict__["meth_static"]
Child.meth_static_ext5 = types.MethodType(external_method_args, Parent)

Child.meth_class_ext = classmethod(external_method_args)
Child.meth_class_ext2 = types.MethodType(external_method_args, Child)
Child.meth_class_ext3 = staticmethod(types.MethodType(external_method_args, Child))
Child.meth_class_ext4 = staticmethod(classmethod(external_method_args))
Child.meth_class_ext5 = classmethod(types.MethodType(external_method_args, child))

Child.meth_instance_ext = external_method_args

Child.meth_static_instance = types.MethodType(external_method_args, child)

Child.attr_property_ext = property(external_method_args)

# instance method attached to a single instance;
# these won't get documented, since they won't belong to the Child type
child.meth_instance_independent = types.MethodType(external_method_args, child)
child.meth_instance_independent2 = child.__get__(external_method_args)