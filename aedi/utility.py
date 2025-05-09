#
#    Module to build various libraries and tools for macOS
#    Copyright (C) 2020-2025 Alexey Lysiuk
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

import collections.abc
import os
import shutil
import typing
from pathlib import Path

from .packaging.version import Version as StrictVersion

# Minimum OS versions
OS_VERSION_X86_64 = StrictVersion('10.15')
OS_VERSION_ARM64 = StrictVersion('11.0')


class ArgumentValue(str):
    def __add__(self, other):
        value = ' ' * bool(self) + other
        return super().__add__(value)


class CommandLineOptions(dict):
    # Rules to combine argument's name and value
    MAKE_RULES = 0
    CMAKE_RULES = 1

    def __missing__(self, key):
        return ArgumentValue()

    def __setitem__(self, key, value):
        return super().__setitem__(key, ArgumentValue(value) if value else None)

    def to_list(self, rules=MAKE_RULES) -> list:
        result = []

        for arg_name, arg_value in self.items():
            if rules == CommandLineOptions.MAKE_RULES:
                arg_value = f'={arg_value}' if arg_value else ''
                option = arg_name + arg_value
            elif rules == CommandLineOptions.CMAKE_RULES:
                arg_value = arg_value if arg_value else ''
                option = f'-D{arg_name}={arg_value}'
            else:
                assert False, 'Unknown argument rules'

            result.append(option)

        return result


class TargetPlatform:
    def __init__(self, architecture: str, host: str, os_version: typing.Union[str, StrictVersion],
                 sdk_path: Path, prefix_path: Path):
        self.architecture = architecture
        self.host = host
        self.os_version = os_version if isinstance(os_version, StrictVersion) else StrictVersion(os_version)
        self.sdk_path = sdk_path
        self.c_compiler = prefix_path / f'bin/{host}-gcc'
        self.cxx_compiler = prefix_path / f'bin/{host}-g++'


def remove_empty_directories(path: Path) -> int:
    content: typing.List[str] = os.listdir(path)
    count = len(content)
    removed = 0

    for entry in content:
        abspath = path / entry

        if os.path.isdir(abspath):
            removed += remove_empty_directories(abspath)

    if count == removed:
        os.rmdir(path)
        removed = 1

    return removed


def symlink_directory(src_path: Path, dst_path: Path, cleanup=True):
    if cleanup:
        # Delete obsolete symbolic links
        for root, _, files in os.walk(dst_path, followlinks=True):
            for filename in files:
                file_path = Path(root) / filename

                if file_path.is_symlink() and not file_path.exists():
                    os.remove(file_path)

    # Create symbolic links if needed
    for entry in src_path.iterdir():
        dst_subpath = dst_path / entry.name
        if entry.is_dir():
            os.makedirs(dst_subpath, exist_ok=True)
            symlink_directory(entry, dst_subpath, cleanup=False)
        elif not dst_subpath.exists():
            if entry.is_symlink():
                shutil.copy(entry, dst_subpath, follow_symlinks=False)
            else:
                os.symlink(entry, dst_subpath)


def _hardlink_directory(src_path: Path, dst_path: Path, seen_inos: set[int]):
    for entry in src_path.iterdir():
        dst_subpath = dst_path / entry.name
        if entry.is_dir():
            os.makedirs(dst_subpath, exist_ok=True)
            _hardlink_directory(entry, dst_subpath, seen_inos)
        else:
            src_ino = os.stat(entry).st_ino
            dst_ino = None

            need_link = False
            need_unlink = False

            try:
                dst_ino = os.stat(dst_subpath, follow_symlinks=False).st_ino
            except FileNotFoundError:
                need_link = True

            if not need_link and src_ino != dst_ino:
                need_link = need_unlink = True

            if need_link:
                if need_unlink:
                    os.unlink(dst_subpath)

                os.link(entry, dst_subpath)

            seen_inos.add(src_ino)


def _unlink_missing(path: Path, seen_inos: set[int]):
    if path.is_dir():
        for subpath in path.iterdir():
            _unlink_missing(subpath, seen_inos)
    else:
        if os.stat(path).st_ino not in seen_inos:
            os.unlink(path)


def hardlink_directories(src_paths: typing.Sequence[Path], dst_path: Path, cleanup=True):
    seen_inos: set[int] = set()

    for src_path in src_paths:
        _hardlink_directory(src_path, dst_path, seen_inos)

    if cleanup:
        for path in dst_path.iterdir():
            _unlink_missing(path, seen_inos)

        remove_empty_directories(dst_path)


# Case insensitive dictionary class from
# https://github.com/psf/requests/blob/v2.25.0/requests/structures.py

class CaseInsensitiveDict(collections.abc.MutableMapping):
    """A case-insensitive ``dict``-like object.
    Implements all methods and operations of
    ``MutableMapping`` as well as dict's ``copy``. Also
    provides ``lower_items``.
    All keys are expected to be strings. The structure remembers the
    case of the last key to be set, and ``iter(instance)``,
    ``keys()``, ``items()``, ``iterkeys()``, and ``iteritems()``
    will contain case-sensitive keys. However, querying and contains
    testing is case insensitive::
        cid = CaseInsensitiveDict()
        cid['Accept'] = 'application/json'
        cid['aCCEPT'] == 'application/json'  # True
        list(cid) == ['Accept']  # True
    For example, ``headers['content-encoding']`` will return the
    value of a ``'Content-Encoding'`` response header, regardless
    of how the header name was originally stored.
    If the constructor, ``.update``, or equality comparison
    operations are given keys that have equal ``.lower()``s, the
    behavior is undefined.
    """

    def __init__(self, data=None, **kwargs):
        self._store = collections.OrderedDict()
        if data is None:
            data = {}
        self.update(data, **kwargs)

    def __setitem__(self, key, value):
        # Use the lowercased key for lookups, but store the actual
        # key alongside the value.
        self._store[key.lower()] = (key, value)

    def __getitem__(self, key):
        return self._store[key.lower()][1]

    def __delitem__(self, key):
        del self._store[key.lower()]

    def __iter__(self):
        return (casedkey for casedkey, mappedvalue in self._store.values())

    def __len__(self):
        return len(self._store)

    def lower_items(self):
        """Like iteritems(), but with all lowercase keys."""
        return (
            (lowerkey, keyval[1])
            for (lowerkey, keyval)
            in self._store.items()
        )

    def __eq__(self, other):
        if isinstance(other, collections.abc.Mapping):
            other = CaseInsensitiveDict(other)
        else:
            return NotImplemented
        # Compare insensitively
        return dict(self.lower_items()) == dict(other.lower_items())

    # Copy is required
    def copy(self):
        return CaseInsensitiveDict(self._store.values())

    def __repr__(self):
        return str(dict(self.items()))
