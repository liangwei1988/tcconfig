# encoding: utf-8

"""
.. codeauthor:: Tsuyoshi Hombashi <tsuyoshi.hombashi@gmail.com>
"""

from __future__ import absolute_import, unicode_literals

import contextlib
import errno
import os
import re
import sys

import logbook
import msgfy
import subprocrunner as spr
import typepy
from path import Path

from ._const import IPV6_OPTION_ERROR_MSG_FORMAT, TcCommandOutput
from ._logger import logger


_bin_path_cache = {}


@contextlib.contextmanager
def logging_context(name):
    logger.debug("|---- {:s}: {:s} -----".format("start", name))
    try:
        yield
    finally:
        logger.debug("----- {:s}: {:s} ----|".format("complete", name))


def find_bin_path(command):
    def _to_regular_bin_path(file_path):
        path_obj = Path(file_path)
        if path_obj.islink():
            return path_obj.readlinkabs()

        return file_path

    if command in _bin_path_cache:
        return _bin_path_cache.get(command)

    bin_path = spr.Which(command)
    if bin_path.is_exist():
        _bin_path_cache[command] = _to_regular_bin_path(bin_path.abspath())
        return _bin_path_cache[command]

    for sbin_path in ("/sbin/{:s}".format(command), "/usr/sbin/{:s}".format(command)):
        if os.path.isfile(sbin_path):
            _bin_path_cache[command] = _to_regular_bin_path(sbin_path)
            return _bin_path_cache[command]

    return None


def check_command_installation(command):
    if find_bin_path(command):
        return

    logger.error("command not found: {}".format(command))
    sys.exit(errno.ENOENT)


def initialize_cli(options):
    from ._logger import set_log_level

    debug_format_str = (
        "[{record.level_name}] {record.channel} {record.func_name} "
        "({record.lineno}): {record.message}")
    if options.log_level == logbook.DEBUG:
        info_format_str = debug_format_str
    else:
        info_format_str = "[{record.level_name}] {record.channel}: {record.message}"

    logbook.StderrHandler(level=logbook.DEBUG, format_string=debug_format_str).push_application()
    logbook.StderrHandler(level=logbook.INFO, format_string=info_format_str).push_application()

    set_log_level(options.log_level)
    spr.SubprocessRunner.is_save_history = True

    if options.is_output_stacktrace:
        spr.SubprocessRunner.is_output_stacktrace = options.is_output_stacktrace


def is_execute_tc_command(tc_command_output):
    return tc_command_output == TcCommandOutput.NOT_SET


def normalize_tc_value(tc_obj):
    import ipaddress

    try:
        tc_obj.sanitize()
    except ipaddress.AddressValueError as e:
        logger.error(IPV6_OPTION_ERROR_MSG_FORMAT.format(e))
        sys.exit(errno.EINVAL)
    except ValueError as e:
        logger.error(msgfy.to_error_message(e))
        sys.exit(errno.EINVAL)


def run_command_helper(command, ignore_error_msg_regexp, notice_msg, exception_class=None):
    proc = spr.SubprocessRunner(command, error_log_level=logbook.NOTSET)
    proc.run()

    if proc.returncode == 0:
        return 0

    if ignore_error_msg_regexp:
        match = ignore_error_msg_regexp.search(proc.stderr)
        if match is None:
            error_msg_format = "\n".join([
                "command execution failed",
                "  command={}".format(command),
                "  stderr={}".format(proc.stderr),
            ])

            if re.search("RTNETLINK answers: Operation not permitted", proc.stderr):
                logger.error(error_msg_format.format(command, proc.stderr))
                sys.exit(proc.returncode)

            logger.error(error_msg_format.format(command, proc.stderr))

            return proc.returncode

    if typepy.is_not_null_string(notice_msg):
        logger.notice(notice_msg)

    if exception_class is not None:
        raise exception_class(command)

    return proc.returncode
