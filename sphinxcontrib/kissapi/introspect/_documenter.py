from sphinx.ext.autodoc import (
	PropertyDocumenter, ModuleDocumenter, AttributeDocumenter,
	MethodDocumenter, DataDocumenter, ClassDocumenter, FunctionDocumenter
)
from sphinx.ext.autosummary import mangle_signature, extract_summary
from sphinx.ext.autosummary import DocumenterBridge, Options
from docutils.statemachine import StringList

from ._types import ClassMemberBinding, ClassMemberType
from ._module import ModuleAPI
from ._class import ClassAPI
from ._routine import RoutineAPI

class Documenter:
	directive = None
	""" a reference to a sphinx directive """
	options = None
	""" autodoc documenter params; copied from autosummary """
	@classmethod
	def bind_directive(cls, directive):
		""" autodoc stuff needs to reference sphinx directive for something internal
			So this sets up the link to a sphinx directive object. At very least, the sphinx app
			gives you a registry of all the autodoc Documenter classes that are available.
		"""
		cls.directive = directive
		cls.options = DocumenterBridge(directive.env, directive.state.document.reporter, Options(), directive.lineno, directive.state)

	__slots__ = ["ref","name","fqn","value","doc"]

	def __init__(self, fqn:str, value, ref, name:str):
		""" Retrieves the same kind of "Documenter" object that autodoc would use to document this variable

			:param fqn: fully qualified variable name
			:param value: VariableValueAPI of variable
			:param ref: parent reference for variable
			:param name: variable name used for reference; can be None, in which case it will just document value
				directly, as though it were not attached to any
		"""
		if Documenter.directive is None or Documenter.options is None:
			raise RuntimeError("must call bind_directive before Documenter will work")

		self.ref = ref
		self.name = name
		self.fqn = fqn
		self.value = value

		""" How autodoc gets documentation for objects:
			Entry point is Documenter.add_content(additional_content, don't_include_docstring)
			AttributeDocumenter: calls with no docstring if not a data descriptor
				1. first check module analyzer, analyzer.find_attr_docs
				2. if not in attr_docs, use self.get_doc
					getdoc + prepare_docstring methods
					getdoc specialization:
					- ClassDocumenter: it will get from __init__ or __new__ instead
					- SlotsDocumenter: if slots is a dict, treats key as the docstring
					Otherwise, getdoc is pretty simple actually, it grabs __doc__; if it is a partial method, it will
					get from func instead; if inherited option is allowed, it walks up the mro and finds __doc__
				3. run self.process_doc on every line of docs, then append to StringList()
					doesn't do much, but emits event so extensions can preprocess docs

				signature is the header:
				self.format_signature()
				self.add_directive_header(sig)

			Although the doc stuff looks fine, the format_signature method is specialized for almost all classes, and
			gets pretty complicated. So for now, I'll just use the autodoc classes
		"""
		# determine autodoc documenter class type
		if isinstance(value.value, property):
			t = PropertyDocumenter
		elif isinstance(ref, ClassAPI) and name is not None:
			extra = ref.members[name]
			r = extra.reason
			if (r == "moduleanalyzer" and extra.binding == ClassMemberBinding.INSTANCE) or r == "slots":
				t = AttributeDocumenter
			elif extra.type == ClassMemberType.METHOD and isinstance(value, RoutineAPI):
				t = MethodDocumenter
			elif extra.type == ClassMemberType.INNERCLASS and isinstance(value, ClassAPI):
				t = ClassDocumenter
			else:
				t = AttributeDocumenter
		elif isinstance(value, RoutineAPI):
			t = FunctionDocumenter
		elif isinstance(value, ClassAPI):
			t = ClassDocumenter
		elif isinstance(value, ModuleAPI):
			t = ModuleDocumenter
		else:
			t = DataDocumenter
		# TODO: specialization for enum and exception
		# PropertyDocumenter, ExceptionDocumenter, DecoratorDocumenter, TypeVarDocumenter, NewTypeDocumenter, NewTypeAttributeDocumenter, 

		# The rest here was copied/adapted from autosummary source code
		self.doc = t(Documenter.options, fqn)
		if not self.doc.parse_name():
			raise RuntimeError("documenter parse_name failed: {}".format(fqn))
		if not self.doc.import_object():
			raise RuntimeError("documenter import_object failed: {}".format(fqn))

		mod_name = fqn.split("::", 1)[0]
		mod = value.package.fqn_tbl.get(mod_name, None)
		if isinstance(mod, ModuleAPI):
			self.doc.analyzer = mod.analyzer

	def summary(self, max_name_chars:int=50):
		""" Gets doc summary, as would be returned by autosummary extension

			:param max_name_chars: do not give full signature if it would cause the full name+signature to exceed this
				number of characters, instead using "..." to fill in missing params; at minimum we will return "(...)"
				if there is a signature
		"""
		# this is copied from autosummary source
		try:
			sig = self.doc.format_signature(show_annotation=False)
		except TypeError:
			sig = self.doc.format_signature()
		if not sig:
			sig = ''
		else:
			max_chars = max(5, max_name_chars-len(self.name))
			sig = mangle_signature(sig, max_chars=max_chars)
		# don't know what this line does, but if not there extract_summary doesn't work
		# guess extract_summary is writing docutils nodes to the autodoc documenter object
		Documenter.options.result = StringList()
		self.doc.add_content(None)
		summary = extract_summary(Documenter.options.result.data[:], Documenter.directive.state.document)
		# summary is being a little too lenient and giving long summaries sometimes
		trim = summary.find(". ")
		if trim != -1:
			summary = summary[:trim+1]
		return {
			"signature": sig,
			"summary": summary
		}

	def documentation(self):
		""" Returns full reST docs, as would be returned by autodoc """
		out = Documenter.options.result = StringList()
		self.doc.generate()
		return out

	def order(self, name=None):
		""" Get relative order index that this variable was declared

			:param name: optionally use the variable's module to lookup order of an arbitrary variable name,
				rather than the one passed into the Documenter constructor
			:returns: tuple, (src_order, name); if src_order can't be determined for whatever reason (probably the
				requested variable isn't part of the module), then it sets to +inf; with the tuple, it will fallback
				to ordering alphabetically in that case
		"""
		if name is None:
			name = self.fqn
			ns = name.split("::",1)
			name = "" if len(ns) < 2 else ns[1]
		return (self.doc.analyzer.tagorder.get(name, float("inf")), self.name)