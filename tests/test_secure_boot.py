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

"""Secure Boot acceptance test for mender-convert images.

Boots a converted image under QEMU/OVMF and checks `mokutil --sb-state`. What it
does depends on the image's grub.d integration and on whether the host could
boot a Secure-Boot-capable firmware for that image. The full matrix (driven by
scripts/test/mender-convert-qemu and run-tests.sh over in mender-convert):

  Runs, asserts "SecureBoot enabled":
    grub.d images (they use the distro's signed shim) booted on real Secure Boot
    firmware -- i.e. every grub.d image on a legacy (2M OVMF) host, plus Debian
    grub.d on a modern (4M OVMF) host.

  Runs, asserts "SecureBoot disabled":
    no-grub.d images on a legacy host. Their unsigned grub boots with Secure
    Boot off, but the 2M firmware still reports a state to assert on.

  Skipped:
    * Yocto / non-conversion images (MEN-5253; unrelated to this test).
    * Any image booted with Secure Boot off on a modern host
      (QEMU_NO_SECURE_BOOT=1). The modern plain OVMF_CODE_4M.fd has Secure Boot
      compiled out and exposes no state for mokutil to read, so there is nothing
      to assert. This covers both the no-grub.d images and *all* Ubuntu images
      on modern hosts: Ubuntu's initramfs lacks the `ahci` driver, so it cannot
      boot the SMM/q35 .secboot firmware and must take the plain path.

See tests/uefi-nvram/README.md and the firmware-choice comment in
scripts/test/mender-convert-qemu (mender-convert) for the firmware details.
"""

import os
import platform

import pytest


def is_legacy_host():
    # The host running the tests: bookworm/jammy ship the 2M OVMF, which has
    # Secure Boot compiled in and always reports a state. Newer debianoids ship
    # only the 4M OVMF (see tests/uefi-nvram/README.md).
    codename = platform.freedesktop_os_release().get("VERSION_CODENAME")
    return codename in ("bookworm", "jammy")


@pytest.mark.only_with_image("uefiimg")
@pytest.mark.usefixtures("setup_board")
class TestSecureBoot:
    @pytest.mark.min_mender_version("1.0.0")
    def test_secure_boot_enabled(self, connection, conversion, bitbake_variables):
        if not conversion:
            pytest.skip("MEN-5253: Secure Boot not yet working for Yocto")

        # Modern host + Secure Boot off: the plain 4M OVMF reports no Secure Boot
        # state, so there is nothing to assert. See the module docstring.
        if not is_legacy_host() and os.environ.get("QEMU_NO_SECURE_BOOT") == "1":
            pytest.skip(
                "Secure Boot disabled on a modern host; the non-SB OVMF cannot "
                "report Secure Boot state"
            )

        output = connection.run("mokutil --sb-state").stdout.strip()

        # For mender-convert, Secure Boot is disabled if `grub.d` integration is
        # turned off.
        if bitbake_variables["MENDER_GRUB_D_INTEGRATION"] == "n":
            assert "SecureBoot disabled" in output
            return

        assert output == "SecureBoot enabled"
