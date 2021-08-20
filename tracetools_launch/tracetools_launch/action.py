# Copyright 2019 Robert Bosch GmbH
# Copyright 2021 Christophe Bedard
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

"""Module for the Trace action."""

import re
import shlex
from typing import Iterable
from typing import List
from typing import Optional
from typing import Text

from launch import logging
from launch.action import Action
from launch.event import Event
from launch.event_handlers import OnShutdown
from launch.frontend import Entity
from launch.frontend import expose_action
from launch.frontend import Parser
from launch.launch_context import LaunchContext
from launch.some_substitutions_type import SomeSubstitutionsType
from launch.substitutions import TextSubstitution
from launch.utilities import normalize_to_list_of_substitutions
from launch.utilities import perform_substitutions
from tracetools_trace.tools import lttng
from tracetools_trace.tools import names
from tracetools_trace.tools import path

from .actions.ld_preload import LdPreload


@expose_action('trace')
class Trace(Action):
    """
    Tracing action for launch.

    Sets up and enables tracing through a launch file description.
    """

    LIB_PROFILE_NORMAL = 'liblttng-ust-cyg-profile.so'
    LIB_PROFILE_FAST = 'liblttng-ust-cyg-profile-fast.so'
    LIB_MEMORY_UST = 'liblttng-ust-libc-wrapper.so'

    PROFILE_EVENT_PATTERN = '^lttng_ust_cyg_profile.*:func_.*'
    MEMORY_UST_EVENT_PATTERN = '^lttng_ust_libc:.*'

    def __init__(
        self,
        *,
        session_name: SomeSubstitutionsType,
        append_timestamp: bool = False,
        base_path: Optional[SomeSubstitutionsType] = None,
        events_ust: Iterable[SomeSubstitutionsType] = names.DEFAULT_EVENTS_ROS,
        events_kernel: Iterable[SomeSubstitutionsType] = names.DEFAULT_EVENTS_KERNEL,
        context_names: Iterable[SomeSubstitutionsType] = names.DEFAULT_CONTEXT,
        profile_fast: bool = True,
        **kwargs,
    ) -> None:
        """
        Create a Trace.

        Substitutions are supported for the session name,
        base path, and the lists of events and context names.

        For the lists of events, wildcards can be used, e.g., 'ros2:*' for
        all events from the 'ros2' tracepoint provider or '*' for all events.

        To disable a type of events (e.g., disable all kernel events) or disable all context
        fields, set the corresponding parameter to an empty list (for Python launch files) or to
        an empty string (through launch frontends).

        :param session_name: the name of the tracing session
        :param append_timestamp: whether to append timestamp to the session name
        :param base_path: the path to the base directory in which to create the session directory,
        or `None` for default
        :param events_ust: the list of ROS UST events to enable
        :param events_kernel: the list of kernel events to enable
        :param context_names: the list of context names to enable
        :param profile_fast: `True` to use fast profiling, `False` for normal (only if necessary)
        """
        super().__init__(**kwargs)
        self.__append_timestamp = append_timestamp
        self.__session_name = normalize_to_list_of_substitutions(session_name)
        self.__base_path = base_path \
            if base_path is None else normalize_to_list_of_substitutions(base_path)
        self.__events_ust = [normalize_to_list_of_substitutions(x) for x in events_ust]
        self.__events_kernel = [normalize_to_list_of_substitutions(x) for x in events_kernel]
        self.__context_names = [normalize_to_list_of_substitutions(x) for x in context_names]
        self.__profile_fast = profile_fast
        self.__logger = logging.get_logger(__name__)
        self.__ld_preload_actions: List[LdPreload] = []

    @classmethod
    def _parse_cmdline(
        cls,
        cmd: Text,
        parser: Parser
    ) -> List[SomeSubstitutionsType]:
        """
        Parse text apt for command line execution.

        :param: cmd a space (' ') delimited command line arguments list.
           All found `TextSubstitution` items are split and added to the
           list again as a `TextSubstitution`.
        :returns: a list of command line arguments.
        """
        result_args = []
        arg: List[SomeSubstitutionsType] = []

        def _append_arg():
            nonlocal arg
            result_args.append(arg)
            arg = []
        for sub in parser.parse_substitution(cmd):
            if isinstance(sub, TextSubstitution):
                tokens = shlex.split(sub.text)
                if not tokens:
                    # Sting with just spaces.
                    # Appending args allow splitting two substitutions
                    # separated by a space.
                    # e.g.: `$(subst1 asd) $(subst2 bsd)` will be two separate arguments.
                    _append_arg()
                    continue
                if sub.text[0].isspace():
                    # Needed for splitting from the previous argument
                    # e.g.: `$(find-exec bsd) asd`
                    # It splits `asd` from the path of `bsd` executable.
                    if len(arg) != 0:
                        _append_arg()
                arg.append(TextSubstitution(text=tokens[0]))
                if len(tokens) > 1:
                    # Needed to split the first argument when more than one token.
                    # e.g. `$(find-pkg-prefix csd)/asd bsd`
                    # will split `$(find-pkg-prefix csd)/asd` from `bsd`.
                    _append_arg()
                    arg.append(TextSubstitution(text=tokens[-1]))
                if len(tokens) > 2:
                    # If there are more than two tokens, just add all the middle tokens to
                    # `result_args`.
                    # e.g. `$(find-pkg-prefix csd)/asd bsd dsd xsd`
                    # 'bsd' 'dsd' will be added.
                    result_args.extend([TextSubstitution(text=x)] for x in tokens[1:-1])
                if sub.text[-1].isspace():
                    # Allows splitting from next argument.
                    # e.g. `exec $(find-some-file)`
                    # Will split `exec` argument from the result of `find-some-file` substitution.
                    _append_arg()
            else:
                arg.append(sub)
        if arg:
            result_args.append(arg)
        return result_args

    @classmethod
    def parse(cls, entity: Entity, parser: Parser):
        """Parse."""
        _, kwargs = super().parse(entity, parser)

        kwargs['session_name'] = entity.get_attr('session-name')
        append_timestamp = entity.get_attr(
            'append-timestamp', data_type=bool, optional=True, can_be_str=False)
        if append_timestamp is not None:
            kwargs['append_timestamp'] = append_timestamp
        base_path = entity.get_attr('base-path', optional=True)
        if base_path:
            kwargs['base_path'] = parser.parse_substitution(base_path)
        # Make sure to handle empty strings and replace with empty lists,
        # otherwise an empty string enables all events
        events_ust = entity.get_attr('events-ust', optional=True)
        if events_ust is not None:
            kwargs['events_ust'] = cls._parse_cmdline(events_ust, parser) \
                if events_ust else []
        events_kernel = entity.get_attr('events-kernel', optional=True)
        if events_kernel is not None:
            kwargs['events_kernel'] = cls._parse_cmdline(events_kernel, parser) \
                if events_kernel else []
        context_names = entity.get_attr('context-names', optional=True)
        if context_names is not None:
            kwargs['context_names'] = cls._parse_cmdline(context_names, parser) \
                if context_names else []
        profile_fast = entity.get_attr(
            'profile-fast', data_type=bool, optional=True, can_be_str=False)
        if profile_fast is not None:
            kwargs['profile_fast'] = profile_fast

        return cls, kwargs

    @staticmethod
    def any_events_match(
        name_pattern: str,
        events: List[str],
    ) -> bool:
        """
        Check if any event name in the list matches the given pattern.

        :param name_pattern: the pattern to use for event names
        :param events: the list of event names
        :return true if there is a match, false otherwise
        """
        return any(re.match(name_pattern, event_name) for event_name in events)

    @classmethod
    def has_profiling_events(
        cls,
        events_ust: List[str],
    ) -> bool:
        """Check if the UST events list contains at least one profiling event."""
        return cls.any_events_match(cls.PROFILE_EVENT_PATTERN, events_ust)

    @classmethod
    def has_ust_memory_events(
        cls,
        events_ust: List[str],
    ) -> bool:
        """Check if the UST events list contains at least one userspace memory event."""
        return cls.any_events_match(cls.MEMORY_UST_EVENT_PATTERN, events_ust)

    def __perform_substitutions(self, context: LaunchContext) -> None:
        self.__session_name = perform_substitutions(context, self.__session_name)
        if self.__append_timestamp:
            self.__session_name = path.append_timestamp(self.__session_name)
        self.__base_path = perform_substitutions(context, self.__base_path) \
            if self.__base_path else path.get_tracing_directory()
        self.__events_ust = [perform_substitutions(context, x) for x in self.__events_ust]
        self.__events_kernel = [perform_substitutions(context, x) for x in self.__events_kernel]
        self.__context_names = [perform_substitutions(context, x) for x in self.__context_names]

        # Add LD_PRELOAD actions if corresponding events are enabled
        if self.has_profiling_events(self.__events_ust):
            self.__ld_preload_actions.append(
                LdPreload(
                    self.LIB_PROFILE_FAST if self.__profile_fast else self.LIB_PROFILE_NORMAL)
            )
        if self.has_ust_memory_events(self.__events_ust):
            self.__ld_preload_actions.append(
                LdPreload(self.LIB_MEMORY_UST)
            )

    def execute(self, context: LaunchContext) -> Optional[List[Action]]:
        self.__perform_substitutions(context)
        # TODO make sure this is done as late as possible
        context.register_event_handler(OnShutdown(on_shutdown=self._destroy))
        # TODO make sure this is done as early as possible
        self._setup()
        return self.__ld_preload_actions

    def _setup(self) -> None:
        trace_directory = lttng.lttng_init(
            session_name=self.__session_name,
            base_path=self.__base_path,
            ros_events=self.__events_ust,
            kernel_events=self.__events_kernel,
            context_names=self.__context_names,
        )
        self.__logger.info(f'Writing tracing session to: {trace_directory}')
        self.__logger.debug(f'UST events: {self.__events_ust}')
        self.__logger.debug(f'Kernel events: {self.__events_kernel}')
        self.__logger.debug(f'Context names: {self.__context_names}')
        self.__logger.debug(f'LD_PRELOAD: {self.__ld_preload_actions}')

    def _destroy(self, event: Event, context: LaunchContext) -> None:
        self.__logger.debug(f'Finalizing tracing session: {self.__session_name}')
        lttng.lttng_fini(session_name=self.__session_name)

    def __repr__(self):
        return (
            'Trace('
            f'session_name={self.__session_name}, '
            f'base_path={self.__base_path}, '
            f'events_ust={self.__events_ust}, '
            f'events_kernel={self.__events_kernel}, '
            f'context_names={self.__context_names}, '
            f'profile_fast={self.__profile_fast}, '
            f'ld_preload_actions={self.__ld_preload_actions})'
        )
