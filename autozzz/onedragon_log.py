"""Carry an SDK console record's original overlay binding through stderr."""
import logging
import re
import sys


OVERLAY_LOG_ENV = "AUTOZZZ_OVERLAY_LOG_BINDING"
_PREFIX = "\x1eAutoZZZ-overlay:"


def valid_log_binding(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{32}", value) is not None


class _WorkerLog(str):
    def __new__(cls, message, binding=None):
        value = super().__new__(cls, message)
        value.binding = binding
        return value


def decode_worker_log(line, legacy_binding=None):
    if line.startswith(_PREFIX):
        binding, separator, message = line[len(_PREFIX):].partition(":")
        if separator and (binding == "-" or valid_log_binding(binding)):
            return _WorkerLog(message, None if binding == "-" else binding)
    return _WorkerLog(line, legacy_binding)


def log_event(message):
    event = dict(type="log", message=str(message))
    if isinstance(message, _WorkerLog):
        event["overlay_log_binding"] = message.binding
    return event


def overlay_state_event(state, legacy_binding):
    # Only a bound state from the old wire format may opt into connection-scoped stderr.
    event = dict(type="state", state=state)
    if (legacy_binding is not None and state.get("window_handle") is not None
            and "window_process_created" not in state and "overlay_log_binding" not in state):
        event["legacy_log_binding"] = legacy_binding
    return event


class _ConsoleFormatter(logging.Formatter):
    def __init__(self, original):
        super().__init__()
        self.original = original or logging.Formatter()

    def format(self, record):
        prefix = _PREFIX + (getattr(record, "autozzz_log_binding", None) or "-") + ":"
        return "\n".join(prefix + line for line in self.original.format(record).split("\n"))


def scope_console_logs(logger, current_context):
    factory = logging.getLogRecordFactory()
    if not getattr(factory, "_autozzz_scoped", False):
        def create_record(name, *args, **kwargs):
            binding = None
            if name == logger.name or name.startswith(logger.name + "."):
                controller = getattr(current_context(), "controller", None)
                window = getattr(controller, "game_win", None)
                binding = getattr(getattr(window, "bound", None), "overlay_log_binding", None)
            record = factory(name, *args, **kwargs)
            record.autozzz_log_binding = binding
            return record

        create_record._autozzz_scoped = True
        logging.setLogRecordFactory(create_record)
    for handler in logger.handlers:
        if getattr(handler, "stream", None) is sys.stderr and not isinstance(handler.formatter, _ConsoleFormatter):
            handler.setFormatter(_ConsoleFormatter(handler.formatter))
