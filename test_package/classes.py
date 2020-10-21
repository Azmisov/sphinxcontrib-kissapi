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