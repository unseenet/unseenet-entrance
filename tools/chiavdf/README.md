# chiavdf for Pyodide

The web client runs the real core in Pyodide, and the core ripens keys with
[chiavdf](https://github.com/Chia-Network/chiavdf) (UN-RFC 0025). chiavdf has
no Pyodide build of its own, so this folder makes one (#27):

- `build.sh OUT_DIR` builds GMP for wasm32 and chiavdf's Python module
  against it with `pyodide build`. The result is a wheel of about 250 KB.
- `setup.py`, `pyproject.toml`: a small build of chiavdf's Python module only,
  without the native `vdf_client` its own build makes.
- `test.mjs WHEEL [CORE_SRC]` loads the wheel in Pyodide. It checks the
  proofs against `vectors.json`, which native chiavdf made, verifies them, and
  runs the core's ripening self-check.

The CI workflow `chiavdf.yml` does all of this. Run by hand with "publish", it
releases the wheel as `chiavdf-<version>-pyodide-<version>`.

## Two things that matter

- **64-bit limbs.** chiavdf asserts that GMP's limbs are 64 bits. On wasm32
  GMP picks 32-bit limbs, unless `configure` is told to use `long long`,
  which `build.sh` does. The proofs are byte for byte those of native
  chiavdf.
- **chiavdf 1.2.0 or later.** In 1.1.14, `create_discriminant` never returns
  where `long` is 32 bits (wasm32, like 64-bit Windows). chiavdf#419 fixed
  that, and 1.2.0 has the fix.

Speed is about 60-65% of native: ~80-105k iterations per second in desktop
browsers and on a Samsung Galaxy S24, so level 0 takes 11-14 minutes.
