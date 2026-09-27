# NAPI-RS

`omniship.plugins.napi` exports `NapiBuild`, `NapiPackage`, `NapiToolchain` and
the imperative `Napi(ctx).build(...)` / `.package(...)` facade.

See [the workflow example](../../examples/napi-release/workflow.py). Supply Node
and Rust toolchains independently. `NapiToolchain(version=...)` installs an exact
CLI version; the example versions are replaceable, not release-version sources.
Install project dependencies before building if your project needs them.

Each build requires an explicit Rust target and registers a directory named
`napi-<target>` containing fresh output (binary, JS loader, TypeScript definitions).
Toolchain targets install Rust standard libraries, not cross-compilers or linkers.
Choose a compatible runner or install the required cross-build tools yourself.

Assembly depends on every build with `after=[...]` and selects their artifact
names explicitly. `package.json` supplies the package name, release version and
`napi.targets`. Every configured platform must have a binary; partial releases
fail. Identically named generated bindings must agree across platforms. This
initial plugin handles native `.node` packages, not WASI or universal macOS merges.

The native CLI creates platform packages and places binaries in them. OmniShip
adds matching root optional dependencies and packs platform packages before the
root. Set the root package's `files`, `main` and `types` fields appropriately;
packing never runs lifecycle hooks. Generated bindings are restored to the source
workspace, but the source `package.json` is restored after packing.

Ship with `NpmPublish(from_artifacts=True)`: it selects the labeled npm artifacts,
preserves platform-before-root order, disables publish hooks, and does not install
dependencies or rebuild. Set `trusted_publishing=True` for npm OIDC, or configure
the existing npm authentication environment. `files=(...)` also accepts explicit
tarball artifact names or workspace paths, in publication order. Publication is
not transactional: if one npm publication fails, earlier versions remain published.

The primitives follow the upstream [build](https://napi.rs/docs/cli/build),
[assembly](https://napi.rs/docs/cli/artifacts) and
[platform package](https://napi.rs/docs/cli/create-npm-dirs) interfaces.
