"""Select a native artifact for the build interpreter, never a foreign binary."""

from pathlib import Path


def select_artifact(root, *, version, implementation, system, bits, extension_suffix):
    if implementation != "CPython" or tuple(version[:2]) != (3, 9) or bits != 64:
        raise RuntimeError("The current native build profile requires 64-bit CPython 3.9")
    if system not in {"Windows", "Linux"}:
        raise RuntimeError("Native packaging currently targets Windows or Linux")
    if not extension_suffix or not extension_suffix.endswith(".pyd" if system == "Windows" else ".so"):
        raise RuntimeError("Cannot determine the interpreter's native extension suffix")
    artifact = Path(root) / "sc2pathlib" / ("sc2pathlib" + extension_suffix)
    if not artifact.is_file():
        raise RuntimeError(
            "Matching native artifact is missing: " + str(artifact)
            + "; build it with this interpreter first (see docs/installation.md)"
        )
    return artifact
