import os, shutil, jinja2
from typing import Union
from .introspect import logger, analyze_package, Documenter
# unfortunately sphinx barfs when trying to pickle when we include package_template for some reason
#from .def_render import package_template

# sphinx imports
from docutils import nodes
from docutils.statemachine import ViewList
from sphinx.util.nodes import nested_parse_with_titles
from sphinx.util.docutils import SphinxDirective

# for emulating our own reST parser state
from docutils.parsers.rst.states import RSTStateMachine, state_classes
from sphinx.util.docutils import (NullReporter, new_document)
from docutils.core import Publisher

class KissAPIDirective(SphinxDirective):
    required_arguments = 1
    """ (int) only argument is the output name """
    def run(self):
        self.mgr = self.env.app.kissapi
        """ (:class:`~manager.RenderManager`) KissAPI render manager """
        self.source = self.get_source_info()
        """ (tuple) source file and line number of the directive """
        self.output = self.arguments[0]
        """ (str) RenderManager output name that should be injected """

        if self.output not in self.mgr.output:
            raise KeyError("{} is not a valid output; an output must be specified in kissapi_config before use".format(self.output))
        out = self.mgr.output[self.output]
        # out is the text that should be inserted in the directive's place
        # https://stackoverflow.com/questions/34350844/how-to-add-rst-format-in-nodes-for-directive
        if out is None:
            return []
        # convert output to docutils
        if isinstance(out, str):
            rst = ViewList()
            src_name = "kissapi_output_{}".format(self.output)
            for i, line in enumerate(out.splitlines()):
                rst.append(line, src_name, i)
            node = nodes.section()
            node.document = self.state.document
            nested_parse_with_titles(self.state, rst, node)
            return node.children
        # list output we assume is already docutils nodes
        if isinstance(out, list):
            return out
        raise TypeError("Cannot inject KissAPI generated output; renderer must return a list, string, or None")

class FakeDirective:
    """ Creates a fake sphinx directive that can be used to parse reST. In order to utilize autodoc/autosummary, they
        require you to pass the reST parsing state, as though they had been created while reading a reST source file.
        Since we are running KissAPI generation during builder-inited phase, the reST parsing state has not been
        setup yet. The :class:`~introspect.Documenter.bind_directive` takes care of the autodoc/autosummary integration,
        as long as you can pass it a directive-like object. This class will emulate a directive, as if it had been
        created by Sphinx itself. You can also grab the ``state`` attribute directly from this to parse other reST
        text yourself as desired.
    """
    def __init__(self, app):
        # After much digging through source code, this is how we emulate Sphinx's builtin reST parsing state
        # https://github.com/sphinx-doc/sphinx/blob/68cc0f7e94f360a2c62ebcb761f8096e04ebf07f/sphinx/io.py#L204
        # Here we're bypassing the RST Parser, and just doing the relevant code ops
        parser = app.registry.create_source_parser(app, "restructuredtext")
        # autosummary uses tab width 8; not set by publisher/env for some reason
        settings = dict(app.env.settings)
        if "tab_width" not in settings:
            settings["tab_width"] = 8
        p2 = Publisher()
        p2.process_programmatic_settings(None, settings, None)
        document = new_document('dummy_kissapi_source', p2.settings)  # (source path, settings)
        document.reporter = NullReporter()
        state_machine = RSTStateMachine(state_classes, 'Body')  # (state machine classes, initial state)
        # needed to set various self.[attr] values of state_machine
        state_machine.run([""], document)  # (input lines, document)

        # the directive attrs that are needed
        self.state = state_machine.get_state()
        self.env = app.env
        self.lineno = 0

class RenderManager:
    """ """
    def __init__(self, app):
        """ Sphinx directive entry point """
        logger.info("Running KissAPI introspection and rendering")
        self.packages = {}
        """ (dict) mapping of package names to their introspected :class:`~introspect.PackageAPI` objects """
        self.output = {}
        """ (dict) Mapping of output names to docutils node lists. These can be injected into reST documents using
            the ``kissapi`` directive.
        """
        self.root_dir = os.path.abspath(os.path.normpath(app.srcdir))
        """ (str) root directory of reST docs """
        self.conf = app.config.kissapi_config
        """ (dict) kissapi configuration defined by user, with some modifications made internally """

        if not isinstance(self.conf, dict):
            raise TypeError("kissapi_config must be a dict")

        # check output directory
        overwrite = self.conf.get("overwrite", True)
        usr_out_dir = self.conf.get("out_dir", "kissapi_output")
        self.out_dir = os.path.abspath(os.path.normpath(os.path.join(self.root_dir, usr_out_dir)))
        """ (str) output directory for KissAPI generated reST files """
        try:
            if self.out_dir == self.root_dir or os.path.commonpath([self.out_dir, self.root_dir]) != self.root_dir:
                raise ValueError("commonpath check failed")
            # handle overwrite check
            if os.path.exists(self.out_dir):
                if overwrite is True:
                    logger.info("Removing old output directory")
                    shutil.rmtree(self.out_dir)
                elif overwrite is False:
                    logger.warning("KissAPI no-op, since overwrite set to False and out_dir found")
                    return
                elif overwrite != "partial":
                    raise TypeError("invalid value for 'overwrite' option")
            # so we don't actually create the directory until the user requests a file
            # to be written from their renderer function; no need to create an empty directory
        except ValueError as e:
            raise ValueError("out_dir path must be within the docs root dir: {}".format(self.root_dir)) from e
        self.out_dir_rel = os.path.relpath(self.out_dir, self.root_dir)
        """ (str) :attr:`~manager.RenderManager.out_dir`, but relative to :attr:`~manager.RenderManager.root_dir` """

        # load up jinja environment
        if "jinja_env" not in self.conf:
            default_jinja_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "def_templates"))
            jinja_dir = self.conf.get("jinja_dir", default_jinja_dir)
            if isinstance(jinja_dir, str):
                if not os.path.isabs(jinja_dir):
                    jinja_dir = os.path.join(self.root_dir, jinja_dir)
                self.conf["jinja_env"] = jinja2.Environment(loader=jinja2.FileSystemLoader(jinja_dir))

        introspect_opts = self.conf.get("introspect", {})
        output_opts = self.conf.get("output", {})
        dedup_outs = {} # check for duplicate outputs {key -> output}

        # this allows introspect Documenter to work
        directive = FakeDirective(app)
        Documenter.bind_directive(directive)

        for oname,oval in output_opts.items():
            if not isinstance(oname, str):
                raise TypeError("output names must be strings")
            if not isinstance(oval, dict):
                raise TypeError("output configuration must be a dict")
            if "package" not in oval or not isinstance(oval["package"], str):
                raise KeyError("output configuration requires string item 'package'")
            if "render" not in oval or not callable(oval["render"]):
                raise KeyError("output configuration requires callable item 'render'")
            pkg = oval["package"]
            render = oval["render"]
            #render = oval.get("render", package_template)


            # introspect first
            if pkg not in self.packages:
                logger.info("Introspecting package '{}'".format(pkg))
                iopts = introspect_opts.get(pkg, {})
                pkgapi = analyze_package(pkg, iopts)
                self.packages[pkg] = pkgapi
            else:
                pkgapi = self.packages[pkg]

            # render output
            key = (pkgapi, render)
            if key not in dedup_outs:
                logger.info("Generating output '{}'".format(oname))
                out = render(self, pkgapi)
                """ out can be injected into reST pages via KissAPI directive
                    in order to get proper page cross links, the reST parser needs the injected document's docname, which
                    we don't have yet; so we have to wait to parse until we know where to inject it
                """
            else:
                out = dedup_outs[key]
            self.output[oname] = out

    def render_template(self, name:str, vars:dict={}) -> str:
        """ Render a template

            :param str name: name of the template
            :param dict vars: template variables
        """
        env = self.conf.get("jinja_env",None)
        if not isinstance(env, jinja2.Environment):
            raise ValueError("no jinja_dir (or env) option specified in kissapi_conf")
        tpl = env.get_template(name)
        return tpl.render(**vars)

    def write_template(self, path: Union[list,tuple,str], name:str, vars:dict):
        """ Render a template and then write the output to a file in out_dir

            :param path: the path to write to; see write_file for details
            :param str name: name of the template
            :param dict vars: template variables
            :returns: the relative output path of file
        """
        out = self.render_template(name, vars)
        return self.write_file(path, out)

    def write_file(self, path: Union[list,tuple,str], content:str):
        """ Write a file to the out_dir

            :param path: The path of the file to write. This should be relative to the out_dir. You can provide
                this as a list/tuple, in which case it will join the path for you properly; specify the filename
                as the last item in the list. You can use the staticmethod "unique_id" to generate an id to
                make this filename unique.
            :param content: The content to write to the file
            :returns: the relative output path of file
        """
        if isinstance(path, str):
            path = [path]
        abs_path = os.path.abspath(os.path.normpath(os.path.join(self.out_dir, *path)))
        abs_dir = os.path.dirname(abs_path)
        try:
            if os.path.commonpath([abs_dir, self.out_dir]) != self.out_dir:
                raise ValueError("commonpath check failed")
        except ValueError as e:
            raise ValueError("file must be written inside out_dir") from e
        # ensure parent directories all exist
        os.makedirs(abs_dir, exist_ok=True)
        with open(abs_path,"w",encoding='utf-8') as f:
            f.write(content)
        # reST wants unix style paths
        return "/"+os.path.relpath(abs_path, self.root_dir).replace('\\', '/')

    _next_id = 0
    @classmethod
    def unique_id(cls) -> str:
        """ For writing files, it can be helpful to have a unique identifier to ensure filenames don't
            conflict. Use this to generate a unique string id that can be pre/appended to a filename.
        """
        cls._next_id += 1
        return str(cls._next_id)