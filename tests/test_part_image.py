#!/usr/bin/python
# Copyright 2017 Northern.tech AS
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

import hashlib
import json
import pytest
import subprocess
import os
import re
import tempfile

from common import *

def align_up(bytes, alignment):
    """Rounds bytes up to nearest alignment."""
    return int((int(bytes) + int(alignment) - 1) / int(alignment)) * int(alignment)


def extract_partition(img, number):
    output = subprocess.Popen(["fdisk", "-l", "-o", "device,start,end", img],
                              stdout=subprocess.PIPE)
    start = None
    end = None
    for line in output.stdout:
        if re.search("img%d" % number, line.decode()) is None:
            continue

        match = re.match(r"\s*\S+\s+(\S+)\s+(\S+)", line.decode())
        assert(match is not None)
        start = int(match.group(1))
        end = (int(match.group(2)) + 1)
    output.wait()

    assert start is not None
    assert end is not None
    subprocess.check_call(["dd", "if=" + img, "of=img%d.fs" % number,
                           "skip=%d" % start, "count=%d" % (end - start)])


def print_partition_table(disk_image):
    fdisk = subprocess.Popen(["fdisk", "-l", "-o", "start,end", disk_image], stdout=subprocess.PIPE)
    payload = False
    starts = []
    ends = []

    while True:
        line = fdisk.stdout.readline().decode()
        if not line:
            break

        line = line.strip()
        if payload:
            match = re.match(r"^\s*([0-9]+)\s+([0-9]+)\s*$", line)
            assert(match is not None)
            starts.append(int(match.group(1)) * 512)
            # +1 because end position is inclusive.
            ends.append((int(match.group(2)) + 1) * 512)
        elif re.match(".*start.*end.*", line, re.IGNORECASE) is not None:
            # fdisk precedes the output with lots of uninteresting stuff,
            # this gets us to the meat (/me wishes for a "machine output"
            # mode).
            payload = True

    fdisk.wait()
    return starts, ends

def get_data_part_number(disk_image):
    parts_start, parts_end = print_partition_table(disk_image)
    assert(len(parts_start) == len(parts_end))

    if len(parts_start) > 4:
        # For some QEMU x86_64 images extended partition is added as
        # the fourth one. Hence data partition can be found as the fifth one.
        return 5
    else:
        return 4


@pytest.mark.only_with_image('sdimg', 'uefiimg')
@pytest.mark.min_mender_version("1.0.0")
class TestMostPartitionImages:

    @staticmethod
    def verify_fstab(data):
        lines = data.split('\n')

        occurred = {}

        # No entry should occur twice.
        for line in lines:
            cols = line.split()
            if len(line) == 0 or line[0] == '#' or len(cols) < 2:
                continue
            assert occurred.get(cols[1]) is None, "%s appeared twice in fstab:\n%s" % (cols[1], data)
            occurred[cols[1]] = True

    def test_total_size(self, bitbake_variables, latest_part_image):
        """Test that the total size of the img is correct."""

        total_size_actual = os.stat(latest_part_image).st_size
        total_size_max_expected = int(bitbake_variables['MENDER_STORAGE_TOTAL_SIZE_MB']) * 1024 * 1024
        total_overhead = int(bitbake_variables['MENDER_PARTITIONING_OVERHEAD_KB']) * 1024

        assert(total_size_actual <= total_size_max_expected)
        assert(total_size_actual >= total_size_max_expected - total_overhead)

    def test_partition_alignment(self, bitbake_path, bitbake_variables, latest_part_image):
        """Test that partitions inside the img are aligned correctly, and
        correct sizes."""

        parts_start, parts_end = print_partition_table(latest_part_image)
        assert(len(parts_start) == len(parts_end))

        alignment = int(bitbake_variables['MENDER_PARTITION_ALIGNMENT'])
        total_size = int(bitbake_variables['MENDER_STORAGE_TOTAL_SIZE_MB']) * 1024 * 1024
        part_overhead = int(bitbake_variables['MENDER_PARTITIONING_OVERHEAD_KB']) * 1024
        boot_part_size = int(bitbake_variables['MENDER_BOOT_PART_SIZE_MB']) * 1024 * 1024
        data_part_size = int(bitbake_variables['MENDER_DATA_PART_SIZE_MB']) * 1024 * 1024

        if "mender-uboot" in bitbake_variables['DISTRO_FEATURES']:
            try:
                uboot_env_size = os.stat(os.path.join(bitbake_variables["DEPLOY_DIR_IMAGE"], "uboot.env")).st_size
            except OSError as e:
                uboot_env_size = alignment * 2

            # Uboot environment should be aligned.
            assert(uboot_env_size % alignment == 0)
        else:
            uboot_env_size = 0

        # First partition should start after exactly one alignment, plus the
        # U-Boot environment.
        assert(parts_start[0] == alignment + uboot_env_size)

        # Subsequent partitions should start where previous one left off.
        assert(parts_start[1] == parts_end[0])
        assert(parts_start[2] == parts_end[1])
        assert(parts_start[3] == parts_end[2])

        # For both data and swap partitions a default offset is added.
        # Offset value is equal to alignment.
        if len(parts_start) > 4:
            assert(parts_start[4] == parts_start[3] + alignment)

        if len(parts_start) > 5:
            assert(parts_start[5] == parts_end[4] + alignment)

        # Partitions should extend for their size rounded up to alignment.
        # No set size for Rootfs partitions, so cannot check them.
        # Boot partition.
        assert(parts_end[0] == parts_start[0] + align_up(boot_part_size, alignment))
        # Data partition.
        if len(parts_start) > 4:
            data_part_index = 4
        else:
            data_part_index = 3

        assert(parts_end[data_part_index] == parts_start[data_part_index] + align_up(data_part_size, alignment))

        # End of the last partition can be smaller than total image size, but
        # not by more than the calculated overhead.
        #
        # For some QEMU x86_64 images Extended partition is created as the fourth one.
        # As its size is a sum of data & swap partitions (plus additional offsets)
        # thus this partition's end is appropriate for comparison with the total size.
        assert(parts_end[3] <= total_size)
        assert(parts_end[3] >= total_size - part_overhead)


    def test_device_type(self, bitbake_path, bitbake_variables, latest_part_image):
        """Test that device type file is correctly embedded."""

        try:
            data_part_index = get_data_part_number(latest_part_image)

            extract_partition(latest_part_image, data_part_index)

            subprocess.check_call(["debugfs", "-R", "dump -p /mender/device_type device_type", 'img%d.fs' % (data_part_index,)])

            assert(os.stat("device_type").st_mode & 0o777 == 0o444)

            fd = open("device_type")

            lines = fd.readlines()
            assert(len(lines) == 1)
            lines[0] = lines[0].rstrip('\n\r')
            assert(lines[0] == "device_type=%s" % bitbake_variables["MENDER_DEVICE_TYPE"])

            fd.close()

        except:
            subprocess.call(["ls", "-l", "device_type"])
            print("Contents of artifact_info:")
            subprocess.call(["cat", "device_type"])
            raise

        finally:
            try:
                os.remove('img%d.fs' % (data_part_index,))
                os.remove("device_type")
            except:
                pass

    def test_data_ownership(self, bitbake_path, bitbake_variables, latest_part_image):
        """Test that the owner of files on the data partition is root."""

        try:
            data_part_index = get_data_part_number(latest_part_image)

            extract_partition(latest_part_image, data_part_index)

            def check_dir(dir):
                ls = subprocess.Popen(["debugfs", "-R" "ls -l -p %s" % dir, 'img%d.fs' % (data_part_index,)], stdout=subprocess.PIPE)

                while True:
                    entry = ls.stdout.readline().decode()
                    if not entry:
                        break

                    entry = entry.strip()

                    if len(entry) == 0:
                        # debugfs might output empty lines too.
                        continue

                    columns = entry.split('/')

                    if columns[1] == "0":
                        # Inode 0 is some weird file inside lost+found, skip it.
                        continue

                    assert(columns[3] == "0")
                    assert(columns[4] == "0")

                    mode = int(columns[2], 8)
                    # Recurse into directories.
                    if mode & 0o40000 != 0 and columns[5] != "." and columns[5] != "..":
                        check_dir(os.path.join(dir, columns[5]))

                ls.wait()

            check_dir("/")

        finally:
            try:
                os.remove('img%d.fs' % (data_part_index,))
            except:
                pass

    def test_fstab_correct(self, bitbake_path, bitbake_variables, latest_part_image):
        with make_tempdir() as tmpdir:
            old_cwd_fd = os.open(".", os.O_RDONLY)
            os.chdir(tmpdir)
            try:
                extract_partition(latest_part_image, 2)
                subprocess.check_call(["debugfs", "-R", "dump -p /etc/fstab fstab", "img2.fs"])
                with open("fstab") as fd:
                    data = fd.read()
                TestMostPartitionImages.verify_fstab(data)
            finally:
                os.fchdir(old_cwd_fd)
                os.close(old_cwd_fd)

    @pytest.mark.only_with_distro_feature('mender-grub')
    def test_mender_grubenv(self, bitbake_path, bitbake_variables, latest_part_image):
        with make_tempdir() as tmpdir:
            old_cwd_fd = os.open(".", os.O_RDONLY)
            os.chdir(tmpdir)
            try:
                extract_partition(latest_part_image, 1)
                for env_name in ["mender_grubenv1", "mender_grubenv2"]:
                    subprocess.check_call(["mcopy", "-i", "img1.fs", "::/EFI/BOOT/%s/env" % env_name, "."])
                    with open("env") as fd:
                        data = fd.read()
                    os.unlink("env")
                    assert "mender_boot_part=%s" % bitbake_variables['MENDER_ROOTFS_PART_A'][-1] in data
                    assert "upgrade_available=0" in data
                    assert "bootcount=0" in data
            finally:
                os.fchdir(old_cwd_fd)
                os.close(old_cwd_fd)

    @pytest.mark.min_yocto_version("warrior")
    def test_split_mender_conf(self, bitbake_path, bitbake_variables, latest_part_image):
        with make_tempdir() as tmpdir:
            old_cwd_fd = os.open(".", os.O_RDONLY)
            os.chdir(tmpdir)
            try:
                data_part_number = get_data_part_number(latest_part_image)

                extract_partition(latest_part_image, data_part_number)

                subprocess.check_call(["debugfs", "-R", "dump -p /mender/mender.conf mender.conf",
                                       "img%d.fs" % data_part_number])
                with open("mender.conf") as fd:
                    content = json.load(fd)
                    assert "RootfsPartA" in content
                    assert "RootfsPartB" in content
                    assert len(content) == 2
            finally:
                os.fchdir(old_cwd_fd)
                os.close(old_cwd_fd)

@pytest.mark.only_with_image('sdimg', 'uefiimg', 'biosimg', 'gptimg')
@pytest.mark.min_mender_version("1.0.0")
class TestAllPartitionImages:

    @pytest.mark.min_yocto_version("warrior")
    @pytest.mark.conversion
    def test_equal_checksum_part_image_and_artifact(self, bitbake_variables, latest_part_image, latest_mender_image):
        bufsize = 1048576 # 1MiB
        if b".xz" in subprocess.check_output(["tar", "tf", latest_mender_image]):
            zext = "xz"
            ztar = "J"
        else:
            zext = "gz"
            ztar = "z"

        with tempfile.NamedTemporaryFile() as tmp_artifact:
            subprocess.check_call("tar xOf %s data/0000.tar.%s | tar x%sO > %s"
                                  % (latest_mender_image, zext, ztar, tmp_artifact.name),
                                  shell=True)
            size = os.stat(tmp_artifact.name).st_size
            hash = hashlib.md5()
            while True:
                buf = tmp_artifact.read(bufsize)
                if len(buf) == 0:
                    break
                hash.update(buf)
            artifact_hash = hash.hexdigest()
            artifact_ls = subprocess.check_output(["ls", "-l", tmp_artifact.name]).decode()

        extract_partition(latest_part_image, 2)
        try:
            bytes_read = 0
            hash = hashlib.md5()
            with open("img2.fs", 'rb') as fd:
                while bytes_read < size:
                    buf = fd.read(min(size - bytes_read, bufsize))
                    if len(buf) == 0:
                        break
                    bytes_read += len(buf)
                    hash.update(buf)
                part_image_hash = hash.hexdigest()
            img_ls = subprocess.check_output(["ls", "-l", "img2.fs"]).decode()
        finally:
            os.remove("img2.fs")

        assert artifact_hash == part_image_hash, "Artifact:\n%s\nImage:\n%s" % (artifact_ls, img_ls)
