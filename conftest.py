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
import pytest
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "tests"))

from utils.fixtures import *


def pytest_collection_modifyitems(session, config, items):
    # This reordering is done for exclusive tests: Those that are not run in
    # parallel. We use flock to obtain shared and exclusive locks. Unfortunately
    # flock is dumb: If an exclusive lock is waiting to be obtained, it will
    # keep granting shared locks as long as at least one worker has one. To get
    # the exclusive lock, all workers would have to release their shared locks
    # at the same time, which will virtually never happen. This means that each
    # exclusive test would tie up one worker waiting for this, defeating the
    # purpose of parallelism.
    #
    # To deal with this, move all exclusive tests to the end of the queue, so
    # that when we finally get to them, we already know that no more shared
    # locks will be granted.
    i = 0
    end = len(items)
    while i < end:
        if items[i].get_closest_marker("exclusive"):
            items.append(items[i])
            del items[i]
            end -= 1
        else:
            i += 1
