from __future__ import annotations

import importlib
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE_NAME = "_autodev_app"


def _load_package() -> None:
    if PACKAGE_NAME in sys.modules:
        return

    init_path = os.path.join(HERE, "__init__.py")
    spec = importlib.util.spec_from_file_location(
        PACKAGE_NAME,
        init_path,
        submodule_search_locations=[HERE],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load AutoDev package for ASGI startup.")

    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE_NAME] = module
    spec.loader.exec_module(module)


_load_package()
app = importlib.import_module(f"{PACKAGE_NAME}.server").app
