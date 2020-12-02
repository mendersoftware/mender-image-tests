import os
import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--host",
        action="store",
        default="localhost:8822",
        help="""IP to connect to, with optional port. Defaults
                     to localhost:8822, which is what the QEMU script sets up.""",
    )
    parser.addoption(
        "--user",
        action="store",
        default="root",
        help="user to log into remote hosts with (default is root)",
    )
    parser.addoption(
        "--ssh-priv-key",
        action="store",
        default="",
        help="Path to an SSH private key if required for login",
    )
    parser.addoption(
        "--http-server",
        action="store",
        default="10.0.2.2:8000",
        help="Remote HTTP server containing update image",
    )
    parser.addoption(
        "--sdimg-location",
        action="store",
        default=os.getcwd(),
        help="location to the image to test (BUILDDIR for Yocto, deploy for mender-convert)",
    )
    parser.addoption(
        "--qemu-wrapper",
        action="store",
        default="../../meta-mender-qemu/scripts/mender-qemu",
        help="location of the shell wrapper to launch QEMU with testing image",
    )
    parser.addoption(
        "--bitbake-image",
        action="store",
        default="core-image-full-cmdline",
        help="image to build during the tests",
    )
    parser.addoption(
        "--no-tmp-build-dir",
        action="store_true",
        default=False,
        help="Do not use a temporary build directory. Faster, but may mess with your build directory.",
    )
    parser.addoption(
        "--board-type",
        action="store",
        default="qemu",
        help="type of board to use in testing, supported types: qemu, bbb, colibri-imx7",
    )
    parser.addoption(
        "--use-s3",
        action="store_true",
        default=False,
        help="use S3 for transferring images under test to target boards",
    )
    parser.addoption(
        "--s3-address",
        action="store",
        default="s3.amazonaws.com",
        help="address of S3 server, defaults to AWS, override when using minio",
    )
    parser.addoption(
        "--test-conversion",
        action="store_true",
        default=False,
        help="""conduct testing of .sdimg image built with mender-convert tool""",
    )
    parser.addoption(
        "--test-variables",
        action="store",
        default="default",
        help="configuration file holding settings for dedicated platform",
    )
    parser.addoption(
        "--mender-image",
        action="store",
        default="default",
        help="Mender compliant raw disk image",
    )
    parser.addoption(
        "--commercial-tests",
        action="store_true",
        help="Enable tests of commercial features",
    )


def pytest_configure(config):
    #
    # Register the plugins markers as per
    # https://pytest.org/en/latest/writing_plugins.html#registering-custom-markers
    #
    config.addinivalue_line(
        "markers",
        "min_mender_version: indicate lowest Mender version for which the test will run",
    )
    config.addinivalue_line(
        "markers",
        "min_yocto_version: indicate lowest Yocto version for which the test will run",
    )
    config.addinivalue_line(
        "markers", "only_for_machine: execute only for the given machine"
    )
    config.addinivalue_line(
        "markers", "only_with_image: execute only if one of the given images is enabled"
    )
    config.addinivalue_line(
        "markers",
        "only_with_distro_feature: execute only if all given features are enabled (dep)recated, but may still be used by old Yocto branches and mender-convert, due to sharing of fixtures.py",
    )
    config.addinivalue_line(
        "markers",
        "only_with_mender_feature: execute only if all given features are enabled",
    )
    config.addinivalue_line("markers", "commercial: run commercial tests")
    config.addinivalue_line(
        "markers",
        "conversion: mark test to run only when --test-conversion cli parameter is provided",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--test-conversion"):
        # --test-conversion given so do not skip conversion tests
        return
    skip_conversion = pytest.mark.skip(
        reason="conversion tests not yet working in Yocto"
    )
    for item in items:
        if "conversion" in item.keywords:
            item.add_marker(skip_conversion)
