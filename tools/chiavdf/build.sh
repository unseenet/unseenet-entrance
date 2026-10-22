#!/bin/sh
# chiavdf for Pyodide (unseenet-entrance#27): a wheel the web client loads beside
# Pyodide's own packages, so `import chiavdf` works there as everywhere else.
#
#   tools/chiavdf/build.sh OUT_DIR
#
# Needs: python3.14 with pyodide-build, emsdk at the version that Pyodide pins.
# CI sets these up (.github/workflows/chiavdf.yml).
set -eu

PYODIDE_VERSION=314.0.7
CHIAVDF_VERSION=1.2.0          # 1.2.0: 64-bit values no longer truncated where long is 32 bits (#419)
GMP_VERSION=6.3.0
GMP_SHA256=a3c2b80201b89e68616f4ad30bc66aee4927c3ce50e33929ca819d5c43538898

HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$(mkdir -p "$1" && cd "$1" && pwd)
WORK=${WORK:-$HERE/.build}
mkdir -p "$WORK"
cd "$WORK"

pyodide xbuildenv install "$PYODIDE_VERSION" >/dev/null
EMV=$(pyodide config get emscripten_version)
emcc --version | head -1 | grep -q " $EMV " || {
    echo "emscripten $EMV needed (emsdk install $EMV)" >&2; exit 1; }

# GMP, static and position-independent (the wheel is a side module).
# chiavdf asserts 64-bit limbs; GMP picks 32-bit ones on wasm32 unless
# configure is told otherwise, so it gets told.
if [ ! -f gmp/lib/libgmp.a ]; then
    [ -f gmp-$GMP_VERSION.tar.xz ] || curl -sSfLO https://ftp.gnu.org/gnu/gmp/gmp-$GMP_VERSION.tar.xz
    echo "$GMP_SHA256  gmp-$GMP_VERSION.tar.xz" | sha256sum -c - >/dev/null
    rm -rf gmp-$GMP_VERSION && tar xf gmp-$GMP_VERSION.tar.xz
    (
        cd gmp-$GMP_VERSION
        grep -q '^case \$limb_chosen in' configure
        sed -i 's/^case \$limb_chosen in/limb_chosen=longlong\ncase $limb_chosen in/' configure
        emconfigure ./configure --host=none --disable-assembly --enable-cxx --disable-shared \
            --with-pic --prefix="$WORK/gmp" CFLAGS="-O3 -fPIC" CXXFLAGS="-O3 -fPIC" >/dev/null
        grep -q '^#define GMP_LIMB_BITS *64' gmp.h
        emmake make -j"$(nproc)" >/dev/null
        emmake make install >/dev/null
    )
fi

# chiavdf at its release tag, with a small setup.py: only the Python module,
# without the native vdf_client / CMake build its own setup.py runs.
rm -rf chiavdf && git clone -q --depth 1 -b "$CHIAVDF_VERSION" https://github.com/Chia-Network/chiavdf.git
cp "$HERE/setup.py" "$HERE/pyproject.toml" chiavdf/
emcc -O3 -fPIC -c chiavdf/src/refcode/lzcnt.c -o chiavdf/lzcnt.o
GMP_PREFIX="$WORK/gmp" CHIAVDF_VERSION="$CHIAVDF_VERSION" \
    pyodide build chiavdf --outdir "$OUT"
ls -l "$OUT"
