#!/usr/bin/python
# Copyright 2021 Northern.tech AS
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
import subprocess
import tempfile

import pytest

from utils.common import put_no_sftp


@pytest.mark.min_yocto_version("kirkstone")
@pytest.mark.usefixtures("setup_board", "bitbake_path")
class TestUpdateModules:
    @pytest.mark.min_mender_version("2.0.0")
    def test_directory_update_module(self, bitbake_variables, connection):
        """Test the directory update module, first with a failed update, then a
        successful one, and finally installing and rolling back another one"""

        file_tree = tempfile.mkdtemp()
        try:
            # First update
            files_and_content = ["file1", "file2"]
            for file_and_content in files_and_content:
                with open(os.path.join(file_tree, file_and_content), "w") as fd:
                    fd.write(file_and_content)

            artifact_file = os.path.join(file_tree, "update.mender")
            cmd = (
                "directory-artifact-gen -o %s -n update-directory -t %s -d /tmp/test_directory_update_module %s"
                % (artifact_file, bitbake_variables["MENDER_DEVICE_TYPE"], file_tree)
            )
            subprocess.check_call(cmd, shell=True)
            put_no_sftp(artifact_file, connection, remote="/var/tmp/update.mender")
            original = connection.run("mender --no-syslog show-artifact").stdout.strip()

            # Block the path with a file, the module should fail
            connection.run("touch /tmp/test_directory_update_module")
            result = connection.run("mender install /var/tmp/update.mender", warn=True)
            assert result.exited == 1
            output = connection.run("mender --no-syslog show-artifact").stdout.strip()
            assert output == original

            # Remove path block and reinstall
            connection.run("rm -f /tmp/test_directory_update_module")
            connection.run("mender install /var/tmp/update.mender")
            output = connection.run("mender --no-syslog show-artifact").stdout.strip()
            assert output == original

            # Check files
            for file_and_content in files_and_content:
                output = connection.run(
                    "cat /tmp/test_directory_update_module/%s" % file_and_content
                ).stdout.strip()
                assert output == file_and_content

            connection.run("mender commit")
            output = connection.run("mender --no-syslog show-artifact").stdout.strip()
            assert output == "update-directory"

            # New update
            files_and_content_new = ["file3", "file4"]
            for old_file in files_and_content:
                os.remove(os.path.join(file_tree, old_file))
            for file_and_content in files_and_content_new:
                with open(os.path.join(file_tree, file_and_content), "w") as fd:
                    fd.write(file_and_content)

            artifact_file = os.path.join(file_tree, "update.mender")
            cmd = (
                "directory-artifact-gen -o %s -n update-directory-new-files -t %s -d /tmp/test_directory_update_module %s"
                % (artifact_file, bitbake_variables["MENDER_DEVICE_TYPE"], file_tree)
            )
            subprocess.check_call(cmd, shell=True)
            put_no_sftp(artifact_file, connection, remote="/var/tmp/update.mender")

            # Install and check files
            connection.run("mender install /var/tmp/update.mender")
            output = connection.run(
                "ls /tmp/test_directory_update_module"
            ).stdout.strip()
            assert "file1" not in output
            assert "file2" not in output
            assert "file3" in output
            assert "file4" in output
            for file_and_content in files_and_content_new:
                output = connection.run(
                    "cat /tmp/test_directory_update_module/%s" % file_and_content
                ).stdout.strip()
                assert output == file_and_content

            # Rollback and check files
            connection.run("mender rollback")
            output = connection.run(
                "ls /tmp/test_directory_update_module"
            ).stdout.strip()
            assert "file1" in output
            assert "file2" in output
            assert "file3" not in output
            assert "file4" not in output
            for file_and_content in files_and_content:
                output = connection.run(
                    "cat /tmp/test_directory_update_module/%s" % file_and_content
                ).stdout.strip()
                assert output == file_and_content
            output = connection.run("mender --no-syslog show-artifact").stdout.strip()
            assert output == "update-directory"

        finally:
            shutil.rmtree(file_tree)

    @pytest.mark.min_mender_version("2.0.0")
    def test_single_file_update_module(self, bitbake_variables, connection):
        """Test the single-file update module, first with a successfull update,
        then installing and rolling back another one"""

        file_tree = tempfile.mkdtemp()
        try:
            update_file = os.path.join(file_tree, "my-file")
            artifact_file = os.path.join(file_tree, "update.mender")

            # Create and send first update
            with open(update_file, "w") as fd:
                fd.write("my-initial-content")
            os.chmod(update_file, 0o777)
            cmd = (
                "single-file-artifact-gen -o %s -n update-file-v1 -t %s -d /tmp/some/new/path %s"
                % (artifact_file, bitbake_variables["MENDER_DEVICE_TYPE"], update_file)
            )
            subprocess.check_call(cmd, shell=True)
            put_no_sftp(artifact_file, connection, remote="/var/tmp/update.mender")

            # Install the update
            original = connection.run("mender --no-syslog show-artifact").stdout.strip()
            result = connection.run("mender install /var/tmp/update.mender")
            output = connection.run("mender --no-syslog show-artifact").stdout.strip()
            assert output == original

            # Check file
            output = connection.run("cat /tmp/some/new/path/my-file").stdout.strip()
            assert output == "my-initial-content"
            output = connection.run(
                "stat -c %a /tmp/some/new/path/my-file"
            ).stdout.strip()
            assert output == "777"

            # Commit
            connection.run("mender commit")
            output = connection.run("mender --no-syslog show-artifact").stdout.strip()
            assert output == "update-file-v1"

            # Create and send a second update
            with open(update_file, "w") as fd:
                fd.write("my-new-content")
            os.chmod(update_file, 0o600)
            cmd = (
                "single-file-artifact-gen -o %s -n update-file-v2 -t %s -d /tmp/some/new/path %s"
                % (artifact_file, bitbake_variables["MENDER_DEVICE_TYPE"], update_file)
            )
            subprocess.check_call(cmd, shell=True)
            put_no_sftp(artifact_file, connection, remote="/var/tmp/update.mender")

            # Install the update
            original = connection.run("mender --no-syslog show-artifact").stdout.strip()
            result = connection.run("mender install /var/tmp/update.mender")
            output = connection.run("mender --no-syslog show-artifact").stdout.strip()
            assert output == "update-file-v1"

            # Check file
            output = connection.run("cat /tmp/some/new/path/my-file").stdout.strip()
            assert output == "my-new-content"
            output = connection.run(
                "stat -c %a /tmp/some/new/path/my-file"
            ).stdout.strip()
            assert output == "600"

            # Rollback
            connection.run("mender rollback")
            output = connection.run("mender --no-syslog show-artifact").stdout.strip()
            assert output == "update-file-v1"

            # Check file
            output = connection.run("cat /tmp/some/new/path/my-file").stdout.strip()
            assert output == "my-initial-content"
            output = connection.run(
                "stat -c %a /tmp/some/new/path/my-file"
            ).stdout.strip()
            assert output == "777"

        finally:
            shutil.rmtree(file_tree)
