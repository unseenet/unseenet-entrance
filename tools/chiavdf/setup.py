"""chiavdf's Python module for Pyodide (used by tools/chiavdf/build.sh).

The same sources and package layout as chiavdf's own wheels, built with
setuptools instead of CMake, against the GMP the build script made for
wasm32 (and lzcnt.c, which the build script compiles as C).
"""
import os

from pybind11.setup_helpers import Pybind11Extension
from setuptools import setup

gmp = os.environ["GMP_PREFIX"]

setup(
    name="chiavdf",
    version=os.environ["CHIAVDF_VERSION"],
    packages=["chiavdf"],
    package_data={"chiavdf": ["py.typed", "__init__.pyi"]},
    ext_modules=[
        Pybind11Extension(
            "chiavdf._chiavdf",
            ["src/python_bindings/fastvdf.cpp"],
            include_dirs=["src", f"{gmp}/include"],
            extra_objects=["lzcnt.o", f"{gmp}/lib/libgmpxx.a", f"{gmp}/lib/libgmp.a"],
            extra_compile_args=["-O3"],
            cxx_std=17,
        )
    ],
)
