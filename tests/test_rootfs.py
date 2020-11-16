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

import subprocess
import os

import pytest

from common import make_tempdir


class TestRootfs:
    @staticmethod
    def verify_artifact_info_data(data, artifact_name):
        lines = data.split()
        assert len(lines) == 1
        line = lines[0]
        line = line.rstrip("\n\r")
        var = line.split("=", 2)
        assert len(var) == 2

        var = [entry.strip() for entry in var]

        assert var[0] == "artifact_name"
        assert var[1] == artifact_name

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

    @pytest.mark.only_with_image("ext4", "ext3", "ext2")
    @pytest.mark.min_mender_version("1.0.0")
    def test_expected_files_ext234(
        self, bitbake_path, bitbake_variables, latest_rootfs
    ):
        """Test that artifact_info file is correctly embedded."""

        with make_tempdir() as tmpdir:
            try:
                subprocess.check_call(
                    [
                        "debugfs",
                        "-R",
                        "dump -p /etc/mender/artifact_info artifact_info",
                        latest_rootfs,
                    ],
                    cwd=tmpdir,
                )
                with open(os.path.join(tmpdir, "artifact_info")) as fd:
                    data = fd.read()
                TestRootfs.verify_artifact_info_data(
                    data, bitbake_variables["MENDER_ARTIFACT_NAME"]
                )
                assert (
                    os.stat(os.path.join(tmpdir, "artifact_info")).st_mode & 0o777
                    == 0o644
                )

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
                    assert (
                        splitted[5] == "." or splitted[5] == ".." or splitted[6] == "0"
                    )

                # Check whether mender exists in /usr/bin
                output = subprocess.check_output(
                    ["debugfs", "-R", "ls -l -p /usr/bin", latest_rootfs], cwd=tmpdir
                ).decode()
                assert any(
                    [
                        line.split("/")[5] == "mender"
                        for line in output.split("\n")
                        if len(line) > 0
                    ]
                )

            except:
                subprocess.call(["ls", "-l", "artifact_info"])
                print("Contents of artifact_info:")
                subprocess.call(["cat", "artifact_info"])
                raise

    @pytest.mark.only_with_image("ubifs")
    @pytest.mark.min_mender_version("1.2.0")
    def test_expected_files_ubifs(self, bitbake_path, bitbake_variables, latest_ubifs):
        """Test that artifact_info file is correctly embedded."""

        with make_tempdir() as tmpdir:
            # NOTE: ubireader_extract_files can keep permissions only if
            # running as root, which we won't do
            subprocess.check_call(
                "ubireader_extract_files -o {outdir} {ubifs}".format(
                    outdir=tmpdir, ubifs=latest_ubifs
                ),
                shell=True,
            )

            path = os.path.join(tmpdir, "etc/mender/artifact_info")
            with open(path) as fd:
                data = fd.read()
            TestRootfs.verify_artifact_info_data(
                data, bitbake_variables["MENDER_ARTIFACT_NAME"]
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
