# Copyright cocotb contributors
# Licensed under the Revised BSD License, see LICENSE for details.
# SPDX-License-Identifier: BSD-3-Clause
import glob
from contextlib import suppress
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import nox

# Sessions run by default if nox is called without further arguments.
nox.options.sessions = ["dev_test"]

test_deps = ["pytest"]
coverage_deps = ["coverage", "pytest-cov"]

dev_deps = [
    "black",
    "isort",
    "mypy",
    "pre-commit",
    "nox",
    "flake8",
    "clang-format",
]

#
# Helpers for use within this file.
#


def simulator_support_matrix() -> List[Tuple[str, str, str]]:
    """
    Get a list of supported simulator/toplevel-language/GPI-interface tuples.
    """

    # Simulators with support for VHDL through VHPI, and Verilog through VPI.
    standard = [
        (sim, toplevel_lang, gpi_interface)
        for sim in ("activehdl", "rivierapro", "xcelium")
        for toplevel_lang in ("verilog", "vhdl")
        for gpi_interface in ("vpi", "vhpi")
        if (toplevel_lang, gpi_interface) in (("verilog", "vpi"), ("vhdl", "vhpi"))
    ]

    # Special-case simulators.
    special = [
        ("cvc", "verilog", "vpi"),
        ("ghdl", "vhdl", "vpi"),
        ("icarus", "verilog", "vpi"),
        ("questa", "verilog", "vpi"),
        ("questa", "vhdl", "fli"),
        ("questa", "vhdl", "vhpi"),
        ("verilator", "verilog", "vpi"),
        ("vcs", "verilog", "vpi"),
    ]

    return standard + special


def env_vars_for_test(
    sim: Optional[str], toplevel_lang: Optional[str], gpi_interface: Optional[str]
) -> Dict[str, str]:
    """Prepare the environment variables controlling the test run."""
    e = {}
    if sim is not None:
        e["SIM"] = sim
    if toplevel_lang is not None:
        e["TOPLEVEL_LANG"] = toplevel_lang
    assert not (toplevel_lang == "verilog" and gpi_interface != "vpi")
    if toplevel_lang == "vhdl" and gpi_interface is not None:
        e["VHDL_GPI_INTERFACE"] = gpi_interface

    return e


def stringify_dict(d: Dict[str, str]) -> str:
    return ", ".join(f"{k}={v}" for k, v in d.items())


#
# Development pipeline
#
# - Use nox to build an sdist; no separate build step is required.
# - Run tests against the installed sdist.
# - Collect coverage.
#


@nox.session
def dev_build(session: nox.Session) -> None:
    session.warn("No building is necessary for development sessions.")


@nox.session
def dev_test(session: nox.Session) -> None:
    """Run all development tests as configured through environment variables."""

    dev_test_nosim(session)
    dev_test_sim(session, sim=None, toplevel_lang=None, gpi_interface=None)


@nox.session
@nox.parametrize("sim,toplevel_lang,gpi_interface", simulator_support_matrix())
def dev_test_sim(
    session: nox.Session,
    sim: Optional[str],
    toplevel_lang: Optional[str],
    gpi_interface: Optional[str],
) -> None:
    """Test a development version of cocotb against a simulator."""

    session.env["CFLAGS"] = "-Werror -Wno-deprecated-declarations -g --coverage"
    session.env["COCOTB_LIBRARY_COVERAGE"] = "1"
    session.env["CXXFLAGS"] = "-Werror"
    session.env["LDFLAGS"] = "--coverage"

    session.install(*test_deps, *coverage_deps)
    session.install("-e", ".")

    env = env_vars_for_test(sim, toplevel_lang, gpi_interface)
    config_str = stringify_dict(env)

    # Remove a potentially existing coverage file from a previous run for the
    # same test configuration.
    coverage_file = Path(f".coverage.test.sim-{sim}-{toplevel_lang}-{gpi_interface}")
    with suppress(FileNotFoundError):
        coverage_file.unlink()

    session.log(f"Running 'make test' against a simulator {config_str}")
    session.run("make", "test", external=True, env=env)

    session.log(f"Running simulator-specific tests against a simulator {config_str}")
    session.run(
        "pytest",
        "-v",
        "--cov=cocotb",
        "--cov-branch",
        "-k",
        "simulator_required",
    )
    Path(".coverage").rename(".coverage.pytest")

    session.log(f"All tests passed with configuration {config_str}!")

    # Combine coverage produced during the test runs, and place it in a file
    # with a name specific to this invocation of dev_test_sim().
    coverage_files = glob.glob("**/.coverage.cocotb", recursive=True)
    if not coverage_files:
        session.error(
            "No coverage files found. Something went wrong during the test execution."
        )
    coverage_files.append(".coverage.pytest")
    session.run("coverage", "combine", "--append", *coverage_files)
    Path(".coverage").rename(coverage_file)

    session.log(f"Stored Python coverage for this test run in {coverage_file}.")

    # Combine coverage from all nox sessions as last step after all sessions
    # have completed.
    session.notify("dev_coverage_combine")


@nox.session
def dev_test_nosim(session: nox.Session) -> None:
    """Run the simulator-agnostic tests against a cocotb development version."""
    session.install(*test_deps, *coverage_deps)
    session.install("-e", ".")

    # Remove a potentially existing coverage file from a previous run for the
    # same test configuration.
    coverage_file = Path(".coverage.test.pytest")
    with suppress(FileNotFoundError):
        coverage_file.unlink()

    # Run pytest with the default configuration in setup.cfg.
    session.log("Running simulator-agnostic tests with pytest")
    session.run(
        "pytest",
        "-v",
        "--cov=cocotb",
        "--cov-branch",
        "-k",
        "not simulator_required",
    )

    # Run pytest for files which can only be tested in the source tree, not in
    # the installed binary (otherwise we get an "import file mismatch" error
    # from pytest).
    session.log("Running simulator-agnostic tests in the source tree with pytest")
    pytest_sourcetree = [
        "cocotb/utils.py",
        "cocotb/binary.py",
        "cocotb/types/",
        "cocotb/_sim_versions.py",
    ]
    session.run(
        "pytest",
        "-v",
        "--doctest-modules",
        "--cov=cocotb",
        "--cov-branch",
        # Append to the .coverage file created in the previous pytest
        # invocation in this session.
        "--cov-append",
        "-k",
        "not simulator_required",
        *pytest_sourcetree,
    )

    session.log("All tests passed!")

    # Rename the .coverage file to make it unique for the
    Path(".coverage").rename(coverage_file)

    session.notify("dev_coverage_combine")


@nox.session
def dev_coverage_combine(session: nox.Session) -> None:
    """Combine coverage from previous dev_* runs into a .coverage file."""
    session.install(*coverage_deps)

    coverage_files = glob.glob("**/.coverage.test.*", recursive=True)
    session.run("coverage", "combine", *coverage_files)

    session.log("Wrote combined coverage database for all tests to '.coverage'.")


@nox.session
def docs(session: nox.Session) -> None:
    """invoke sphinx-build to build the HTML docs"""
    session.install("-r", "documentation/requirements.txt")
    session.install("-e", ".")
    outdir = session.cache_dir / "docs_out"
    session.run(
        "sphinx-build", "./documentation/source", str(outdir), "--color", "-b", "html"
    )
    index = (outdir / "index.html").resolve().as_uri()
    session.log(f"Documentation is available at {index}")


@nox.session
def docs_linkcheck(session: nox.Session) -> None:
    """invoke sphinx-build to linkcheck the docs"""
    session.install("-r", "documentation/requirements.txt")
    session.install("-e", ".")
    outdir = session.cache_dir / "docs_out"
    session.run(
        "sphinx-build",
        "./documentation/source",
        str(outdir),
        "--color",
        "-b",
        "linkcheck",
    )


@nox.session
def docs_spelling(session: nox.Session) -> None:
    """invoke sphinx-build to spellcheck the docs"""
    session.install("-r", "documentation/requirements.txt")
    session.install("-e", ".")
    outdir = session.cache_dir / "docs_out"
    session.run(
        "sphinx-build",
        "./documentation/source",
        str(outdir),
        "--color",
        "-b",
        "spelling",
    )


@nox.session(reuse_venv=True)
def dev(session: nox.Session) -> None:
    """Build a development environment and optionally run a command given as extra args"""
    session.install(*test_deps)
    session.install(*dev_deps)
    session.install("-e", ".")
    if session.posargs:
        session.run(*session.posargs, external=True)
