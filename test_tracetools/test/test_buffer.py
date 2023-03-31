# Copyright 2019
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

import unittest

from tracetools_test.case import TraceTestCase
from tracetools_trace.tools import tracepoints as tp


VERSION_REGEX = r'^[0-9]+\.[0-9]+\.[0-9]+$'


class TestBuffer(TraceTestCase):

    def __init__(self, *args) -> None:
        super().__init__(
            *args,
            session_name_prefix='session-test-buffer-creation',
            events_ros=[
                tp.construct_ring_buffer,
                tp.ipb_to_subscription,
                tp.buffer_to_ipb
            ],
            package='test_tracetools',
            nodes=['test_intra'],
        )

    def test_all(self):
        # Check events as set
        self.assertEventsSet(self._events_ros)
        print('EVENTS: ', self._events)

        # Check fields
        construct_buffer_events = self.get_events_with_name(tp.construct_ring_buffer)
        for event in construct_buffer_events:
            self.assertValidPointer(event, 'buffer')
            self.assertFieldType(event, 'capacity', int)

        ipb_to_subscription_events = self.get_events_with_name(tp.ipb_to_subscription)
        for event in ipb_to_subscription_events:
            self.assertValidPointer(event, ['ipb', 'subscription'])

        buffer_to_ipb_events = self.get_events_with_name(tp.buffer_to_ipb)
        for event in buffer_to_ipb_events:
            self.assertValidPointer(event, ['buffer', 'ipb'])

        buffers = [self.get_field(e, 'buffer') for e in buffer_to_ipb_events]
        for event in construct_buffer_events:
            buffer = self.get_field(event, 'buffer')
            self.assertTrue(
                buffer in buffers,
                f'cannot find buffer_to_ipb event for buffer: {buffer} ({buffers})',
            )

        ipbs = [self.get_field(e, 'ipb') for e in ipb_to_subscription_events]
        for event in buffer_to_ipb_events:
            ipb = self.get_field(event, 'ipb')
            self.assertTrue(
                ipb in ipbs,
                f'cannot find ipb_to_subscription event for ipb: {ipb} ({ipbs})',
            )

        # Check the number of events
        self.assertTrue(
            len(construct_buffer_events) == len(buffer_to_ipb_events),
            f'The number of events does not match.\n \
                construct_buffer_events: {len(construct_buffer_events)}\n \
                buffer_to_ipb_events: {len(buffer_to_ipb_events)}'
        )

        # Check the number of events
        self.assertTrue(
            len(buffer_to_ipb_events) == len(ipb_to_subscription_events),
            f'The number of events does not match.\n \
                buffer_to_ipb_events: {len(buffer_to_ipb_events)}\n \
                ipb_to_subscription_events: {len(ipb_to_subscription_events)}'
        )

        # Check subscription init order
        for i in range(len(construct_buffer_events)):
            self.assertEventOrder([
                construct_buffer_events[i],
                buffer_to_ipb_events[i],
                ipb_to_subscription_events[i]
            ])

if __name__ == '__main__':
    unittest.main()
