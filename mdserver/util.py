#
# Copyright 2016-2023 Australian National University
#
# Please see the LICENSE.txt file for details.

import sys


def _removeprefix(text, prefix):
    """Remove prefix from text if found.

    For backwards compatibility with Python versions prior to 3.9.
    """
    if sys.version_info.minor >= 9:
        return text.removeprefix(prefix)
    if text.startswith(prefix):
        return text[len(prefix) :]
    else:
        return text


# distutils has been deprecated for ages, and is removed in 3.12 - we need our
# own version of strtobool.
#
# This is based on the version from
# https://github.com/symonsoft/str2bool/blob/master/str2bool/__init__.py
def strtobool(value):
    _true_set = {"yes", "true", "t", "y", "1"}
    _false_set = {"no", "false", "f", "n", "0"}
    if isinstance(value, str):
        value = value.lower()
        if value in _true_set:
            return True
        if value in _false_set:
            return False

    raise ValueError('Expected "%s"' % '", "'.join(_true_set | _false_set))


def strtobool_or_val(string):
    """Return a boolean True/False if string is or parses as a boolean,
    otherwise return the string itself.
    """
    if isinstance(string, bool):
        return string
    try:
        return strtobool(string)
    except ValueError:
        return string
