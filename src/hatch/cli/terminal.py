from __future__ import annotations

import os
from abc import ABC, abstractmethod
from functools import cached_property
from textwrap import indent as indent_text
from typing import Callable

import click
from rich.console import Console
from rich.errors import StyleSyntaxError
from rich.status import Status
from rich.style import Style
from rich.text import Text


class TerminalStatus(ABC):
    @abstractmethod
    def stop(self) -> None:
        ...

    def __enter__(self) -> TerminalStatus:
        return self

    @abstractmethod
    def __exit__(self, exc_type, exc_val, exc_tb):
        ...


class NullStatus(TerminalStatus):
    def stop(self):
        pass

    def __exit__(self, exc_type, exc_value, traceback):
        pass


class BorrowedStatus(TerminalStatus):
    def __init__(
        self,
        console: Console,
        *,
        tty: bool,
        verbosity: int,
        spinner_style: str,
        waiting_style: Style,
        success_style: Style,
        initializer: Callable,
        finalizer: Callable,
    ):
        self.__console = console
        self.__tty = tty
        self.__verbosity = verbosity
        self.__spinner_style = spinner_style
        self.__waiting_style = waiting_style
        self.__success_style = success_style
        self.__initializer = initializer
        self.__finalizer = finalizer

        # This is the possibly active current status
        self.__status: Status | None = None

        # This is used as a stack to display the current message
        self.__messages: list[tuple[Text, str]] = []

    def stop(self) -> None:
        active = self.__active()
        if self.__status is not None:
            self.__status.stop()

        old_message, final_text = self.__messages[-1]
        if self.__verbosity > 0 and active:
            if not final_text:
                final_text = old_message.plain
                final_text = f'Finished {final_text[:1].lower()}{final_text[1:]}'

            self.__output(Text(final_text, style=self.__success_style))

    def __call__(self, message: str, final_text: str = '') -> BorrowedStatus:
        self.__messages.append((Text(message, style=self.__waiting_style), final_text))
        return self

    def __enter__(self) -> BorrowedStatus:
        if not self.__messages:
            return self

        message, _ = self.__messages[-1]
        if not self.__tty:
            self.__output(message)
            return self

        if self.__status is None:
            self.__initializer()
        else:
            self.__status.stop()

        self.__status = self.__console.status(message, spinner=self.__spinner_style)
        self.__status.start()

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        old_message, final_text = self.__messages.pop()
        if self.__verbosity > 0 and self.__active():
            if not final_text:
                final_text = old_message.plain
                final_text = f'Finished {final_text[:1].lower()}{final_text[1:]}'

            self.__output(Text(final_text, style=self.__success_style))

        if not self.__tty:
            return

        self.__status.stop()
        if not self.__messages:
            self.__status = None
            self.__finalizer()
        else:
            message, _ = self.__messages[-1]
            self.__status = self.__console.status(message, spinner=self.__spinner_style)
            self.__status.start()

    def __active(self) -> bool:
        return self.__status is not None and self.__status._live.is_started

    def __output(self, text):
        self.__console.stderr = True
        try:
            self.__console.print(text, overflow='ignore', no_wrap=True, crop=False)
        finally:
            self.__console.stderr = False


class Terminal:
    def __init__(self, verbosity, enable_color, interactive):
        self.verbosity = verbosity
        self.interactive = interactive
        self.console = Console(
            force_terminal=enable_color,
            no_color=enable_color is False,
            markup=False,
            emoji=False,
            highlight=False,
            # Force consistent output for test assertions
            legacy_windows=False if 'HATCH_SELF_TESTING' in os.environ else None,
        )

        # Set defaults so we can pretty print before loading user config
        self._style_level_success: Style | str = 'bold cyan'
        self._style_level_error: Style | str = 'bold red'
        self._style_level_warning: Style | str = 'bold yellow'
        self._style_level_waiting: Style | str = 'bold magenta'
        # Default is simply bold rather than bold white for shells that have been configured with a white background
        self._style_level_info: Style | str = 'bold'
        self._style_level_debug: Style | str = 'bold'

        # Chosen as the default since it's compatible everywhere and looks nice
        self._style_spinner = 'simpleDotsScrolling'

    def initialize_styles(self, styles: dict):  # no cov
        # Lazily display errors so that they use the correct style
        errors = []

        for option, style in styles.items():
            attribute = f'_style_level_{option}'

            default_level = getattr(self, attribute, None)
            if default_level:
                try:
                    style = Style.parse(style)
                except StyleSyntaxError as e:  # no cov
                    errors.append(f'Invalid style definition for `{option}`, defaulting to `{default_level}`: {e}')
                    style = Style.parse(default_level)
            else:
                attribute = f'_style_{option}'

            setattr(self, attribute, style)

        return errors

    def display(self, text='', **kwargs):
        self.console.print(text, style=self._style_level_info, overflow='ignore', no_wrap=True, crop=False, **kwargs)

    def display_critical(self, text='', **kwargs):
        self.console.stderr = True
        try:
            self.console.print(
                text, style=self._style_level_error, overflow='ignore', no_wrap=True, crop=False, **kwargs
            )
        finally:
            self.console.stderr = False

    def display_error(self, text='', *, stderr=True, indent=None, link=None, **kwargs):
        if self.verbosity < -2:  # noqa: PLR2004
            return

        self.output(text, self._style_level_error, stderr=stderr, indent=indent, link=link, **kwargs)

    def display_warning(self, text='', *, stderr=True, indent=None, link=None, **kwargs):
        if self.verbosity < -1:
            return

        self.output(text, self._style_level_warning, stderr=stderr, indent=indent, link=link, **kwargs)

    def display_info(self, text='', *, stderr=True, indent=None, link=None, **kwargs):
        if self.verbosity < 0:
            return

        self.output(text, self._style_level_info, stderr=stderr, indent=indent, link=link, **kwargs)

    def display_success(self, text='', *, stderr=True, indent=None, link=None, **kwargs):
        if self.verbosity < 0:
            return

        self.output(text, self._style_level_success, stderr=stderr, indent=indent, link=link, **kwargs)

    def display_waiting(self, text='', *, stderr=True, indent=None, link=None, **kwargs):
        if self.verbosity < 0:
            return

        self.output(text, self._style_level_waiting, stderr=stderr, indent=indent, link=link, **kwargs)

    def display_debug(self, text='', level=1, *, stderr=True, indent=None, link=None, **kwargs):
        if not 1 <= level <= 3:  # noqa: PLR2004
            error_message = 'Debug output can only have verbosity levels between 1 and 3 (inclusive)'
            raise ValueError(error_message)
        elif self.verbosity < level:
            return

        self.output(text, self._style_level_debug, stderr=stderr, indent=indent, link=link, **kwargs)

    def display_mini_header(self, text, *, stderr=False, indent=None, link=None):
        if self.verbosity < 0:
            return

        self.display_info('[', stderr=stderr, indent=indent, end='')
        self.display_success(text, stderr=stderr, link=link, end='')
        self.display_info(']', stderr=stderr)

    def display_header(self, title='', *, stderr=False):
        self.console.rule(Text(title, self._style_level_success))

    def display_markdown(self, text, **kwargs):  # no cov
        from rich.markdown import Markdown

        self.display_raw(Markdown(text), **kwargs)

    def display_table(self, title, columns, *, show_lines=False, column_options=None, force_ascii=False, num_rows=0):
        from rich.table import Table

        if column_options is None:
            column_options = {}

        table_options = {}
        if force_ascii:
            from rich.box import ASCII_DOUBLE_HEAD

            table_options['box'] = ASCII_DOUBLE_HEAD
            table_options['safe_box'] = True

        table = Table(title=title, show_lines=show_lines, title_style='', **table_options)
        columns = dict(columns)

        for title, indices in list(columns.items()):
            if indices:
                table.add_column(title, style='bold', **column_options.get(title, {}))
            else:
                columns.pop(title)

        if not columns:
            return

        for i in range(num_rows or max(map(max, columns.values())) + 1):
            row = []
            for indices in columns.values():
                row.append(indices.get(i, ''))

            if any(row):
                table.add_row(*row)

        self.output(table)

    @cached_property
    def status(self) -> BorrowedStatus:
        return BorrowedStatus(
            self.console,
            tty=self.interactive and self.console.is_terminal,
            verbosity=self.verbosity,
            spinner_style=self._style_spinner,
            waiting_style=self._style_level_waiting,
            success_style=self._style_level_success,
            initializer=lambda: setattr(self.platform, 'displaying_status', True),  # type: ignore[attr-defined]
            finalizer=lambda: setattr(self.platform, 'displaying_status', False),  # type: ignore[attr-defined]
        )

    def status_if(self, *args, condition: bool, **kwargs) -> TerminalStatus:
        return self.status(*args, **kwargs) if condition else NullStatus()

    def output(self, text='', style=None, *, stderr=False, indent=None, link=None, **kwargs):
        kwargs.setdefault('overflow', 'ignore')
        kwargs.setdefault('no_wrap', True)
        kwargs.setdefault('crop', False)

        if indent:
            text = indent_text(text, indent)

        if link:
            style = style.update_link(self.platform.format_file_uri(link))

        if not stderr:
            self.console.print(text, style=style, **kwargs)
        else:
            self.console.stderr = True
            try:
                self.console.print(text, style=style, **kwargs)
            finally:
                self.console.stderr = False

    def display_raw(self, text, **kwargs):
        # No styling
        self.console.print(text, overflow='ignore', no_wrap=True, crop=False, **kwargs)

    @staticmethod
    def prompt(text, **kwargs):
        return click.prompt(text, **kwargs)

    @staticmethod
    def confirm(text, **kwargs):
        return click.confirm(text, **kwargs)
