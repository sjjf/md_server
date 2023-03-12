#
# Copyright 2016-2023 Australian National University
#
# Please see the LICENSE.txt file for details.

import sys
from distutils.util import strtobool


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
