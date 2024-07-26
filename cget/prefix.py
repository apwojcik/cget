import os, shutil, shlex, six, inspect, click, contextlib, sys, functools
from pathlib import Path
from typing import List

from cget.builder import Builder
from cget.package import fname_to_pkg
from cget.package import PackageSource
from cget.package import PackageBuild
from cget.package import parse_pkg_build_tokens
import cget.util as util
from cget.types import returns
from cget.types import params

__CGET_DIR__ = os.path.dirname(os.path.realpath(__file__))
__CGET_CMAKE_DIR__ = os.path.join(__CGET_DIR__, 'cmake')


@params(s=six.string_types)
def parse_deprecated_alias(s):
    i = s.find(':', 0, max(s.find('://'), s.find(':\\')))
    if i > 0:
        click.echo("WARNING: Using ':' for aliases is now deprecated.")
        return s[0:i], s[i + 1:]
    else:
        return None, s


@params(s=six.string_types)
def parse_alias(s):
    i = s.find(',')
    if i > 0:
        return s[0:i], s[i + 1:]
    else:
        return parse_deprecated_alias(s)


@params(s=six.string_types)
def parse_src_name(url, default=None):
    x = url.split('@')
    p = x[0]
    # If the same name is used, then reduce to the same name
    if '/' in p:
        ps = p.split('/')
        if functools.reduce(lambda x, y: x == y, ps):
            p = ps[0]
    v = default
    if len(x) > 1: v = x[1]
    return (p, v)


def cmake_set(var, val, quote=True, cache=None, description=None):
    if isinstance(val, Path):
        val = str(val)
    if quote:
        val = util.quote(val)
    if cache is None or cache.lower() == 'none':
        yield f'set({var} {val})'
    else:
        yield f'set({var} {val} CACHE {cache} "{description or ''}")'


def cmake_append(var, *vals, **kwargs):
    quote = True
    if 'quote' in kwargs:
        quote = kwargs['quote']
    x = ' '.join([str(v) for v in vals])
    if quote:
        x = ' '.join([util.quote(val) for val in vals])
    yield f'list(APPEND {var} {x})'


def cmake_if(cond, *args):
    yield 'if ({})'.format(cond)
    for arg in args:
        for line in arg:
            yield '    ' + line
    yield 'endif()'


def cmake_else(*args):
    yield 'else ()'
    for arg in args:
        for line in arg:
            yield '    ' + line


def parse_cmake_var_type(key, value):
    if ':' in key:
        p = key.split(':')
        return (p[0], p[1].upper(), value)
    elif value.lower() in ['on', 'off', 'true', 'false']:
        return (key, 'BOOL', value)
    else:
        return (key, 'STRING', value)


def find_cmake(p, start):
    if p and not os.path.isabs(p):
        absp = util.actual_path(p, start)
        if os.path.exists(absp):
            return absp
        else:
            x = util.cget_dir('cmake', p)
            if os.path.exists(x):
                return x
            elif os.path.exists(x + '.cmake'):
                return x + '.cmake'
    return p


PACKAGE_SOURCE_TYPES = (six.string_types, PackageSource, PackageBuild)


class CGetPrefix:
    def __init__(self, prefix, verbose=False, build_path=None):
        self.prefix = Path(prefix or 'cget').absolute()
        self.verbose = verbose
        self.build_path = build_path
        self.cmd = util.Commander(paths=[self.prefix / 'bin'], env=self.get_env(), verbose=self.verbose)
        self.toolchain = self.write_cmake()

    def log(self, *args):
        if self.verbose: click.secho(' '.join([str(arg) for arg in args]), bold=True)

    def check(self, f, *args):
        if self.verbose and not f(*args):
            raise util.BuildError('ASSERTION FAILURE: ', ' '.join([str(arg) for arg in args]))

    def get_env(self):
        if os.name == 'nt':
            return None
        return {
            'LD_LIBRARY_PATH': str(self.prefix / 'lib'),
            'PKG_CONFIG_PATH': self.pkg_config_path()
        }


    def write_cmake(self, always_write=False, **kwargs):
        return util.mkfile(self.get_private_path(), 'cget.cmake', self.generate_cmake_toolchain(**kwargs),
                           always_write=always_write)

    @returns(inspect.isgenerator)
    @util.yield_from
    def generate_cmake_toolchain(self, toolchain=None, cc=None, cxx=None, cflags=None, cxxflags=None, ldflags=None,
                                 std=None, defines=None):
        set_ = cmake_set
        if_ = cmake_if
        else_ = cmake_else
        append_ = cmake_append
        yield set_('CGET_PREFIX', self.prefix)
        yield set_('CMAKE_PREFIX_PATH', self.prefix)
        yield if_('${CMAKE_VERSION} VERSION_LESS "3.6.0"',
                  ['include_directories(SYSTEM ${CGET_PREFIX}/include)'],
                  else_(
                      set_('CMAKE_CXX_STANDARD_INCLUDE_DIRECTORIES', '${CGET_PREFIX}/include'),
                      set_('CMAKE_C_STANDARD_INCLUDE_DIRECTORIES', '${CGET_PREFIX}/include')
                  )
                  )
        if toolchain: yield ['include({})'.format(util.quote(os.path.abspath(toolchain)))]
        yield if_('CMAKE_CROSSCOMPILING',
                  append_('CMAKE_FIND_ROOT_PATH', self.prefix)
                  )
        yield if_('CMAKE_INSTALL_PREFIX_INITIALIZED_TO_DEFAULT',
                  set_('CMAKE_INSTALL_PREFIX', self.prefix)
                  )
        if cxx: yield set_('CMAKE_CXX_COMPILER', cxx)
        if cc: yield set_('CMAKE_C_COMPILER', cc)
        if std:
            yield if_('NOT "${CMAKE_CXX_COMPILER_ID}" STREQUAL "MSVC"',
                      set_('CMAKE_CXX_STD_FLAG', "-std={}".format(std))
                      )
        yield if_('"${CMAKE_CXX_COMPILER_ID}" STREQUAL "MSVC"',
                  set_('CMAKE_CXX_ENABLE_PARALLEL_BUILD_FLAG', "/MP")
                  )
        if cflags:
            yield set_('CMAKE_C_FLAGS', "$ENV{{CFLAGS}} ${{CMAKE_C_FLAGS_INIT}} {}".format(cflags or ''),
                       cache='STRING')
        if cxxflags or std:
            yield set_('CMAKE_CXX_FLAGS',
                       "$ENV{{CXXFLAGS}} ${{CMAKE_CXX_FLAGS_INIT}} ${{CMAKE_CXX_STD_FLAG}} {}".format(cxxflags or ''),
                       cache='STRING')
        if ldflags:
            for link_type in ['STATIC', 'SHARED', 'MODULE', 'EXE']:
                yield set_('CMAKE_{}_LINKER_FLAGS'.format(link_type), "$ENV{{LDFLAGS}} {0}".format(ldflags),
                           cache='STRING')
        for dkey in defines or {}:
            name, vtype, value = parse_cmake_var_type(dkey, defines[dkey])
            yield set_(name, value, cache=vtype, quote=(vtype != 'BOOL'))
        yield if_('BUILD_SHARED_LIBS',
                  set_('CMAKE_WINDOWS_EXPORT_ALL_SYMBOLS', 'ON', cache='BOOL')
                  )
        yield set_('CMAKE_FIND_FRAMEWORK', 'LAST', cache='STRING')
        yield set_('CMAKE_INSTALL_RPATH', '${CGET_PREFIX}/lib', cache='STRING')

    def get_private_path(self) -> Path:
        return self.prefix / 'cget'

    def get_public_path(self) -> Path:
        return self.prefix / 'etc' / 'cget'

    def get_recipe_paths(self) -> List[Path]:
        return [self.get_public_path() / 'recipes']

    @contextlib.contextmanager
    @params(pb=PACKAGE_SOURCE_TYPES)
    def create_builder(self, pb, tmp=False):
        d = self.get_package_directory(pb)
        exists = d.exists()
        util.mkdir(d)
        yield Builder(self, d, exists)
        if os.name != 'nt' and tmp:
            shutil.rmtree(d, ignore_errors=True)

    @params(pb=PACKAGE_SOURCE_TYPES)
    def get_package_directory(self, pb):
        pb = self.parse_pkg_build(pb)
        return self.get_private_path() / pb.to_fname()

    @params(pb=PACKAGE_SOURCE_TYPES)
    def get_unlink_directory(self, pb):
        return self.get_package_directory(pb) / '.unlink'

    @params(pb=PACKAGE_SOURCE_TYPES)
    def get_deps_directory(self, pb) -> Path:
        return self.get_package_directory(pb) / '.depend'

    def parse_src_file(self, name, url, start=None):
        f = util.actual_path(url, start)
        self.log('parse_src_file actual_path:', start, f)
        if os.path.exists(f): return PackageSource(name=name, url='file://' + f)
        return None

    def parse_src_recipe(self, name, url):
        p, v = parse_src_name(url)
        for rpath in self.get_recipe_paths():
            rp = os.path.normcase(os.path.join(rpath, p, v or ''))
            if os.path.exists(rp):
                return PackageSource(name=name or p, recipe=rp)
        return None

    def parse_src_github(self, name, url):
        p, v = parse_src_name(url, 'HEAD')
        if '/' in p:
            url = 'https://github.com/{0}/archive/{1}.tar.gz'.format(p, v)
        else:
            url = 'https://github.com/{0}/{0}/archive/{1}.tar.gz'.format(p, v)
        if name is None: name = p
        return PackageSource(name=name, url=url)

    @returns(PackageSource)
    @params(pkg=PACKAGE_SOURCE_TYPES)
    def parse_pkg_src(self, pkg, start=None, no_recipe=False):
        if isinstance(pkg, PackageSource):
            return pkg
        if isinstance(pkg, PackageBuild):
            return self.parse_pkg_src(pkg.pkg_src, start)
        name, url = parse_alias(pkg)
        self.log('parse_pkg_src:', name, url, pkg)
        if '://' not in url:
            return self.parse_src_file(name, url, start) or \
                (None if no_recipe else self.parse_src_recipe(name, url)) or \
                self.parse_src_github(name, url)
        return PackageSource(name=name, url=url)

    @returns(PackageBuild)
    @params(pkg=PACKAGE_SOURCE_TYPES)
    def parse_pkg_build(self, pkg, start=None, no_recipe=False):
        if isinstance(pkg, PackageBuild):
            pkg.pkg_src = self.parse_pkg_src(pkg.pkg_src, start, no_recipe)
            if pkg.pkg_src.recipe: pkg = self.from_recipe(pkg.pkg_src.recipe, pkg)
            if pkg.cmake: pkg.cmake = find_cmake(pkg.cmake, start)
            return pkg
        else:
            pkg_src = self.parse_pkg_src(pkg, start, no_recipe)
            if pkg_src.recipe:
                return self.from_recipe(pkg_src.recipe, pkg_src.name)
            else:
                return PackageBuild(pkg_src)

    def from_recipe(self, recipe, pkg=None, name=None):
        recipe_pkg = os.path.join(recipe, "package.txt")
        util.ensure_exists(recipe_pkg)
        p = next(iter(self.from_file(recipe_pkg, no_recipe=True)))
        self.check(lambda: p.pkg_src is not None)
        requirements = os.path.join(recipe, "requirements.txt")
        if os.path.exists(requirements): p.requirements = requirements
        p.pkg_src.recipe = None
        # Use original name
        if pkg:
            p.pkg_src.name = pkg.pkg_src.name
        elif name:
            p.pkg_src.name = name

        if pkg:
            return p.merge(pkg)
        else:
            return p

    def from_file(self, file, url=None, no_recipe=False):
        if file is None:
            return
        if not os.path.exists(file):
            self.log("file not found: " + file)
            return
        start = os.path.dirname(file)
        if url is not None and url.startswith('file://'):
            start = url[7:]
        with open(file) as f:
            self.log("parse file: " + file)
            for line in f.readlines():
                tokens = shlex.split(line, comments=True)
                if len(tokens) > 0:
                    pb = parse_pkg_build_tokens(tokens)
                    ps = self.from_file(util.actual_path(pb.file, start), no_recipe=no_recipe) if pb.file else [
                        self.parse_pkg_build(pb, start=start, no_recipe=no_recipe)]
                    for p in ps: yield p

    def write_parent(self, pb, track=True):
        if track and pb.parent is not None: util.mkfile(self.get_deps_directory(pb.to_fname()), pb.parent, pb.parent)

    def install_deps(self, pb, d, test=False, test_all=False, generator=None, insecure=False,
                     ignore_requirements=False):
        req_txt = os.path.join(d, 'requirements.txt') if not ignore_requirements else None
        for dependent in self.from_file(pb.requirements or req_txt, pb.pkg_src.url):
            transient = dependent.test or dependent.build
            testing = test or test_all
            installable = not dependent.test or dependent.test == testing
            if installable:
                self.install(dependent.of(pb), test_all=test_all, generator=generator, track=not transient,
                             insecure=insecure)

    @returns(six.string_types)
    @params(pb=PACKAGE_SOURCE_TYPES, test=bool, test_all=bool, update=bool, track=bool)
    def install(self, pb, test=False, test_all=False, generator=None, update=False, track=True, insecure=False):
        pb = self.parse_pkg_build(pb)
        pkg_dir = self.get_package_directory(pb)
        unlink_dir = pkg_dir / 'unlink'
        # If it's been unlinked, then link it in
        if unlink_dir.is_dir():
            if update:
                shutil.rmtree(unlink_dir)
            else:
                self.link(pb)
                self.write_parent(pb, track=track)
                return "Linking package {}".format(pb.to_name())
        if pkg_dir.is_dir():
            self.write_parent(pb, track=track)
            if update:
                self.remove(pb)
            else:
                return "Package {} already installed".format(pb.to_name())
        with self.create_builder(pb, tmp=True) as builder:
            # Fetch package
            src_dir = builder.fetch(pb.pkg_src.url, pb.hash, (pb.cmake != None), insecure=insecure)
            # Install any dependencies first
            self.install_deps(pb, src_dir, test=test, test_all=test_all, generator=generator, insecure=insecure,
                              ignore_requirements=pb.ignore_requirements)
            # Setup cmake file
            if pb.cmake:
                target = os.path.join(src_dir, 'CMakeLists.txt')
                if os.path.exists(target):
                    os.rename(target, os.path.join(src_dir, builder.cmake_original_file))
                shutil.copyfile(pb.cmake, target)
            # Configure and build
            builder.configure(src_dir, defines=pb.define, generator=generator, install_prefix=self.prefix, test=test,
                              variant=pb.variant)
            builder.build(variant=pb.variant)
            # Run tests if enabled
            if test or test_all: builder.test(variant=pb.variant)
            # Install
            builder.build(target='install', variant=pb.variant)
        self.write_parent(pb, track=track)
        return f'Successfully installed {pb.to_name()}'

    @returns(six.string_types)
    @params(pb=PACKAGE_SOURCE_TYPES)
    def ignore(self, pb):
        pb = self.parse_pkg_build(pb)
        pkg_dir = self.get_private_path() / pb.to_fname()
        # If package doesn't exist
        if not pkg_dir.exists():
            util.mkfile(pkg_dir, "ignore", "ignore")
            return "Ignore package {}".format(pb.to_name())
        else:
            return "Package {} already installed".format(pb.to_name())

    @params(pb=PACKAGE_SOURCE_TYPES, test=bool)
    def build(self, pb, test=False, target=None, generator=None):
        pb = self.parse_pkg_build(pb)
        src_dir = pb.pkg_src.get_src_dir()
        if os.path.exists(os.path.join(src_dir, 'dev-requirements.txt')):
            pb.requirements = os.path.join(src_dir, 'dev-requirements.txt')
        elif os.path.exists(os.path.join(src_dir, 'requirements.txt')):
            pb.requirements = os.path.join(src_dir, 'requirements.txt')
        with self.create_builder(pb) as builder:
            # Install any dependencies first
            self.install_deps(pb, src_dir, generator=generator, test=test)
            # Configure and build
            if not builder.exists: builder.configure(src_dir, defines=pb.define, generator=generator, test=test,
                                                     variant=pb.variant)
            builder.build(variant=pb.variant, target=target)
            # Run tests if enabled
            if test: builder.test(variant=pb.variant)

    @params(pb=PACKAGE_SOURCE_TYPES)
    def get_build_path(self, pb) -> Path:
        if self.build_path:
            return Path(self.build_path)
        else:
            return self.get_package_directory(pb) / 'build'

    @params(pb=PACKAGE_SOURCE_TYPES)
    def build_clean(self, pb):
        p = self.get_package_directory(pb)
        if p.exists():
            shutil.rmtree(p)

    @params(pb=PACKAGE_SOURCE_TYPES)
    def build_configure(self, pb):
        pb = self.parse_pkg_build(pb)
        src_dir = pb.pkg_src.get_src_dir()
        if 'ccmake' in self.cmd:
            self.cmd.ccmake([src_dir], cwd=self.get_build_path(pb))
        elif 'cmake-gui' in self.cmd:
            self.cmd.cmake_gui([src_dir], cwd=self.get_build_path(pb))

    @params(pkg=PACKAGE_SOURCE_TYPES)
    def remove(self, pkg):
        self.unlink(pkg, delete=True)

    @params(pkg=PACKAGE_SOURCE_TYPES)
    def unlink(self, pkg, delete=False):
        pkg = self.parse_pkg_src(pkg)
        pkg_dir = self.get_package_directory(pkg)
        unlink_dir = self.get_unlink_directory(pkg)
        self.log("Unlink:", pkg_dir)
        if pkg_dir.exists():
            if util.USE_SYMLINKS:
                util.rm_symlink_from(pkg_dir / 'install', self.prefix)
            else:
                util.rm_dup_dir(pkg_dir / 'install', self.prefix, remove_both=False)
            util.rm_empty_dirs(self.prefix)
            if delete:
                util.delete_dir(pkg_dir)
            else:
                util.mkdir(self.get_unlink_directory())
                os.rename(pkg_dir, unlink_dir)

    @params(pkg=PACKAGE_SOURCE_TYPES)
    def link(self, pkg):
        pkg = self.parse_pkg_src(pkg)
        pkg_dir = self.get_package_directory(pkg)
        unlink_dir = pkg_dir / 'unlink'
        if os.path.exists(unlink_dir):
            util.mkdir(pkg_dir)
            os.rename(unlink_dir, pkg_dir)
            if util.USE_SYMLINKS:
                util.symlink_dir(pkg_dir / 'install', self.prefix)
            else:
                util.copy_dir(pkg_dir / 'install', self.prefix)
        # Relink dependencies
        for dep in util.ls(unlink_dir, os.path.isdir):
            ls = util.ls(self.get_unlink_deps_directory(pkg) / dep, os.path.isfile)
            if pkg.to_fname() in ls: self.link(dep)

    def _list_files(self, pkg=None, top=True):
        pkg = self.parse_pkg_src(pkg)
        pkg_dir = self.get_package_directory(pkg)
        if pkg is None:
            return util.ls(pkg_dir, os.path.isdir)
        else:
            ls = util.ls(self.get_deps_directory(pkg.to_fname()), os.path.isfile)
            if top:
                return [pkg.to_fname()] + list(ls)
            else:
                return ls

    def list(self, pkg=None, recursive=False, top=True):
        for d in self._list_files(pkg, top):
            p = fname_to_pkg(d)
            if (self.get_private_path() / d).exists():
                yield p
            if recursive:
                for child in self.list(p, recursive=recursive, top=False):
                    yield child

    def clean(self):
        if util.USE_SYMLINKS:
            util.delete_dir(self.get_private_path())
            util.rm_symlink_dir(self.prefix)
            util.rm_empty_dirs(self.prefix)
        else:
            for p in self.list():
                self.remove(p)
            util.delete_dir(self.get_private_path())

    def clean_cache(self):
        p = util.get_cache_path()
        if os.path.exists(p): shutil.rmtree(util.get_cache_path())

    def pkg_config_path(self):
        paths = []
        for p in ['lib', 'lib64', 'share']:
            paths.append(str(self.prefix / p / 'pkgconfig'))
        return os.pathsep.join(paths)

    @contextlib.contextmanager
    def try_(self, msg=None, on_fail=None):
        try:
            yield
        except util.BuildError as err:
            if err.msg: click.echo(err.msg)
            if msg: click.echo(msg)
            if on_fail: on_fail()
            if self.verbose:
                if err.data: click.echo(err.data)
                raise
            sys.exit(1)
        except:
            extype, exvalue, extraceback = sys.exc_info()
            click.echo("Unexpected error: " + str(extype))
            click.echo(str(exvalue))
            if msg: click.echo(msg)
            if on_fail: on_fail()
            if self.verbose: raise
            sys.exit(1)
