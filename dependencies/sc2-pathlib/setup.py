"""Package only the current interpreter's artifact, never a pure wheel."""

import platform
import runpy
import sys
import sysconfig
from pathlib import Path

from setuptools import Distribution, setup
from setuptools.command.build_py import build_py

root = Path(__file__).resolve().parent
select_artifact = runpy.run_path(str(root / "build_support.py"))["select_artifact"]
artifact = select_artifact(
    root, version=sys.version_info, implementation=platform.python_implementation(),
    system=platform.system(), bits=64 if sys.maxsize > 2**32 else 32,
    extension_suffix=sysconfig.get_config_var("EXT_SUFFIX"),
)


class TargetBuildPy(build_py):
    def run(self):
        super().run()
        # A reused build directory must not leak a previous platform's binary.
        package_dir = Path(self.build_lib) / "sc2pathlib"
        for candidate in package_dir.iterdir():
            if candidate.suffix in {".pyd", ".so"} and candidate.name != artifact.name:
                candidate.unlink()


class NativeDistribution(Distribution):
    def has_ext_modules(self):
        return True


setup(distclass=NativeDistribution, cmdclass={"build_py": TargetBuildPy},
      include_package_data=False, package_data={"sc2pathlib": [artifact.name]})
