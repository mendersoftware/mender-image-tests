#!/usr/bin/python
# Copyright 2020 Northern.tech AS
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

import os
import shutil
import time
import errno
import tempfile
import subprocess
from pathlib import Path

import pytest
from fabric import Connection
from paramiko.client import WarningPolicy
from ..common import (
    build_image,
    manual_uboot_commit,
    latest_build_artifact,
    run_verbose,
    reset_build_conf,
    get_local_conf_path,
    get_local_conf_orig_path,
    get_bblayers_conf_path,
    get_bblayers_conf_orig_path,
    start_qemu_block_storage,
    start_qemu_flash,
    get_no_sftp,
    get_bitbake_variables,
    version_is_minimum,
)


def config_host(host):
    host_info = host.split(":")

    if len(host_info) == 2:
        return host_info[0], int(host_info[1])
    elif len(host_info) == 1:
        return host_info[0], 8822
    else:
        return "localhost", 8822


def connection_factory(request, user, host, ssh_priv_key):
    host, port = config_host(host)
    connect_kwargs = {
        "password": "",
        "banner_timeout": 60,
        "auth_timeout": 60,
        "look_for_keys": False,
        "allow_agent": False,
    }
    if ssh_priv_key != "":
        connect_kwargs["key_filename"] = ssh_priv_key
    conn = Connection(
        host=host,
        user=user,
        port=port,
        connect_timeout=60,
        connect_kwargs=connect_kwargs,
    )
    conn.client.set_missing_host_key_policy(WarningPolicy())

    def fin():
        conn.close()

    request.addfinalizer(fin)

    return conn


@pytest.fixture(scope="session")
def session_connection(request, user, host, ssh_priv_key):
    return connection_factory(request, user, host, ssh_priv_key)


@pytest.fixture(scope="function")
def connection(request, session_connection):
    def collect_coverage():
        # Collect the coverage files from /data/mender/ if present
        try:
            session_connection.run("ls /data/mender/cover*")
            Path("coverage").mkdir(exist_ok=True)
            get_no_sftp("/data/mender/cover*", session_connection, local="coverage")
            session_connection.run("rm -f /data/mender/cover*")
        except:
            pass

    request.addfinalizer(collect_coverage)

    return session_connection


@pytest.fixture(scope="session")
def second_connection(request, user, host, ssh_priv_key):
    return connection_factory(request, user, host, ssh_priv_key)


def setup_qemu(request, qemu_wrapper, build_dir, conn):
    latest_sdimg = latest_build_artifact(request, build_dir, ".sdimg")
    latest_uefiimg = latest_build_artifact(request, build_dir, ".uefiimg")
    latest_biosimg = latest_build_artifact(request, build_dir, ".biosimg")
    latest_gptimg = latest_build_artifact(request, build_dir, ".gptimg")
    latest_vexpress_nor = latest_build_artifact(request, build_dir, ".vexpress-nor")

    if latest_sdimg:
        qemu, img_path = start_qemu_block_storage(
            latest_sdimg, suffix=".sdimg", conn=conn, qemu_wrapper=qemu_wrapper
        )
    elif latest_uefiimg:
        qemu, img_path = start_qemu_block_storage(
            latest_uefiimg, suffix=".uefiimg", conn=conn, qemu_wrapper=qemu_wrapper
        )
    elif latest_biosimg:
        qemu, img_path = start_qemu_block_storage(
            latest_biosimg, suffix=".biosimg", conn=conn, qemu_wrapper=qemu_wrapper
        )
    elif latest_gptimg:
        qemu, img_path = start_qemu_block_storage(
            latest_gptimg, suffix=".gptimg", conn=conn, qemu_wrapper=qemu_wrapper
        )
    elif latest_vexpress_nor:
        qemu, img_path = start_qemu_flash(
            latest_vexpress_nor, conn=conn, qemu_wrapper=qemu_wrapper
        )
    else:
        pytest.fail("cannot find a suitable image type")

    print("qemu started with pid {}, image {}".format(qemu.pid, img_path))

    # Make sure we revert to the first root partition on next reboot, makes test
    # cases more predictable.
    def qemu_finalizer():
        def qemu_finalizer_impl(conn):
            # Try clearing firmware variables
            try:
                manual_uboot_commit(conn)
            except:
                pass

            # Try clean poweroff
            try:
                conn.run("poweroff")
                halt_time = time.time()
                # Wait up to 30 seconds for shutdown.
                while halt_time + 30 > time.time() and qemu.poll() is None:
                    time.sleep(1)
            except:
                pass

            # Terminate qemu
            try:
                qemu.terminate()
            except OSError as oserr:
                # qemu might have exited before we reached this place
                if oserr.errno == errno.ESRCH:
                    pass
                else:
                    raise

            qemu.wait()
            os.remove(img_path)

        qemu_finalizer_impl(conn=conn)

    request.addfinalizer(qemu_finalizer)


@pytest.fixture(scope="session")
def setup_board(request, qemu_wrapper, build_image_fn, session_connection, board_type):

    print("board type: ", board_type)

    if "qemu" in board_type:
        image_dir = build_image_fn()
        return setup_qemu(request, qemu_wrapper, image_dir, session_connection)
    else:
        pytest.fail("unsupported board type {}".format(board_type))

    """Make sure 'image.dat' is not present on the device."""
    session_connection.run("rm -f image.dat")


@pytest.fixture(scope="session")
def latest_rootfs(request, conversion, mender_image):
    assert os.environ.get("BUILDDIR", False), "BUILDDIR must be set"

    # Find latest built rootfs.
    if conversion:
        image_name = os.path.splitext(mender_image)[0]
        return latest_build_artifact(
            request, os.environ["BUILDDIR"], "%s.ext[234]" % image_name
        )
    else:
        return latest_build_artifact(
            request, os.environ["BUILDDIR"], "core-image*.ext[234]"
        )


@pytest.fixture(scope="session")
def latest_sdimg(request):
    assert os.environ.get("BUILDDIR", False), "BUILDDIR must be set"

    # Find latest built sdimg.
    return latest_build_artifact(request, os.environ["BUILDDIR"], "core-image*.sdimg")


@pytest.fixture(scope="session")
def latest_ubimg(request):
    assert os.environ.get("BUILDDIR", False), "BUILDDIR must be set"

    # Find latest built ubimg.
    return latest_build_artifact(request, os.environ["BUILDDIR"], "core-image*.ubimg")


@pytest.fixture(scope="session")
def latest_ubifs(request):
    assert os.environ.get("BUILDDIR", False), "BUILDDIR must be set"

    # Find latest built ubifs. NOTE: need to include *core-image* otherwise
    # we'll likely match data partition file - data.ubifs
    return latest_build_artifact(request, os.environ["BUILDDIR"], "core-image*.ubifs")


@pytest.fixture(scope="session")
def latest_vexpress_nor(request):
    assert os.environ.get("BUILDDIR", False), "BUILDDIR must be set"

    # Find latest built ubifs. NOTE: need to include *core-image* otherwise
    # we'll likely match data partition file - data.ubifs
    return latest_build_artifact(
        request, os.environ["BUILDDIR"], "core-image*.vexpress-nor"
    )


@pytest.fixture(scope="session")
def latest_mender_image(request, conversion, mender_image):
    assert os.environ.get("BUILDDIR", False), "BUILDDIR must be set"

    # Find latest built rootfs.
    if conversion:
        image_name = os.path.splitext(mender_image)[0]
        return latest_build_artifact(
            request, os.environ["BUILDDIR"], "%s.mender" % image_name
        )
    else:
        return latest_build_artifact(
            request, os.environ["BUILDDIR"], "core-image*.mender"
        )


@pytest.fixture(scope="session")
def latest_part_image(request, conversion, mender_image):
    assert os.environ.get("BUILDDIR", False), "BUILDDIR must be set"

    if conversion:
        pattern = os.path.splitext(mender_image)[0]
    else:
        pattern = "core-image*"

    # Find latest built rootfs.
    latest_sdimg = latest_build_artifact(
        request, os.environ["BUILDDIR"], "%s.sdimg" % pattern
    )
    latest_uefiimg = latest_build_artifact(
        request, os.environ["BUILDDIR"], "%s.uefiimg" % pattern
    )
    latest_biosimg = latest_build_artifact(
        request, os.environ["BUILDDIR"], "%s.biosimg" % pattern
    )
    latest_gptimg = latest_build_artifact(
        request, os.environ["BUILDDIR"], "%s.gptimg" % pattern
    )
    if latest_sdimg:
        return latest_sdimg
    elif latest_uefiimg:
        return latest_uefiimg
    elif latest_biosimg:
        return latest_biosimg
    elif latest_gptimg:
        return latest_gptimg
    else:
        # Tempting to throw an exception here, but this runs even for platforms
        # that skip the test, so we should return None instead.
        return None


@pytest.fixture(scope="function")
def successful_image_update_mender(request, build_image_fn):
    """Provide a 'successful_image_update.mender' file in the current directory that
    contains the latest built update."""

    latest_mender_image = latest_build_artifact(request, build_image_fn(), ".mender")

    shutil.copy(latest_mender_image, "successful_image_update.mender")

    print("Copying '%s' to 'successful_image_update.mender'" % latest_mender_image)

    def cleanup_image_dat():
        os.remove("successful_image_update.mender")

    request.addfinalizer(cleanup_image_dat)

    return "successful_image_update.mender"


#
# bitbake related fixtures
#
@pytest.fixture(scope="session")
def bitbake_variables(request, conversion, sdimg_location):
    """Returns a map of all bitbake variables active for the build."""

    if conversion:
        os.environ["BUILDDIR"] = sdimg_location

    assert os.environ.get("BUILDDIR", False), "BUILDDIR must be set"
    return get_bitbake_variables(request, "core-image-minimal")


@pytest.fixture(scope="session")
def bitbake_path(request, conversion):
    """Fixture that enables the PATH we need for our testing tools."""

    old_path = os.environ["PATH"]

    if not conversion:
        run_verbose("bitbake -c prepare_recipe_sysroot mender-test-dependencies")
        bb_testing_variables = get_bitbake_variables(
            request, "mender-test-dependencies"
        )
        os.environ["PATH"] = bb_testing_variables["PATH"] + ":" + os.environ["PATH"]

    def path_restore():
        os.environ["PATH"] = old_path

    request.addfinalizer(path_restore)

    return os.environ["PATH"]


@pytest.fixture(scope="session")
def build_image_fn(request, conversion, prepared_test_build_base, bitbake_image):
    """Returns a function which returns the build dir of a clean image.

    The reason it does not return the build dir directly is that it may need to
    be reset to a clean state if several independent fixtures invoke it, and
    there have been unclean builds in between.
    """

    def img_builder():
        if conversion:
            assert os.environ.get("BUILDDIR", False), "BUILDDIR must be set"
            return os.environ["BUILDDIR"]

        reset_build_conf(prepared_test_build_base["build_dir"])
        build_image(
            prepared_test_build_base["build_dir"],
            prepared_test_build_base["bitbake_corebase"],
            bitbake_image,
        )
        return prepared_test_build_base["build_dir"]

    return img_builder


@pytest.fixture(scope="session")
def prepared_test_build_base(
    request, conversion, bitbake_variables, no_tmp_build_dir, keep_tmp_build_dir
):

    if conversion:
        return {"build_dir": None, "bitbake_corebase": None}

    if no_tmp_build_dir:
        build_dir = os.environ["BUILDDIR"]
    else:
        build_dir = tempfile.mkdtemp(prefix="test-build-", dir=os.environ["BUILDDIR"])

    local_conf = get_local_conf_path(build_dir)
    local_conf_orig = get_local_conf_orig_path(build_dir)
    bblayers_conf = get_bblayers_conf_path(build_dir)
    bblayers_conf_orig = get_bblayers_conf_orig_path(build_dir)

    def cleanup_test_build():
        if not no_tmp_build_dir and not keep_tmp_build_dir:
            run_verbose("rm -rf %s" % build_dir)
        else:
            reset_build_conf(build_dir, full_cleanup=True)

    cleanup_test_build()
    request.addfinalizer(cleanup_test_build)

    env_setup = "cd %s && . oe-init-build-env %s" % (
        bitbake_variables["COREBASE"],
        build_dir,
    )
    run_verbose(env_setup)

    if not no_tmp_build_dir:
        run_verbose("cp %s/conf/* %s/conf" % (os.environ["BUILDDIR"], build_dir))
        with open(local_conf, "a") as fd:
            fd.write(
                'SSTATE_MIRRORS = " file://.* file://%s/PATH"\n'
                % bitbake_variables["SSTATE_DIR"]
            )
            fd.write('DL_DIR = "%s"\n' % bitbake_variables["DL_DIR"])

    run_verbose("cp %s %s" % (local_conf, local_conf_orig))
    run_verbose("cp %s %s" % (bblayers_conf, bblayers_conf_orig))

    return {"build_dir": build_dir, "bitbake_corebase": bitbake_variables["COREBASE"]}


@pytest.fixture(scope="function")
def prepared_test_build(prepared_test_build_base):
    """
    Prepares a separate test build directory where a custom build can be
    made, which reuses the sstate-cache.
    """

    reset_build_conf(prepared_test_build_base["build_dir"])
    return prepared_test_build_base


@pytest.fixture(autouse=True)
def min_mender_version(request, bitbake_variables):
    version_mark = request.node.get_closest_marker("min_mender_version")
    if version_mark is None:
        pytest.fail(
            (
                '%s must be marked with @pytest.mark.min_mender_version("<VERSION>") to '
                + "indicate lowest Mender version for which the test will work."
            )
            % str(request.node)
        )

    test_version = version_mark.args[0]
    if not version_is_minimum(bitbake_variables, "mender-client", test_version):
        pytest.skip("Test requires Mender client %s or newer" % test_version)


@pytest.fixture(autouse=True)
def min_yocto_version(request, bitbake_variables):
    version_mark = request.node.get_closest_marker("min_yocto_version")
    if version_mark is None:
        return

    if request.config.getoption("--test-conversion"):
        return

    yocto_versions_ordered = [
        "krogoth",
        "morty",
        "pyro",
        "rocko",
        "sumo",
        "thud",
        "warrior",
        "zeus",
        "dunfell",
        "gatesgarth",
        "hardknott",
        "honister",
        "kirkstone",
        # Keep this at the bottom.
        "master",
    ]

    test_version = version_mark.args[0]

    candidates = [
        "'refs/heads/%s' 'refs/remotes/*/%s'" % (branch, branch)
        for branch in yocto_versions_ordered
    ]

    # Technique taken from release_tool.py in integration repository:

    # Return "closest" branch or tag name. Basically we measure the distance in
    # commits from the merge base of most refs to the current HEAD, and then
    # pick the shortest one, and we assume that this is our current version. We
    # pick all the refs from tags and local branches, as well as single level
    # upstream branches (which avoids pull requests).

    # An additional tweak here is that we only consider the well known branch
    # names from Yocto as candidates.
    yocto_version = (
        subprocess.check_output(
            """
        for i in $(git for-each-ref --format='%%(refname:short)' %s); do
            echo $(git log --oneline $(git merge-base $i HEAD)..HEAD | wc -l) $i
        done | sort -n | head -n1 | awk '{print $2}'
        """
            % " ".join(candidates),
            shell=True,
        )
        .strip()
        .decode()
    )

    # Get rid of remote information, if any.
    if yocto_version.rfind("/"):
        yocto_version = yocto_version[yocto_version.rfind("/") + 1 :]

    if yocto_versions_ordered.index(test_version) > yocto_versions_ordered.index(
        yocto_version
    ):
        pytest.skip(
            "Test requires minimum Yocto version '%s' and current Yocto version is '%s'"
            % (test_version, yocto_version)
        )


@pytest.fixture(autouse=True)
def only_for_machine(request, bitbake_variables):
    """Fixture that enables use of `only_for_machine(machine-name)` mark.
    Example::

       @pytest.mark.only_for_machine('vexpress-qemu')
       def test_foo():
           # executes only if building for vexpress-qemu
           pass

    """
    mach_mark = request.node.get_closest_marker("only_for_machine")
    if mach_mark is not None:
        machines = mach_mark.args
        current = bitbake_variables.get("MACHINE", None)
        if current not in machines:
            pytest.skip(
                "incompatible machine {} "
                "(required {})".format(
                    current if not None else "(none)", ", ".join(machines)
                )
            )


@pytest.fixture(autouse=True)
def not_for_machine(request, bitbake_variables):
    """Fixture that enables use of `not_for_machine(machine-name)` mark.
    Example::

       @pytest.mark.not_for_machine('vexpress-qemu')
       def test_foo():
           # executes only if not building for vexpress-qemu
           pass

    """
    mach_mark = request.node.get_closest_marker("not_for_machine")
    if mach_mark is not None:
        machines = mach_mark.args
        current = bitbake_variables.get("MACHINE", None)
        if current in machines:
            pytest.skip(
                "incompatible machine {} ".format(current if not None else "(none)")
            )


@pytest.fixture(autouse=True)
def only_with_image(request, bitbake_variables):
    """Fixture that enables use of `only_with_image(img1, img2)` mark.
    Example::

       @pytest.mark.only_with_image('ext4')
       def test_foo():
           # executes only if ext4 image is enabled
           pass

    """
    mark = request.node.get_closest_marker("only_with_image")
    if mark is not None:
        images = mark.args
        current = bitbake_variables.get("IMAGE_FSTYPES", "").strip().split()
        current.append(bitbake_variables.get("ARTIFACTIMG_FSTYPE", ""))
        if not any([img in current for img in images]):
            pytest.skip(
                "no supported filesystem in {} "
                "(supports {})".format(", ".join(current), ", ".join(images))
            )


@pytest.fixture(autouse=True)
def only_with_mender_feature(request, bitbake_variables):
    """Fixture that enables use of `only_with_mender_feature(feature1, feature2)` mark.
    Example::

       @pytest.mark.only_with_mender_feature('mender-uboot')
       def test_foo():
           # executes only if mender-uboot feature is enabled
           pass

    """

    mark = request.node.get_closest_marker("only_with_mender_feature")
    if mark is None:
        # For historical reasons we also use the old marker name.
        mark = request.node.get_closest_marker("only_with_distro_feature")

    if mark is not None:
        features = mark.args
        current = bitbake_variables.get("MENDER_FEATURES", "").strip().split()
        # For historical reasons we also check in DISTRO_FEATURES.
        current += bitbake_variables.get("DISTRO_FEATURES", "").strip().split()
        if not all([feature in current for feature in features]):
            pytest.skip(
                "no supported distro feature in {} "
                "(supports {})".format(", ".join(current), ", ".join(features))
            )


@pytest.fixture(autouse=True)
def not_with_mender_feature(request, bitbake_variables):
    """Fixture that enables use of `not_with_mender_feature(feature1, feature2)` mark.
    Example::

       @pytest.mark.not_with_mender_feature('mender-uboot')
       def test_foo():
           # executes only if mender-uboot feature is not enabled
           pass

    """

    mark = request.node.get_closest_marker("not_with_mender_feature")
    if mark is not None:
        features = mark.args
        current = bitbake_variables.get("MENDER_FEATURES", "").strip().split()
        if any([feature in current for feature in features]):
            pytest.skip(
                "supported distro feature in {} "
                "(excludes {})".format(", ".join(current), ", ".join(features))
            )


@pytest.fixture(scope="session")
def host(request):
    return request.config.getoption("--host")


@pytest.fixture(scope="session")
def user(request):
    return request.config.getoption("--user")


@pytest.fixture(scope="session")
def ssh_priv_key(request):
    return request.config.getoption("--ssh-priv-key")


@pytest.fixture(scope="session")
def http_server(request):
    return request.config.getoption("--http-server")


@pytest.fixture(scope="session")
def board_type(request):
    return request.config.getoption("--board-type")


@pytest.fixture(scope="session")
def sdimg_location(request):
    return request.config.getoption("--sdimg-location")


@pytest.fixture(scope="session")
def qemu_wrapper(request):
    return request.config.getoption("--qemu-wrapper")


@pytest.fixture(scope="session")
def mender_image(request):
    return request.config.getoption("--mender-image")


@pytest.fixture(scope="session")
def bitbake_image(request):
    return request.config.getoption("--bitbake-image")


@pytest.fixture(scope="session")
def conversion(request):
    return request.config.getoption("--test-conversion")


@pytest.fixture(scope="session")
def use_s3(request):
    return request.config.getoption("--use-s3")


@pytest.fixture(scope="session")
def s3_address(request):
    return request.config.getoption("--s3-address")


@pytest.fixture(scope="session")
def no_tmp_build_dir(request):
    return request.config.getoption("--no-tmp-build-dir")


@pytest.fixture(scope="session")
def keep_tmp_build_dir(request):
    return request.config.getoption("--keep-tmp-build-dir")


@pytest.fixture(scope="session")
def test_variables(request):
    return request.config.getoption("--test-variables")


@pytest.fixture(autouse=True)
def commercial_test(request, bitbake_variables):
    mark = request.node.get_closest_marker("commercial")
    if mark is not None and not request.config.getoption("--commercial-tests"):
        pytest.skip("Tests of commercial features are disabled.")
