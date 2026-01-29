#!/usr/bin/python
# Copyright 2023 Northern.tech AS
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


import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from utils.common import (
    bootenv_tools,
    determine_active_passive_part,
    get_no_sftp,
    make_tempdir,
    put_no_sftp,
    reboot,
    run_after_connect,
    signing_key,
)
from utils.helpers import Helpers


class SignatureCase:
    label = ""
    signature = False
    signature_ok = False
    key = False
    key_type = ""
    checksum_ok = True
    header_checksum_ok = True

    update_written = False
    success = True

    def __init__(
        self,
        label,
        signature,
        signature_ok,
        key,
        key_type,
        checksum_ok,
        header_checksum_ok,
        update_written,
        artifact_version,
        success,
    ):
        self.label = label
        self.signature = signature
        self.signature_ok = signature_ok
        self.key = key
        self.key_type = key_type
        self.checksum_ok = checksum_ok
        self.header_checksum_ok = header_checksum_ok
        self.update_written = update_written
        self.artifact_version = artifact_version
        self.success = success


@pytest.mark.usefixtures("setup_board", "bitbake_path")
class TestUpdates:
    @pytest.mark.min_mender_version("4.0.0")
    def test_broken_image_update(self, bitbake_variables, connection):
        """Test that an update with a broken filesystem rolls back correctly."""

        file_flag = Helpers.get_file_flag(bitbake_variables)
        (active_before, passive_before) = determine_active_passive_part(
            bitbake_variables, connection
        )

        image_type = bitbake_variables["MENDER_DEVICE_TYPE"]

        with tempfile.NamedTemporaryFile() as image_dat, tempfile.NamedTemporaryFile(
            suffix=".mender"
        ) as image_mender:
            # Make a dummy/broken update
            retcode = subprocess.check_call(
                f"dd if=/dev/zero of={image_dat.name} bs=1M count=0 seek=16", shell=True
            )
            retcode = subprocess.check_call(
                "mender-artifact write rootfs-image -t %s -n test-update %s %s -o %s"
                % (image_type, file_flag, image_dat.name, image_mender.name),
                shell=True,
            )

            put_no_sftp(image_mender.name, connection, remote="/var/tmp/image.mender")
            connection.run("mender-update install /var/tmp/image.mender")
            try:
                reboot(connection)

                # Now qemu is auto-rebooted twice; once to boot the dummy image,
                # where it fails, and the boot loader auto-reboots a second time
                # into the original partition.

                output = run_after_connect("mount", connection)

                # The update should have reverted to the original active partition,
                # since the image was bogus.
                assert output.find(active_before) >= 0
                assert output.find(passive_before) < 0

            finally:
                connection.run("mender-update rollback")

    # We will use mender-artifact to modify an artifact in this test. Because it
    # doesn't support ubifs modifications, disable it for vexpress-qemu-flash.
    @pytest.mark.not_for_machine("vexpress-qemu-flash")
    @pytest.mark.min_mender_version("4.0.0")
    def test_image_update_broken_kernel(
        self,
        bitbake_variables,
        connection,
        latest_mender_image,
        http_server,
        board_type,
        use_s3,
        s3_address,
    ):
        """Test that an update with a broken kernel rolls back correctly. This is
        distinct from the test_broken_image_update test, which corrupts the
        filesystem. When grub.d integration is enabled, these two scenarios
        trigger very different code paths."""

        file_flag = Helpers.get_file_flag(bitbake_variables)
        (active_before, passive_before) = determine_active_passive_part(
            bitbake_variables, connection
        )

        image_type = bitbake_variables["MENDER_DEVICE_TYPE"]

        with tempfile.NamedTemporaryFile(suffix=".mender") as temp_artifact:
            shutil.copyfile(latest_mender_image, temp_artifact.name)
            # Assume that artifact has the same kernel names as the currently
            # running image.
            kernels = connection.run(
                "find /boot/ -maxdepth 1 -name '*linu[xz]*' -o -name '*Image'"
            ).stdout.split()
            for kernel in kernels:
                # Inefficient, but there shouldn't be too many kernels.
                subprocess.check_call(
                    ["mender-artifact", "rm", f"{temp_artifact.name}:{kernel}"]
                )

            Helpers.install_update(
                temp_artifact.name,
                connection,
                http_server,
                board_type,
                use_s3,
                s3_address,
            )

            try:
                reboot(connection)

                # Now qemu is auto-rebooted twice; once to boot the dummy image,
                # where it fails, and the boot loader auto-reboots a second time
                # into the original partition.

                output = run_after_connect("mount", connection)

                # The update should have reverted to the original active partition,
                # since the kernel was missing.
                assert output.find(active_before) >= 0
                assert output.find(passive_before) < 0

            finally:
                connection.run("mender-update rollback")

    @pytest.mark.cross_platform
    @pytest.mark.min_mender_version("4.0.0")
    def test_too_big_image_update(self, bitbake_variables, connection):

        file_flag = Helpers.get_file_flag(bitbake_variables)
        image_type = bitbake_variables["MENDER_DEVICE_TYPE"]

        with tempfile.NamedTemporaryFile() as image_dat, tempfile.NamedTemporaryFile(
            suffix=".mender"
        ) as image_too_big_mender:
            # Make a too big update
            subprocess.check_call(
                f"dd if=/dev/zero of={image_dat.name} bs=1M count=0 seek=4096",
                shell=True,
            )
            subprocess.check_call(
                "mender-artifact write rootfs-image -t %s -n test-update-too-big %s %s -o %s"
                % (image_type, file_flag, image_dat.name, image_too_big_mender.name),
                shell=True,
            )
            put_no_sftp(
                image_too_big_mender.name,
                connection,
                remote="/var/tmp/image-too-big.mender",
            )
            output = connection.run(
                "mender-update install /var/tmp/image-too-big.mender ; echo ret_code=$?"
            )

            allowed_msgs = ["no space left on device"]
            if bitbake_variables["MACHINE"] == "vexpress-qemu-flash":
                # On UBI, we get this message instead because we are not permitted to schedule a
                # write larger than the device size.
                allowed_msgs.append("operation not permitted")
            assert any(
                [
                    any([msg in out.lower() for msg in allowed_msgs])
                    for out in [output.stderr, output.stdout]
                ]
            ), output

            assert "ret_code=0" not in output.stdout, output

    @pytest.mark.min_mender_version("4.0.0")
    def test_network_based_image_update(
        self,
        successful_image_update_mender,
        bitbake_variables,
        connection,
        http_server,
        board_type,
        use_s3,
        s3_address,
    ):

        (active_before, passive_before) = determine_active_passive_part(
            bitbake_variables, connection
        )

        Helpers.install_update(
            successful_image_update_mender,
            connection,
            http_server,
            board_type,
            use_s3,
            s3_address,
        )

        bootenv_print, _ = bootenv_tools(connection)

        output = connection.run(f"{bootenv_print} bootcount").stdout
        assert output.rstrip("\n") == "bootcount=0"

        output = connection.run(f"{bootenv_print} upgrade_available").stdout
        assert output.rstrip("\n") == "upgrade_available=1"

        output = connection.run(f"{bootenv_print} mender_boot_part").stdout
        assert output.rstrip("\n") == "mender_boot_part=" + passive_before[-1:]

        # Delete kernel and associated files from currently running partition,
        # so that the boot will fail if U-Boot for any reason tries to grab the
        # kernel from the wrong place.
        connection.run("rm -f /boot/* || true")

        reboot(connection)

        run_after_connect("true", connection)
        (active_after, passive_after) = determine_active_passive_part(
            bitbake_variables, connection
        )

        # The OS should have moved to a new partition, since the image was fine.
        assert active_after == passive_before
        assert passive_after == active_before

        output = connection.run(f"{bootenv_print} bootcount").stdout
        assert output.rstrip("\n") == "bootcount=1"

        output = connection.run(f"{bootenv_print} upgrade_available").stdout
        assert output.rstrip("\n") == "upgrade_available=1"

        output = connection.run(f"{bootenv_print} mender_boot_part").stdout
        assert output.rstrip("\n") == "mender_boot_part=" + active_after[-1:]

        connection.run("mender-update commit")

        output = connection.run(f"{bootenv_print} upgrade_available").stdout
        assert output.rstrip("\n") == "upgrade_available=0"

        output = connection.run(f"{bootenv_print} mender_boot_part").stdout
        assert output.rstrip("\n") == "mender_boot_part=" + active_after[-1:]

        active_before = active_after
        passive_before = passive_after

        reboot(connection)

        run_after_connect("true", connection)
        (active_after, passive_after) = determine_active_passive_part(
            bitbake_variables, connection
        )

        # The OS should have stayed on the same partition, since we committed.
        assert active_after == active_before
        assert passive_after == passive_before

    @pytest.mark.parametrize(
        "sig_case",
        [
            SignatureCase(
                label="Not signed, key not present",
                signature=False,
                signature_ok=False,
                key=False,
                key_type=None,
                checksum_ok=True,
                header_checksum_ok=True,
                update_written=True,
                artifact_version=None,
                success=True,
            ),
            SignatureCase(
                label="RSA, Correctly signed, key present",
                signature=True,
                signature_ok=True,
                key=True,
                key_type="RSA",
                checksum_ok=True,
                header_checksum_ok=True,
                update_written=True,
                artifact_version=None,
                success=True,
            ),
            SignatureCase(
                label="RSA, Incorrectly signed, key present",
                signature=True,
                signature_ok=False,
                key=True,
                key_type="RSA",
                checksum_ok=True,
                header_checksum_ok=True,
                update_written=False,
                artifact_version=None,
                success=False,
            ),
            SignatureCase(
                label="RSA, Correctly signed, key not present",
                signature=True,
                signature_ok=True,
                key=False,
                key_type="RSA",
                checksum_ok=True,
                header_checksum_ok=True,
                update_written=True,
                artifact_version=None,
                success=True,
            ),
            SignatureCase(
                label="RSA, Not signed, key present",
                signature=False,
                signature_ok=False,
                key=True,
                key_type="RSA",
                checksum_ok=True,
                header_checksum_ok=True,
                update_written=False,
                artifact_version=None,
                success=False,
            ),
            SignatureCase(
                label="RSA, Correctly signed, but checksum wrong, key present",
                signature=True,
                signature_ok=True,
                key=True,
                key_type="RSA",
                checksum_ok=False,
                header_checksum_ok=True,
                update_written=True,
                artifact_version=None,
                success=False,
            ),
            SignatureCase(
                label="EC, Correctly signed, key present",
                signature=True,
                signature_ok=True,
                key=True,
                key_type="EC",
                checksum_ok=True,
                header_checksum_ok=True,
                update_written=True,
                artifact_version=None,
                success=True,
            ),
            SignatureCase(
                label="EC, Incorrectly signed, key present",
                signature=True,
                signature_ok=False,
                key=True,
                key_type="EC",
                checksum_ok=True,
                header_checksum_ok=True,
                update_written=False,
                artifact_version=None,
                success=False,
            ),
            SignatureCase(
                label="EC, Correctly signed, key not present",
                signature=True,
                signature_ok=True,
                key=False,
                key_type="EC",
                checksum_ok=True,
                header_checksum_ok=True,
                update_written=True,
                artifact_version=None,
                success=True,
            ),
            SignatureCase(
                label="EC, Not signed, key present",
                signature=False,
                signature_ok=False,
                key=True,
                key_type="EC",
                checksum_ok=True,
                header_checksum_ok=True,
                update_written=False,
                artifact_version=None,
                success=False,
            ),
            SignatureCase(
                label="EC, Correctly signed, but checksum wrong, key present",
                signature=True,
                signature_ok=True,
                key=True,
                key_type="EC",
                checksum_ok=False,
                header_checksum_ok=True,
                update_written=True,
                artifact_version=None,
                success=False,
            ),
            SignatureCase(
                label="EC, Correctly signed, but header does not match checksum, key present",
                signature=True,
                signature_ok=True,
                key=True,
                key_type="EC",
                checksum_ok=True,
                header_checksum_ok=False,
                update_written=False,
                artifact_version=None,
                success=False,
            ),
        ],
    )
    @pytest.mark.cross_platform
    @pytest.mark.min_mender_version("4.0.0")
    def test_signed_updates(self, sig_case, bitbake_variables, connection):
        """Test various combinations of signed and unsigned, present and non-
        present verification keys."""

        with make_tempdir() as tmpdir:
            origdir = os.getcwd()
            os.chdir(tmpdir)
            try:
                file_flag = Helpers.get_file_flag(bitbake_variables)

                # mmc mount points are named: /dev/mmcblk0p1
                # ubi volumes are named: ubi0_1
                (active, passive) = determine_active_passive_part(
                    bitbake_variables, connection
                )
                if passive.startswith("ubi"):
                    passive = "/dev/" + passive

                # Generate "update" appropriate for this test case.
                # Cheat a little. Instead of spending a lot of time on a lot of reboots,
                # just verify that the contents of the update are correct.
                new_content = sig_case.label
                with open("image.dat", "w") as fd:
                    fd.write(new_content)
                    # Write some extra data just to make sure the update is big enough
                    # to be written even if the checksum is wrong. If it's too small it
                    # may fail before it has a chance to be written.
                    fd.write("\x00" * (1048576 * 8))

                artifact_args = ""

                # Generate artifact with or without signature.
                if sig_case.signature:
                    artifact_args += " -k %s" % os.path.join(
                        origdir, signing_key(sig_case.key_type).private
                    )

                # Generate artifact with specific version. None means default.
                if sig_case.artifact_version is not None:
                    artifact_args += " -v %d" % sig_case.artifact_version

                if sig_case.key_type:
                    sig_key = signing_key(sig_case.key_type)
                else:
                    sig_key = None

                image_type = bitbake_variables["MENDER_DEVICE_TYPE"]

                subprocess.check_call(
                    "mender-artifact write rootfs-image %s -t %s -n test-update %s image.dat -o image.mender"
                    % (artifact_args, image_type, file_flag),
                    shell=True,
                )

                # If instructed to, corrupt the signature and/or checksum.
                if (
                    (sig_case.signature and not sig_case.signature_ok)
                    or not sig_case.checksum_ok
                    or not sig_case.header_checksum_ok
                ):
                    self.manipulate_artifact_internals(sig_case, "image.mender")

                put_no_sftp("image.mender", connection, remote="/data/image.mender")

                # mender-convert'ed images don't have transient mender.conf
                device_has_mender_conf = (
                    connection.run(
                        "test -f /etc/mender/mender.conf", warn=True
                    ).return_code
                    == 0
                )
                # mender-convert'ed images don't have this directory, but the test uses
                # it to save certificates
                connection.run("mkdir -p /data/etc/mender")

                try:
                    # Get configuration from device or create an empty one
                    if device_has_mender_conf:
                        connection.run(
                            "cp /etc/mender/mender.conf /data/etc/mender/mender.conf.bak"
                        )
                        get_no_sftp("/etc/mender/mender.conf", connection)
                    else:
                        with open("mender.conf", "w") as fd:
                            json.dump({}, fd)

                    # Update key in configuration.
                    with open("mender.conf") as fd:
                        config = json.load(fd)
                    if sig_case.key:
                        config[
                            "ArtifactVerifyKey"
                        ] = "/data/etc/mender/%s" % os.path.basename(sig_key.public)
                        put_no_sftp(
                            os.path.join(origdir, sig_key.public),
                            connection,
                            remote="/data/etc/mender/%s"
                            % os.path.basename(sig_key.public),
                        )
                    else:
                        if config.get("ArtifactVerifyKey"):
                            del config["ArtifactVerifyKey"]

                    # Send new configuration to device
                    with open("mender.conf", "w") as fd:
                        json.dump(config, fd)
                    put_no_sftp(
                        "mender.conf", connection, remote="/etc/mender/mender.conf"
                    )
                    os.remove("mender.conf")

                    # Start by writing known "old" content in the partition.
                    old_content = "Preexisting partition content"
                    if "ubi" in passive:
                        # ubi volumes cannot be directly written to, we have to use
                        # ubiupdatevol
                        connection.run(
                            'echo "%s" | dd of=/tmp/update.tmp && '
                            "ubiupdatevol %s /tmp/update.tmp; "
                            "rm -f /tmp/update.tmp" % (old_content, passive)
                        )
                    else:
                        connection.run('echo "%s" | dd of=%s' % (old_content, passive))

                    result = connection.run(
                        "mender-update install /data/image.mender", warn=True
                    )

                    if result.return_code == 0:
                        # Just reset database for next update. The content is still there, IOW it
                        # doesn't wipe it, and we can still test it.
                        connection.run("mender-update rollback")

                    if sig_case.success:
                        if result.return_code != 0:
                            pytest.fail(
                                "Update failed when it should have succeeded: %s, Output: %s"
                                % (sig_case.label, result)
                            )
                    else:
                        if result.return_code == 0:
                            pytest.fail(
                                "Update succeeded when it should not have: %s, Output: %s"
                                % (sig_case.label, result)
                            )

                    if sig_case.update_written:
                        expected_content = new_content
                    else:
                        expected_content = old_content

                    try:
                        content = connection.run(
                            "dd if=%s bs=%d count=1" % (passive, len(expected_content))
                        ).stdout
                        assert content == expected_content, "Case: %s" % sig_case.label

                    except subprocess.CalledProcessError:
                        if (
                            "mender-ubi"
                            in bitbake_variables.get("MENDER_FEATURES", "").split()
                            or "mender-ubi"
                            in bitbake_variables.get("DISTRO_FEATURES", "").split()
                        ):
                            # For UBI volumes specifically: The UBI_IOCVOLUP call which
                            # Mender uses prior to writing the data, takes a size
                            # argument, and if you don't write that amount of bytes, the
                            # volume is marked corrupted as a security measure. This
                            # sometimes triggers in our checksum mismatch tests, so
                            # accept the volume being unreadable in that case.
                            pass
                        else:
                            raise

                finally:
                    # Reset environment to what it was.
                    _, bootenv_set = bootenv_tools(connection)
                    connection.run(f"{bootenv_set} mender_boot_part %s" % active[-1:])
                    connection.run(
                        f"{bootenv_set} mender_boot_part_hex %x" % int(active[-1:])
                    )
                    connection.run(f"{bootenv_set} upgrade_available 0")
                    if device_has_mender_conf:
                        connection.run(
                            "cp -L /data/etc/mender/mender.conf.bak $(realpath /etc/mender/mender.conf)"
                        )
                    else:
                        connection.run("rm -f $(realpath /etc/mender/mender.conf)")
                    if sig_key:
                        connection.run(
                            "rm -f /etc/mender/%s" % os.path.basename(sig_key.public)
                        )
            finally:
                os.chdir(origdir)

    def manipulate_artifact_internals(self, sig_case, artifact):
        tar = subprocess.check_output(["tar", "tf", artifact])
        tar_list = tar.split()
        with make_tempdir() as tmpdir:
            shutil.copy(artifact, os.path.join(tmpdir, "image.mender"))
            cwd = os.open(".", os.O_RDONLY)
            os.chdir(tmpdir)
            try:
                tar = subprocess.check_output(["tar", "xf", "image.mender"])
                if not sig_case.signature_ok:
                    # Corrupt signature.
                    with open("manifest.sig", "r+") as fd:
                        Helpers.corrupt_middle_byte(fd)
                if not sig_case.checksum_ok:
                    os.chdir("data")
                    try:
                        data_list = subprocess.check_output(
                            ["tar", "tzf", "0000.tar.gz"]
                        )
                        data_list = data_list.split()
                        subprocess.check_call(["tar", "xzf", "0000.tar.gz"])
                        # Corrupt checksum by changing file slightly.
                        with open("image.dat", "r+") as fd:
                            Helpers.corrupt_middle_byte(fd)
                        # Pack it up again in same order.
                        os.remove("0000.tar.gz")
                        subprocess.check_call(["tar", "czf", "0000.tar.gz"] + data_list)
                        for data_file in data_list:
                            os.remove(data_file)
                    finally:
                        os.chdir("..")

                if not sig_case.header_checksum_ok:
                    data_list = subprocess.check_output(["tar", "tzf", "header.tar.gz"])
                    data_list = data_list.split()
                    subprocess.check_call(["tar", "xzf", "header.tar.gz"])
                    # Corrupt checksum by changing file slightly.
                    with open("headers/0000/files", "a") as fd:
                        # Some extra data to corrupt the header checksum,
                        # but still valid JSON.
                        fd.write(" ")
                    # Pack it up again in same order.
                    os.remove("header.tar.gz")
                    subprocess.check_call(["tar", "czf", "header.tar.gz"] + data_list)
                    for data_file in data_list:
                        os.remove(data_file)

                # Make sure we put it back in the same order.
                os.remove("image.mender")
                subprocess.check_call(["tar", "cf", "image.mender"] + tar_list)
            finally:
                os.fchdir(cwd)
                os.close(cwd)

            shutil.move(os.path.join(tmpdir, "image.mender"), artifact)

    @pytest.mark.only_with_mender_feature("mender-grub")
    @pytest.mark.min_mender_version("1.0.0")
    def test_redundant_grub_env(
        self, successful_image_update_mender, bitbake_variables, connection
    ):
        """This tests pretty much the same thing as the test_redundant_uboot_env
        above, but the details differ. U-Boot maintains a counter in each
        environment, and then only updates one of them. However, the GRUB
        variant we have implemented in the GRUB scripting language, where we
        cannot do this, so instead we update both, and use the validity of the
        variables instead as a crude checksum."""

        (active, passive) = determine_active_passive_part(bitbake_variables, connection)

        # Corrupt the passive partition.
        connection.run("dd if=/dev/zero of=%s bs=1024 count=1024" % passive)

        if (
            "mender-bios" in bitbake_variables.get("MENDER_FEATURES", "").split()
            or "mender-bios" in bitbake_variables.get("DISTRO_FEATURES", "").split()
        ):
            env_dir = "/boot/grub/grub-mender-grubenv"
        else:
            env_dir = "/boot/efi/grub-mender-grubenv"

        # Now try to corrupt the environment, and make sure it doesn't get booted into.
        for env_num in [1, 2]:
            # Make a copy of the two environments.
            connection.run(
                "cp %s/{mender_grubenv1/env,mender_grubenv1/env.backup}" % env_dir
            )
            connection.run(
                "cp %s/{mender_grubenv1/lock,mender_grubenv1/lock.backup}" % env_dir
            )
            connection.run(
                "cp %s/{mender_grubenv2/env,mender_grubenv2/env.backup}" % env_dir
            )
            connection.run(
                "cp %s/{mender_grubenv2/lock,mender_grubenv2/lock.backup}" % env_dir
            )

            try:
                env_file = "%s/mender_grubenv%d/env" % (env_dir, env_num)
                lock_file = "%s/mender_grubenv%d/lock" % (env_dir, env_num)
                connection.run('sed -e "s/editing=.*/editing=1/" %s' % lock_file)
                connection.run(
                    'sed -e "s/mender_boot_part=.*/mender_boot_part=%s/" %s'
                    % (passive[-1], lock_file)
                )

                reboot(connection)
                run_after_connect("true", connection)

                (new_active, new_passive) = determine_active_passive_part(
                    bitbake_variables, connection
                )
                assert new_active == active
                assert new_passive == passive

            finally:
                # Restore the two environments.
                connection.run(
                    "mv %s/{mender_grubenv1/env.backup,mender_grubenv1/env}" % env_dir
                )
                connection.run(
                    "mv %s/{mender_grubenv1/lock.backup,mender_grubenv1/lock}" % env_dir
                )
                connection.run(
                    "mv %s/{mender_grubenv2/env.backup,mender_grubenv2/env}" % env_dir
                )
                connection.run(
                    "mv %s/{mender_grubenv2/lock.backup,mender_grubenv2/lock}" % env_dir
                )

    @pytest.mark.only_with_mender_feature("mender-uboot")
    @pytest.mark.only_with_image("sdimg", "uefiimg")
    @pytest.mark.min_mender_version("4.0.0")
    @pytest.mark.min_yocto_version("dunfell")
    def test_uboot_mender_saveenv_canary(self, bitbake_variables, connection):
        """Tests that the mender_saveenv_canary works correctly, which tests
        that Mender will not proceed unless the U-Boot boot loader has saved the
        environment."""

        file_flag = Helpers.get_file_flag(bitbake_variables)
        image_type = bitbake_variables["MACHINE"]

        with tempfile.NamedTemporaryFile() as image_dat, tempfile.NamedTemporaryFile(
            suffix=".mender"
        ) as image_mender:
            # Make a dummy/broken update
            subprocess.check_call(
                f"dd if=/dev/zero of={image_dat.name} bs=1M count=0 seek=16", shell=True
            )
            subprocess.check_call(
                "mender-artifact write rootfs-image -t %s -n test-update %s %s -o %s"
                % (image_type, file_flag, image_dat.name, image_mender.name),
                shell=True,
            )
            put_no_sftp(image_mender.name, connection, remote="/var/tmp/image.mender")

            env_conf = connection.run("cat /etc/fw_env.config").stdout
            env_conf_lines = env_conf.rstrip("\n\r").split("\n")
            assert len(env_conf_lines) == 2
            for i in [0, 1]:
                entry = env_conf_lines[i].split()
                connection.run(
                    "dd if=%s skip=%d bs=%d count=1 iflag=skip_bytes > /data/old_env%d"
                    % (entry[0], int(entry[1], 0), int(entry[2], 0), i)
                )

            try:
                bootenv_print, bootenv_set = bootenv_tools(connection)

                # Try to manually remove the canary first.
                connection.run(f"{bootenv_set} mender_saveenv_canary")
                result = connection.run(
                    "mender-update install /var/tmp/image.mender", warn=True
                )
                assert (
                    result.return_code != 0
                ), "Update succeeded when canary was not present!"
                output = connection.run(
                    f"{bootenv_print} upgrade_available"
                ).stdout.rstrip("\n")
                # Upgrade should not have been triggered.
                assert output == "upgrade_available=0"

                # Then zero the environment, causing the libubootenv to fail
                # completely.
                for i in [0, 1]:
                    entry = env_conf_lines[i].split()
                    connection.run(
                        "dd if=/dev/zero of=%s seek=%d bs=%d count=1 oflag=seek_bytes"
                        % (entry[0], int(entry[1], 0), int(entry[2], 0))
                    )
                result = connection.run(
                    "mender-update install /var/tmp/image.mender", warn=True
                )
                assert (
                    result.return_code != 0
                ), "Update succeeded when canary was not present!"

            finally:
                # Restore environment to what it was.
                for i in [0, 1]:
                    entry = env_conf_lines[i].split()
                    connection.run(
                        "dd of=%s seek=%d bs=%d count=1 oflag=seek_bytes < /data/old_env%d"
                        % (entry[0], int(entry[1], 0), int(entry[2], 0), i)
                    )
                    connection.run("rm -f /data/old_env%d" % i)

    @pytest.mark.cross_platform
    @pytest.mark.min_mender_version("4.0.0")
    def test_standalone_update_rollback(self, bitbake_variables, connection):
        """Test that the rollback state on the active partition does roll back to the
        currently running active partition after a failed update.

        This is done through adding a failing state script
        'ArtifactInstall_Leave_01' to a rootfs-image update, and have it fail
        the update. This should trigger a rollback, while still on the active
        partition.

        """

        image_type = bitbake_variables["MACHINE"]

        tdir = tempfile.mkdtemp()
        script_name = os.path.join(tdir, "ArtifactInstall_Leave_01")

        with tempfile.NamedTemporaryFile() as image_dat, tempfile.NamedTemporaryFile(
            suffix=".mender"
        ) as image_mender:
            bootenv_print, _ = bootenv_tools(connection)

            original_partition = connection.run(
                f"{bootenv_print} mender_boot_part"
            ).stdout
            assert original_partition != ""

            with open(script_name, "w") as af:
                af.write("#!/bin/bash\nexit 1")
            res = subprocess.check_call(
                f"dd if=/dev/zero of={image_dat.name} bs=1M count=0 seek=16", shell=True
            )
            assert res == 0
            res = subprocess.check_call(
                "mender-artifact write rootfs-image -t %s -n test-update -f %s -o %s -s %s"
                % (image_type, image_dat.name, image_mender.name, script_name),
                shell=True,
            )
            assert res == 0

            put_no_sftp(image_mender.name, connection, remote="/var/tmp/image.mender")

            res = connection.run(
                "mender-update install /var/tmp/image.mender", warn=True
            )
            assert res.return_code != 0

            #
            # The rollback should not leave the device pending a partition
            # switch on boot
            #
            output = connection.run(f"{bootenv_print} upgrade_available").stdout
            assert output.rstrip("\n") == "upgrade_available=0"

            #
            # Make sure the device is still on the original partition
            #
            active = connection.run(f"{bootenv_print} mender_boot_part").stdout
            assert original_partition == active

    @pytest.mark.min_mender_version("4.1.0")
    def test_rollback_after_commit(
        self,
        successful_image_update_mender,
        bitbake_variables,
        connection,
        http_server,
        board_type,
        use_s3,
        s3_address,
    ):
        """Test that we can roll back even after an ArtifactCommit. See the update module protocol about
        that state to understand why this is important.

        """

        (active_before, passive_before) = determine_active_passive_part(
            bitbake_variables, connection
        )

        Helpers.install_update(
            successful_image_update_mender,
            connection,
            http_server,
            board_type,
            use_s3,
            s3_address,
        )

        bootenv_print, bootenv_set = bootenv_tools(connection)

        output = connection.run(f"{bootenv_print} upgrade_available").stdout
        assert output.rstrip("\n") == "upgrade_available=1"

        output = connection.run(f"{bootenv_print} mender_boot_part").stdout
        assert output.rstrip("\n") == "mender_boot_part=" + passive_before[-1:]

        output = connection.run(f"{bootenv_print} mender_boot_part_hex").stdout
        assert output.rstrip("\n") == "mender_boot_part_hex=" + passive_before[-1:]

        reboot(connection)
        run_after_connect("true", connection)

        connection.run("mender-update commit --stop-before ArtifactCommit_Leave")

        output = connection.run(f"{bootenv_print} upgrade_available").stdout
        assert output.rstrip("\n") == "upgrade_available=0"

        output = connection.run(f"{bootenv_print} mender_boot_part").stdout
        assert output.rstrip("\n") == "mender_boot_part=" + passive_before[-1:]

        output = connection.run(f"{bootenv_print} mender_boot_part_hex").stdout
        assert output.rstrip("\n") == "mender_boot_part_hex=" + passive_before[-1:]

        connection.run("mender-update rollback")

        output = connection.run(f"{bootenv_print} upgrade_available").stdout
        assert output.rstrip("\n") == "upgrade_available=0"

        output = connection.run(f"{bootenv_print} mender_boot_part").stdout
        assert output.rstrip("\n") == "mender_boot_part=" + active_before[-1:]

        output = connection.run(f"{bootenv_print} mender_boot_part_hex").stdout
        assert output.rstrip("\n") == "mender_boot_part_hex=" + active_before[-1:]

        # This is just a time saving measure when cleaning up. Since we rolled back, but didn't
        # reboot, the boot environment doesn't match the mounted root now. But the update we did is
        # perfectly valid, so instead of wasting time on another reboot, just switch to this rootfs.
        output = connection.run(
            f"{bootenv_set} mender_boot_part {passive_before[-1:]}"
        ).stdout
        output = connection.run(
            f"{bootenv_set} mender_boot_part_hex {passive_before[-1:]}"
        ).stdout

        output = connection.run(f"{bootenv_print} mender_boot_part").stdout
        assert output.rstrip("\n") == "mender_boot_part=" + passive_before[-1:]

        output = connection.run(f"{bootenv_print} mender_boot_part_hex").stdout
        assert output.rstrip("\n") == "mender_boot_part_hex=" + passive_before[-1:]

    @pytest.mark.cross_platform
    @pytest.mark.min_mender_version("4.0.0")
    def test_standalone_update_from_state_v1(self, bitbake_variables, connection):
        """Test a successful update from standalone state v1 (Mender Client 4.x) to standalone
        state v2 (Mender Client 5.x or newer).
        """
        # When using mender-convert, the mender-update daemon is running,
        # stop it allow us to modify the mdb database
        active_daemon = (
            0
            == connection.run(
                "systemctl is-active mender-updated", warn=True
            ).return_code
        )
        try:
            if active_daemon:
                connection.run("systemctl stop mender-updated")
            with tempfile.TemporaryDirectory() as tmpdir:

                # Write v1 standalone state data into a plain text file
                state_v1_path = Path(tmpdir, "standalone")
                with open(state_v1_path, "w", encoding="utf-8") as fd:
                    fd.write(
                        """standalone-state
    {"Version":1,"ArtifactName":"my-hacky-update","ArtifactGroup":"","PayloadTypes": ["single-file"],"ArtifactTypeInfoProvides": {"rootfs-image.single-file.version":"my-hacky-update"},"ArtifactClearsProvides": ["rootfs-image.single-file.*"]}
    """
                    )

                # Make sure the database is initialized by reading it with show-provides
                connection.run("mender-update show-provides")

                # Update the database on the device with the v1 standalone state data
                database_path = Path(tmpdir, "mender-store")
                get_no_sftp("/var/lib/mender/mender-store", connection, database_path)
                subprocess.check_call(
                    [
                        "/usr/bin/mdb_load",
                        "-T",
                        "-f",
                        state_v1_path,
                        "-n",
                        database_path,
                    ]
                )
                put_no_sftp(database_path, connection, "/var/lib/mender/mender-store")

                # Create the working directory for the "ongoing" update
                connection.run("mkdir -p /var/lib/mender/modules/v3/payloads/0000/tree")

                # The database indicates an ongoing update from v1. Commit it and check provides
                output = connection.run("mender-update -l trace commit")
                assert output.stdout.rstrip("\n") == "Committed."
                output = connection.run("mender-update show-provides")
                assert (
                    "rootfs-image.single-file.version=my-hacky-update" in output.stdout
                )
                assert "artifact_name=my-hacky-update" in output.stdout
        finally:
            if active_daemon:
                connection.run("systemctl start mender-updated")
