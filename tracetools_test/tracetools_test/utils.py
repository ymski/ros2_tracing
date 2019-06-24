# Copyright 2019 Robert Bosch GmbH
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Utils for tracetools_test that are not strictly test-related."""

import os
import shutil
import time
from typing import List
from typing import Tuple

from launch import LaunchDescription
from launch import LaunchService
from launch_ros import get_default_launch_description
import launch_ros.actions
from tracetools_read.utils import DictEvent
from tracetools_read.utils import get_event_name
from tracetools_trace.tools import lttng


def run_and_trace(
    base_path: str,
    session_name_prefix: str,
    ros_events: List[str],
    kernel_events: List[str],
    package_name: str,
    node_names: List[str],
) -> Tuple[int, str]:
    """
    Run a node while tracing.

    :param base_path: the base path where to put the trace directory
    :param session_name_prefix: the session name prefix for the trace directory
    :param ros_events: the list of ROS UST events to enable
    :param kernel_events: the list of kernel events to enable
    :param package_name: the name of the package to use
    :param node_names: the names of the nodes to execute
    :return: exit code, full generated path
    """
    session_name = f'{session_name_prefix}-{time.strftime("%Y%m%d%H%M%S")}'
    full_path = os.path.join(base_path, session_name)

    lttng.lttng_init(session_name, full_path, ros_events=ros_events, kernel_events=kernel_events)

    nodes = []
    for node_name in node_names:
        n = launch_ros.actions.Node(
            package=package_name,
            node_executable=node_name,
            output='screen')
        nodes.append(n)
    ld = LaunchDescription(nodes)
    ls = LaunchService()
    ls.include_launch_description(get_default_launch_description())
    ls.include_launch_description(ld)

    exit_code = ls.run()

    lttng.lttng_fini(session_name)

    return exit_code, full_path


def cleanup_trace(full_path: str) -> None:
    """
    Cleanup trace data.

    :param full_path: the full path to the main trace directory
    """
    shutil.rmtree(full_path)


def get_event_names(events: List[DictEvent]) -> List[str]:
    """
    Get a list of events names from a list of events.

    :param events: the events of the trace
    :return: the list of event names
    """
    return [get_event_name(e) for e in events]
