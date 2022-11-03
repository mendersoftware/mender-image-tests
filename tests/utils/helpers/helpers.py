#!/usr/bin/python
# Copyright 2022 Northern.tech AS
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
import subprocess
import time
import requests
import redo

from ..common import get_no_sftp, version_is_minimum, get_worker_index


class Helpers:
    @staticmethod
    def upload_to_s3(artifact):
        subprocess.call(
            ["s3cmd", "--follow-symlinks", "put", artifact, "s3://mender/temp/"]
        )
        subprocess.call(
            ["s3cmd", "setacl", "s3://mender/temp/%s" % artifact, "--acl-public"]
        )

    @staticmethod
    def corrupt_middle_byte(fd):
        # Corrupt the middle byte in the contents.
        middle = int(os.fstat(fd.fileno()).st_size / 2)
        fd.seek(middle)
        middle_byte = int(fd.read(1).encode().hex(), base=16)
        fd.seek(middle)
        # Flip lowest bit.
        fd.write("%c" % (middle_byte ^ 0x1))

    @staticmethod
    def get_file_flag(bitbake_variables):
        if version_is_minimum(bitbake_variables, "mender-artifact", "3.0.0"):
            return "-f"
        else:
            return "-u"

    @staticmethod
    def install_update(image, conn, http_server, board_type, use_s3, s3_address):
        # We want `image` to be in the current directory because we use Python's
        # `http.server`. If it isn't, make a symlink, and relaunch.
        if os.path.dirname(os.path.abspath(image)) != os.getcwd():
            temp_artifact = "temp-artifact-%d.mender" % get_worker_index()
            os.symlink(image, temp_artifact)
            try:
                return Helpers.install_update(
                    temp_artifact, conn, http_server, board_type, use_s3, s3_address,
                )
            finally:
                os.unlink(temp_artifact)

        port = 8000 + get_worker_index()
        http_server_location = http_server

        http_server = None
        if "qemu" not in board_type or use_s3:
            Helpers.upload_to_s3(image)
            http_server_location = "{}/mender/temp".format(s3_address)
        else:
            http_server = subprocess.Popen(["python3", "-m", "http.server", str(port)])
            assert http_server

            def probe_http_server():
                assert (
                    requests.head("http://localhost:%d/%s" % (port, image)).status_code
                    == 200
                )

            redo.retry(
                probe_http_server,
                attempts=5,
                sleeptime=1,
                retry_exceptions=(requests.exceptions.ConnectionError),
            )

        try:
            output = conn.run(
                "mender install http://%s/%s" % (http_server_location, image)
            )
            print("output from rootfs update: ", output.stdout)
        finally:
            if http_server:
                try:
                    status_code = requests.head(
                        "http://localhost:%d/%s" % (port, image)
                    ).status_code
                    if status_code != 200:
                        print(
                            "warning: http server is not accessible, status code %d"
                            % (status_code)
                        )
                except Exception as e:
                    print("Exception during request" + str(e))
                http_server.terminate()
