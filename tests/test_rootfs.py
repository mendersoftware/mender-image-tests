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

import subprocess
import os
import json

import pytest

from utils.common import (
    make_tempdir,
    version_is_minimum,
    is_cpp_client,
)


class TestRootfs:
    @staticmethod
    def verify_fstab(data):
        lines = data.split("\n")

        occurred = {}

        # No entry should occur twice.
        for line in lines:
            cols = line.split()
            if len(line) == 0 or line[0] == "#" or len(cols) < 2:
                continue
            assert occurred.get(cols[1]) is None, "%s appeared twice in fstab:\n%s" % (
                cols[1],
                data,
            )
            occurred[cols[1]] = True

    @staticmethod
    def verify_file_exists(tmpdir, rootfs, path, filename, expect_to_exist=True):
        output = subprocess.check_output(
            ["debugfs", "-R", f"ls -l -p {path}", rootfs], cwd=tmpdir
        ).decode()
        file_found = any(
            [
                line.split("/")[5] == filename
                for line in output.split("\n")
                if len(line) > 0
            ]
        )
        assert (expect_to_exist and file_found) or (
            not expect_to_exist and not file_found
        )

    @staticmethod
    def verify_file_executable(tmpdir, rootfs, path, filename):
        subprocess.check_call(
            ["debugfs", "-R", f"dump -p {path}/{filename} {filename}", rootfs,],
            cwd=tmpdir,
        )
        assert os.access(os.path.join(tmpdir, filename), os.X_OK)

    @pytest.mark.cross_platform
    @pytest.mark.only_with_mender_feature("mender-update-install")
    @pytest.mark.only_with_image("ext4", "ext3", "ext2")
    @pytest.mark.min_mender_version("2.5.0")
    def test_expected_files_ext234(
        self,
        bitbake_path,
        bitbake_variables,
        mender_auth_binary,
        mender_update_binary,
        latest_rootfs,
    ):
        """Test fstab contents and mender client expected files"""

        with make_tempdir() as tmpdir:
            subprocess.check_call(
                ["debugfs", "-R", "dump -p /etc/fstab fstab", latest_rootfs],
                cwd=tmpdir,
            )
            with open(os.path.join(tmpdir, "fstab")) as fd:
                data = fd.read()
            TestRootfs.verify_fstab(data)

            output = subprocess.check_output(
                ["debugfs", "-R", "ls -l -p /data", latest_rootfs], cwd=tmpdir
            ).decode()
            for line in output.split("\n"):
                splitted = line.split("/")
                if len(splitted) <= 1:
                    continue
                # Should only contain "." and "..". In addition, debugfs
                # sometimes, but not always, returns a strange 0 entry, with
                # no name, but a "0" in the sixth column. It is not present
                # when mounting the filesystem.
                assert splitted[5] == "." or splitted[5] == ".." or splitted[6] == "0"

            # Check whether mender exists in /usr/bin
            self.verify_file_exists(
                tmpdir, latest_rootfs, "/usr/bin", mender_auth_binary, True
            )
            self.verify_file_exists(
                tmpdir, latest_rootfs, "/usr/bin", mender_update_binary, True
            )

            # Check whether mender exists in /var/lib
            self.verify_file_exists(
                tmpdir, latest_rootfs, "/var/lib", "mender", True,
            )

            # Check contents of /var/lib/mender
            output = subprocess.check_output(
                ["debugfs", "-R", "stat /var/lib/mender", latest_rootfs], cwd=tmpdir,
            ).decode()
            assert "Type: symlink" in output
            assert 'Fast link dest: "/data/mender"' in output

            # Check whether D-Bus policy files exist
            self.verify_file_exists(
                tmpdir,
                latest_rootfs,
                "/usr/share/dbus-1/system.d",
                "io.mender.AuthenticationManager.conf",
                True,
            )
            if not is_cpp_client(bitbake_variables):
                self.verify_file_exists(
                    tmpdir,
                    latest_rootfs,
                    "/usr/share/dbus-1/system.d",
                    "io.mender.UpdateManager.conf",
                    True,
                )

            self.verify_file_exists(
                tmpdir,
                latest_rootfs,
                "/etc/mender",
                "artifact_info",
                expect_to_exist=not version_is_minimum(
                    bitbake_variables, "mender-client", "3.5.0"
                ),
            )

            if is_cpp_client(bitbake_variables):
                # Check whether mender-flash exists in /usr/bin
                self.verify_file_exists(
                    tmpdir, latest_rootfs, "/usr/bin", "mender-flash", True
                )

    @pytest.mark.cross_platform
    @pytest.mark.only_with_image("ext4", "ext3", "ext2")
    @pytest.mark.min_mender_version("2.5.1")
    def test_expected_files_ext234_mender_connect(
        self, conversion, bitbake_path, bitbake_variables, latest_rootfs
    ):
        """Test mender-connect expected files"""

        # Expect to be installed for Yocto and not for mender-convert
        expect_installed = not conversion

        with make_tempdir() as tmpdir:
            # Check whether mender-connect exists in /usr/bin
            self.verify_file_exists(
                tmpdir, latest_rootfs, "/usr/bin", "mender-connect", expect_installed
            )

            # Check whether mender-connect.conf exists in /etc/mender
            self.verify_file_exists(
                tmpdir,
                latest_rootfs,
                "/etc/mender",
                "mender-connect.conf",
                expect_installed,
            )

            # Check mender-connect.conf contents
            if expect_installed:
                subprocess.check_call(
                    [
                        "debugfs",
                        "-R",
                        "dump -p /etc/mender/mender-connect.conf mender-connect.conf",
                        latest_rootfs,
                    ],
                    cwd=tmpdir,
                )
                with open(os.path.join(tmpdir, "mender-connect.conf")) as fd:
                    mender_connect_vars = json.load(fd)
                assert len(mender_connect_vars) == 2, mender_connect_vars
                assert "ShellCommand" in mender_connect_vars, mender_connect_vars
                assert "User" in mender_connect_vars, mender_connect_vars

    @pytest.mark.cross_platform
    @pytest.mark.only_with_image("ext4", "ext3", "ext2")
    @pytest.mark.min_mender_version("2.6.0")
    def test_expected_files_ext234_mender_configure(
        self, conversion, bitbake_path, bitbake_variables, latest_rootfs
    ):
        """Test mender-configure expected files"""

        # Expect to be installed for Yocto and not for mender-convert
        expect_installed = not conversion

        with make_tempdir() as tmpdir:

            # Check whether mender-configure exists in /usr/share/mender/modules/v3
            self.verify_file_exists(
                tmpdir,
                latest_rootfs,
                "/usr/share/mender/modules/v3",
                "mender-configure",
                expect_installed,
            )

            # Check whether mender-configure is executable
            if expect_installed:
                self.verify_file_executable(
                    tmpdir,
                    latest_rootfs,
                    "/usr/share/mender/modules/v3",
                    "mender-configure",
                )

            # Check whether mender-inventory-mender-configure exists in /usr/share/mender/inventory
            self.verify_file_exists(
                tmpdir,
                latest_rootfs,
                "/usr/share/mender/inventory",
                "mender-inventory-mender-configure",
                expect_installed,
            )

            # Check whether mender-inventory-mender-configure is executable
            if expect_installed:
                self.verify_file_executable(
                    tmpdir,
                    latest_rootfs,
                    "/usr/share/mender/inventory",
                    "mender-inventory-mender-configure",
                )

            # Independently from mender-configure's installation status, we create the
            # symlink for /var/lib/mender-configure to the data partition for mender-convert
            # to support the subsequent installation of the add-on by the user

            # Check whether mender-configure exists in /var/lib
            self.verify_file_exists(
                tmpdir, latest_rootfs, "/var/lib", "mender-configure",
            )

            # Check contents of /var/lib/mender-configure
            output = subprocess.check_output(
                ["debugfs", "-R", "stat /var/lib/mender-configure", latest_rootfs],
                cwd=tmpdir,
            ).decode()
            assert "Type: symlink" in output
            assert 'Fast link dest: "/data/mender-configure"' in output

    @pytest.mark.cross_platform
    @pytest.mark.only_with_image("ext4", "ext3", "ext2")
    @pytest.mark.min_mender_version("3.1.0")
    def test_expected_files_ext234_mender_monitor(
        self, conversion, bitbake_path, bitbake_variables, latest_rootfs
    ):
        """Test mender-monitor expected files (only state folder)"""

        if not conversion:
            pytest.skip("Test only applicable for mender-convert images.")

        with make_tempdir() as tmpdir:
            # Check whether mender-monitor exists in /var/lib
            self.verify_file_exists(
                tmpdir, latest_rootfs, "/var/lib", "mender-monitor",
            )

            # Check contents of /var/lib/mender-monitor
            output = subprocess.check_output(
                ["debugfs", "-R", "stat /var/lib/mender-monitor", latest_rootfs],
                cwd=tmpdir,
            ).decode()
            assert "Type: symlink" in output
            assert 'Fast link dest: "/data/mender-monitor"' in output

    @pytest.mark.only_with_image("ubifs")
    @pytest.mark.min_mender_version("1.2.0")
    def test_expected_files_ubifs(self, bitbake_path, bitbake_variables, latest_ubifs):
        """Test fstab contents on UBI File System."""

        with make_tempdir() as tmpdir:
            # NOTE: ubireader_extract_files can keep permissions only if
            # running as root, which we won't do
            subprocess.check_call(
                "ubireader_extract_files -o {outdir} {ubifs}".format(
                    outdir=tmpdir, ubifs=latest_ubifs
                ),
                shell=True,
            )

            path = os.path.join(tmpdir, "etc/fstab")
            with open(path) as fd:
                data = fd.read()
            TestRootfs.verify_fstab(data)

    @pytest.mark.only_with_mender_feature("mender-convert")
    @pytest.mark.min_mender_version("1.0.0")
    def test_unconfigured_image(self, latest_rootfs):
        """Test that images from mender-convert are unconfigured. We want
        `mender setup` to be the configuration mechanism there."""
        output = subprocess.check_output(
            ["debugfs", "-R", "ls -l -p /etc/mender", latest_rootfs]
        ).decode()
        assert "mender.conf" not in output
