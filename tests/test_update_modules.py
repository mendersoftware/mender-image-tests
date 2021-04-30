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


@pytest.mark.usefixtures("setup_board", "bitbake_path")
class TestUpdateModules:
    @pytest.mark.min_mender_version("2.0.0")
    def test_directory_update_module(self, bitbake_variables, connection):
        """Test the directory based update module, first with a failed update,
        then a successful one."""

        file_tree = tempfile.mkdtemp()
        try:
            files_and_content = ["file1", "file2"]
            for file_and_content in files_and_content:
                with open(os.path.join(file_tree, file_and_content), "w") as fd:
                    fd.write(file_and_content)

            artifact_file = os.path.join(file_tree, "update.mender")
            cmd = (
                "directory-artifact-gen -o %s -n update-directory -t %s -d /tmp/test_directory_update_module %s"
                % (artifact_file, bitbake_variables["MACHINE"], file_tree)
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

        finally:
            shutil.rmtree(file_tree)
