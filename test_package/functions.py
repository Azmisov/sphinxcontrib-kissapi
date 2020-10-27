def nested_class():
    class Nested:
        """ This is a nested class """
        def meth_instance(self):
            """ method inside that instance """
            pass

    return Nested

NestedOutside = nested_class()
nested_instance = NestedOutside()
