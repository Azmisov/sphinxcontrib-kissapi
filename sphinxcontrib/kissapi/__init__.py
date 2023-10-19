"""
    This is a sphinx extension for generating API reST docs. I tried sphinx automodapi extension,
	see https://sphinx-automodapi.readthedocs.io/en/latest/, but there's just not enough customization options
	to give me the result I want.

	This file takes same approach as automodapi, where it reads in a package and generates reST files with
	autodoc markup in them. It defers to autodoc for actually generating the doc pages. Advantage of having
	this small, lightweight extension is you can easily customize the generated output

	Keep it simple, stupid

	kissapi_config = {
		out_dir: (str, def="kissapi_output") output directory for files generated by renderer
		overwrite: (bool or "partial", def=True), whether to overwrite the out_dir:
			- True = delete folder and completely rebuild
			- "partial" = allow files to be overwritten, but don't delete out_dir initially
			- False = don't do anything if folder is found, keep as is
		jinja_dir: a directory with jinja templates, which allows you to use some helpers on KissAPI
			instance for easier reST file generation; if a relative path is given, it will be relative to the root docs
			folder. By default, this is set to ``kissapi.templates`` directory, which are some default jinja templates
			I have made to go along with :meth:`~kissapi.render.package_template`
		jinja_env: jinja environment will be created from jinja_dir, but you can manually create one
			instead if you desire and set it to this config option
		output: {
			[name]: {
				package: the package to be introspected (see "introspect" config val to customize), and rendered
				render: a callback with signature (RenderManager, PackageAPI); it can use the RenderManager instance
					to generate other reST files; return any text you wish to insert in-place of the directive. By
					default, :meth:`~def_render.package_template` will be used if not specified.
			}
		}
		# Options for customizing the introspection behavior for packages to be rendered
		introspect: {
			[pkgname]: {
				package_exclude: callback(pkg_name:str, module_name:str) -> bool; return True if the module
					(from sys.modules) should be excluded from the package given by pkg_name. By default, this is
					PackageAPI.package_exclude, which excludes modules not prefixed by "[pkg_name].", or that contain
					a private module ("_" prefix somewhere in path). Only non-exluded modules are analyzed.
				var_exclude: callback(pkg_name:str, var:VariableValueAPI) -> bool; return True if the variable
					should be excluded from ModuleAPI's vars/aliased_vars/exports lists. By default this is
					PackageAPI.var_exclude, which excludes private and external variables (see var.is_private/is_external
					methods)
			}
		}
	}

	To inject rendered output into existing reST files, include the directive:

	\..kissapi:: [output_name]
	
"""
from .introspect import logger
from .manager import RenderManager, KissAPIDirective

__version__ = "2.0.0"
""" Current KissAPI version """

def bootstrap(app):
	""" Main callback for KissAPI to execute. This is called automatically during the Sphinx
		``builder-inited`` event, which is the latest event in which we are able to generate reST
		files. Here, we parse the ``kissapi_config`` value and run the package introspection and
		rendering. These results can then be inserted using the kissapi directive.
	"""
	try:
		app.kissapi = RenderManager(app)
	except Exception as e:
		logger.critical("Exception encountered executing KissAPI", exc_info=e)
		#traceback.print_exception(type(e), e, e.__traceback__)
		raise e

def setup(app):
	""" Called internally by Sphinx to register the KissAPI extension """
	# We'll just have a single config dict for all configuration
	app.add_config_value('kissapi_config', {}, 'env') # env/html other modes
	app.connect("builder-inited", bootstrap)
	app.add_directive("kissapi", KissAPIDirective)

	return {
		'version': __version__,
		# we do our file writes prior to parallel reads, so no problems
		'parallel_read_safe': True,
		'parallel_write_safe': True,
	}