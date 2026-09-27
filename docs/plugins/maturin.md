# Maturin

`omniship.plugins.maturin` exports `MaturinWheel`, `MaturinToolchain` and the
imperative `Maturin(ctx).build_wheels(...)` facade. See the
[ABI3 workflow example](../../examples/maturin-release/workflow.py).

The Build block accepts `target`, `interpreters`, `features`, `compatibility`,
`manifest_path`, `output`, and `release`. Omitting the target/interpreters delegates
selection to Maturin. Supply executable names or paths for installed interpreters.
ABI3 uses your PyO3 Cargo features, for example `features=("pyo3/abi3-py39",)`;
there is no artificial OmniShip ABI3 switch.

Each invocation builds into a fresh directory. Only newly produced wheels with
valid ZIP content and WHEEL/METADATA/RECORD entries are registered. Existing wheels
with the same filename are replaced; unrelated stale wheels are not exported.
`PyPIPublish` consumes the resulting artifacts without rebuilding. Package release
versions come from the project's manifests, not from the plugin or workflow.

`MaturinToolchain(version="1.9.4")` demonstrates an exact, user-selected CLI pin.
Rust and Python interpreter provisioning remain separate requirements. The
generator already provisions uv; Maturin is installed as its own tool environment.

Select runners/variants with the existing execution API. A Rust target is not a
runner label, and installing it does not supply a cross-linker. Linux publishing
requires an appropriate manylinux/musllinux build environment and, when needed,
patchelf. A `compatibility` flag validates/tags output; it does not create that
environment. This first plugin does not provision containers or run wheel tests.
Use the existing GitHub container/setup facilities and imperative tasks for those.

See the upstream [distribution guide](https://www.maturin.rs/distribution.html).
