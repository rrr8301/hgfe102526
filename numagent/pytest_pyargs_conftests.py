import importlib
import importlib.util
import re
import sys

_MODULE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")


def _package_conftests(argv):
    names = []
    for arg in argv:
        target = arg.split("::", 1)[0]
        if arg.startswith("-") or not _MODULE.match(target):
            continue
        parts = target.split(".")
        for depth in range(1, len(parts) + 1):
            name = ".".join(parts[:depth] + ["conftest"])
            if name in names:
                continue
            try:
                spec = importlib.util.find_spec(name)
            # BaseException, as in pytest_addoption below: find_spec imports the
            # PARENT, and a test module that calls `pytest.importorskip` at module
            # level (astropy's test_fitsio.py) raises Skipped, which is not an
            # Exception. Escaping here it aborted pytest on both revisions (exit 1)
            # instead of letting it report the skip.
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:        # not a package (an option's value, a -k word)
                break
            if spec is not None and spec.origin:
                names.append(name)
    return names


def pytest_addoption(parser, pluginmanager):
    if "--pyargs" not in sys.argv:
        return
    for name in _package_conftests(sys.argv[1:]):
        try:
            module = importlib.import_module(name)
        # BaseException: pandas' pytables conftest calls `pytest.importorskip`
        # at module level, and its Skipped is not an Exception. Escaping here it
        # aborts pytest (exit 1, no summary) instead of skipping the directory —
        # and a skip is what tells the harness to install the missing package.
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            continue                     # pytest reports it itself, with context
        path = str(module.__file__)
        if pluginmanager.get_plugin(path) is not None or pluginmanager.is_registered(module):
            continue
        try:
            pluginmanager.consider_conftest(module, registration_name=path)
        except TypeError:                # pytest < 8.1: named by __file__ already
            pluginmanager.consider_conftest(module)
        known = getattr(pluginmanager, "_conftest_plugins", None)
        if isinstance(known, set):
            known.add(module)
