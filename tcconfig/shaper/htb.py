# encoding: utf-8

"""
.. codeauthor:: Tsuyoshi Hombashi <tsuyoshi.hombashi@gmail.com>
"""

from __future__ import absolute_import, division, unicode_literals

import errno
import re

import typepy

from .._common import logging_context, run_command_helper
from .._const import ShapingAlgorithm, TcCommandOutput, TcSubCommand
from .._error import TcAlreadyExist
from .._logger import logger
from .._network import get_no_limit_kbits
from .._tc_command_helper import run_tc_show
from ._interface import AbstractShaper


class HtbShaper(AbstractShaper):
    __DEFAULT_CLASS_MINOR_ID = 1

    class MinQdiscMinorId(object):
        OUTGOING = 40
        INCOMING = 20

    @property
    def algorithm_name(self):
        return ShapingAlgorithm.HTB

    def __init__(self, tc_obj):
        super(HtbShaper, self).__init__(tc_obj)

        self.__qdisc_minor_id = None
        self.__qdisc_minor_id_count = 0
        self.__netem_major_id = None
        self.__classid_wo_shaping = None

    def _get_qdisc_minor_id(self):
        if self.__qdisc_minor_id is None:
            self.__qdisc_minor_id = self.__get_unique_qdisc_minor_id()
            logger.debug("__get_unique_qdisc_minor_id: {:d}".format(
                self.__qdisc_minor_id))

        return self.__qdisc_minor_id

    def _get_netem_qdisc_major_id(self, base_id):
        if self.__netem_major_id is None:
            self.__netem_major_id = self.__get_unique_netem_major_id()

        return self.__netem_major_id

    def _make_qdisc(self):
        base_command = self._tc_obj.get_tc_command(TcSubCommand.QDISC)
        if base_command is None:
            return 0

        if self._tc_obj.is_change_shaping_rule:
            return 0

        handle = "{:s}:".format(self._tc_obj.qdisc_major_id_str)

        if self._tc_obj.is_add_shaping_rule:
            message = None
        else:
            message = self._tc_obj.EXISTS_MSG_TEMPLATE.format(
                "failed to '{command:s}': qdisc already exists "
                "({dev:s}, handle={handle:s}, algo={algorithm:s}).".format(
                    command=base_command, dev=self._dev, handle=handle,
                    algorithm=self.algorithm_name))

        run_command_helper(
            " ".join([
                base_command, self._dev, "root",
                "handle {:s}".format(handle),
                self.algorithm_name,
                "default {:d}".format(self.__DEFAULT_CLASS_MINOR_ID),
            ]),
            ignore_error_msg_regexp=self._tc_obj.REGEXP_FILE_EXISTS,
            notice_msg=message,
            exception_class=TcAlreadyExist)

        return self.__add_default_class()

    def _add_rate(self):
        base_command = self._tc_obj.get_tc_command(TcSubCommand.CLASS)

        parent = "{:s}:".format(self._get_tc_parent(
            "{:s}:".format(self._tc_obj.qdisc_major_id_str)).split(":")[0])
        classid = self._get_tc_parent("{:s}:{:d}".format(
            self._tc_obj.qdisc_major_id_str, self._get_qdisc_minor_id()))

        no_limit_kbits = get_no_limit_kbits(self._tc_device)

        kbits = self._tc_obj.bandwidth_rate
        if kbits is None:
            kbits = no_limit_kbits

        command_item_list = [
            base_command,
            self._dev,
            "parent {:s}".format(parent),
            "classid {:s}".format(classid),
            self.algorithm_name,
            "rate {}Kbit".format(kbits),
            "ceil {}Kbit".format(kbits),
        ]

        if kbits != no_limit_kbits:
            command_item_list.extend([
                "burst {}KB".format(kbits / (10 * 8)),
                "cburst {}KB".format(kbits / (10 * 8)),
            ])

        run_command_helper(
            " ".join(command_item_list),
            ignore_error_msg_regexp=self._tc_obj.REGEXP_FILE_EXISTS,
            notice_msg=self._tc_obj.EXISTS_MSG_TEMPLATE.format(
                "failed to '{command:s}': class already exists "
                "({dev:s}, parent={parent:s}, classid={classid:s}, "
                "algo={algorithm:s}).".format(
                    command=base_command, dev=self._dev, parent=parent,
                    classid=classid, algorithm=self.algorithm_name)),
            exception_class=TcAlreadyExist)

    def _add_exclude_filter(self):
        import subprocrunner

        if all([typepy.is_null_string(param)
                for param in (self._tc_obj.exclude_dst_network, self._tc_obj.exclude_src_network,
                              self._tc_obj.exclude_dst_port, self._tc_obj.exclude_src_port)]):
            logger.debug("no exclude filter found")
            return

        base_command = self._tc_obj.get_tc_command(TcSubCommand.FILTER)
        if base_command is None:
            return 0

        command_item_list = [
            base_command,
            self._dev,
            "protocol {:s}".format(self._tc_obj.protocol),
            "parent {:s}:".format(self._tc_obj.qdisc_major_id_str),
            "prio 1",
            "u32",
        ]

        if typepy.is_not_null_string(self._tc_obj.exclude_dst_network):
            command_item_list.append("match {:s} {:s} {:s}".format(
                self._tc_obj.protocol_match, "dst", self._tc_obj.exclude_dst_network))

        if typepy.is_not_null_string(self._tc_obj.exclude_src_network):
            command_item_list.append("match {:s} {:s} {:s}".format(
                self._tc_obj.protocol_match, "src", self._tc_obj.exclude_src_network))

        if typepy.is_not_null_string(self._tc_obj.exclude_dst_port):
            command_item_list.append("match {:s} {:s} {:s} 0xffff".format(
                self._tc_obj.protocol_match, "dport", self._tc_obj.exclude_dst_port))

        if typepy.is_not_null_string(self._tc_obj.exclude_src_port):
            command_item_list.append("match {:s} {:s} {:s} 0xffff".format(
                self._tc_obj.protocol_match, "sport", self._tc_obj.exclude_src_port))

        command_item_list.append("flowid {:s}".format(self.__classid_wo_shaping))

        return subprocrunner.SubprocessRunner(" ".join(command_item_list)).run()

    def set_shaping(self):
        is_add_shaping_rule = self._tc_obj.is_add_shaping_rule

        with logging_context("_make_qdisc"):
            try:
                self._make_qdisc()
            except TcAlreadyExist:
                if not is_add_shaping_rule:
                    return errno.EINVAL

        with logging_context("_add_rate"):
            try:
                self._add_rate()
            except TcAlreadyExist:
                if not is_add_shaping_rule:
                    return errno.EINVAL

        with logging_context("_set_netem"):
            self._set_netem()

        with logging_context("_add_exclude_filter"):
            self._add_exclude_filter()

        with logging_context("_add_filter"):
            self._add_filter()

        return 0

    def __get_unique_qdisc_minor_id(self):
        if (self._tc_obj.tc_command_output != TcCommandOutput.NOT_SET or
                self._tc_obj.is_change_shaping_rule):
            self.__qdisc_minor_id_count += 1

            return self.__DEFAULT_CLASS_MINOR_ID + self.__qdisc_minor_id_count

        exist_class_item_list = re.findall(
            "class {algorithm:s} {qdisc_major_id:s}[\:][0-9]+".format(
                algorithm=ShapingAlgorithm.HTB,
                qdisc_major_id=self._tc_obj.qdisc_major_id_str),
            run_tc_show(TcSubCommand.CLASS, self._tc_device),
            re.MULTILINE)

        exist_class_minor_id_list = []
        for class_item in exist_class_item_list:
            try:
                exist_class_minor_id_list.append(
                    typepy.type.Integer(class_item.split(":")[1]).convert())
            except typepy.TypeConversionError:
                continue

        logger.debug("existing class list with {:s}: {}".format(
            self._dev, exist_class_item_list))
        logger.debug("existing minor classid list with {:s}: {}".format(
            self._dev, exist_class_minor_id_list))

        next_minor_id = self.__DEFAULT_CLASS_MINOR_ID
        while True:
            if next_minor_id not in exist_class_minor_id_list:
                break

            next_minor_id += 1

        return next_minor_id

    def __get_unique_netem_major_id(self):
        exist_netem_item_list = re.findall(
            "qdisc [a-z]+ [a-z0-9]+",
            run_tc_show(TcSubCommand.QDISC, self._tc_device),
            re.MULTILINE)

        exist_netem_major_id_list = []
        for netem_item in exist_netem_item_list:
            exist_netem_major_id_list.append(int(netem_item.split()[-1], 16))

        logger.debug("existing netem list with {:s}: {}".format(
            self._dev, exist_netem_item_list))
        logger.debug("existing netem major id list with {:s}: {}".format(
            self._dev, exist_netem_major_id_list))

        next_netem_major_id = self._tc_obj.qdisc_major_id + 128
        while True:
            if next_netem_major_id not in exist_netem_major_id_list:
                break

            next_netem_major_id += 1

        return next_netem_major_id

    def __add_default_class(self):
        base_command = self._tc_obj.get_tc_command(TcSubCommand.CLASS)
        parent = "{:s}:".format(self._tc_obj.qdisc_major_id_str)
        classid = "{:s}:{:d}".format(
            self._tc_obj.qdisc_major_id_str, self.__DEFAULT_CLASS_MINOR_ID)
        self.__classid_wo_shaping = classid

        if self._tc_obj.is_add_shaping_rule:
            message = None
        else:
            message = self._tc_obj.EXISTS_MSG_TEMPLATE.format(
                "failed to default '{command:s}': class already exists "
                "({dev}, parent={parent:s}, classid={classid:s}, "
                "algo={algorithm:s})".format(
                    command=base_command, dev=self._dev, parent=parent,
                    classid=classid, algorithm=self.algorithm_name))

        return run_command_helper(
            " ".join([
                base_command, self._dev,
                "parent {:s}".format(parent),
                "classid {:s}".format(classid),
                self.algorithm_name,
                "rate {}kbit".format(get_no_limit_kbits(self._tc_device)),
            ]),
            ignore_error_msg_regexp=self._tc_obj.REGEXP_FILE_EXISTS,
            notice_msg=message)
