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

import pytest


@pytest.mark.platform_test
@pytest.mark.software_test
@pytest.mark.only_with_image("uefiimg")
@pytest.mark.usefixtures("setup_board")
class TestSecureBoot:
    @pytest.mark.min_mender_version("1.0.0")
    def test_secure_boot_enabled(self, connection, conversion, bitbake_variables):
        if not conversion:
            pytest.skip("MEN-5253: Secure Boot not yet working for Yocto")

        output = connection.run("mokutil --sb-state").stdout.strip()

        if conversion:
            # For mender-convert, Secure Boot is disabled if `grub.d`
            # integration is turned off.
            if bitbake_variables["MENDER_GRUB_D_INTEGRATION"] == "n":
                assert "SecureBoot disabled" in output
                return

        assert output == "SecureBoot enabled"
