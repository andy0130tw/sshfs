import itertools
import os
import random
import string
import subprocess
import time
from contextlib import contextmanager
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Generator, NamedTuple

import pytest
from util import base_cmdline, basename, cleanup, umount, wait_for_mount


__all__ = ['DataFile', 'SshfsDirs']


# If a test fails, wait a moment before retrieving the captured
# stdout/stderr. When using a server process, this makes sure that we capture
# any potential output of the server that comes *after* a test has failed. For
# example, if a request handler raises an exception, the server first signals an
# error to FUSE (causing the test to fail), and then logs the exception. Without
# the extra delay, the exception will go into nowhere.
@pytest.hookimpl(hookwrapper=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> Generator[None, None, None]:  # noqa: unused-argument
    outcome = yield
    failed = outcome.excinfo is not None
    if failed:
        time.sleep(1)


class DataFile(NamedTuple):
    path: Path
    data: bytes


@pytest.fixture(scope='session', autouse=True)
def data_file(tmp_path_factory: pytest.TempPathFactory) -> DataFile:
    data_dir = tmp_path_factory.mktemp('data')
    test_data_file = data_dir / 'data.txt'
    random.seed(12345)
    test_data = ''.join(random.choices(string.ascii_letters + string.digits, k=2048)).encode()
    test_data_file.write_bytes(test_data)
    return DataFile(test_data_file, test_data)


def product_dict_values(options: dict) -> list:
    return [tuple(zip(options.keys(), value_combo)) for value_combo in itertools.product(*options.values())]


class SshfsDirs(NamedTuple):
    src_dir: Path
    mnt_dir: Path
    cache_timeout: int


class TestNamemapType(Enum):
    NONE = auto()
    USER = auto()
    FILE = auto()
    FILE_EMPTY = auto()


@contextmanager
def mount_sshfs(  # noqa: too-many-locals
    tmp_path_factory: pytest.TempPathFactory,
    debug: bool,
    cache_timeout: int,
    sync_rd: bool,
    multiconn: bool,
    namemap: TestNamemapType,
) -> Generator[SshfsDirs, None, None]:
    # Test if we can ssh into localhost without password
    try:
        res = subprocess.call(
            [
                'ssh',
                '-o',
                'KbdInteractiveAuthentication=no',
                '-o',
                'ChallengeResponseAuthentication=no',
                '-o',
                'PasswordAuthentication=no',
                'localhost',
                '--',
                'true',
            ],
            stdin=subprocess.DEVNULL,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        res = 1
    if res != 0:
        pytest.fail('Unable to ssh into localhost without password prompt.')

    conf_dir = tmp_path_factory.mktemp('conf')
    mnt_dir = tmp_path_factory.mktemp('mnt')
    src_dir = tmp_path_factory.mktemp('src')

    cmdline = [*base_cmdline, str(basename / 'build/sshfs'), '-f', f'localhost:{src_dir}', str(mnt_dir)]
    if debug:
        cmdline += ['-o', 'sshfs_debug']

    if sync_rd:
        cmdline += ['-o', 'sync_readdir']

    # SSHFS Cache
    if cache_timeout == 0:
        cmdline += ['-o', 'dir_cache=no']
    else:
        cmdline += [
            '-o',
            f'dcache_timeout={cache_timeout}',
            '-o',
            'dir_cache=yes',
        ]

    # FUSE Cache
    cmdline += [
        '-o',
        'entry_timeout=0',
        '-o',
        'attr_timeout=0',
    ]

    if multiconn:
        cmdline += ['-o', 'max_conns=3']

    match namemap:
        case TestNamemapType.USER:
            cmdline += ['-o', 'namemap=user']
        case TestNamemapType.FILE:
            cmdline += ['-o', 'namemap=file']
            unamemap_path = conf_dir / 'unamefile.txt'
            with unamemap_path.open('w') as sr:
                sr.write('foo_user:root\n')
            gnamemap_path = conf_dir / 'gnamefile.txt'
            gnamemap_path.write_text('bar_group:root\n')
            cmdline += ['-o', f'unamefile={unamemap_path}', '-o', f'gnamefile={gnamemap_path}']
        case TestNamemapType.FILE_EMPTY:
            cmdline += ['-o', 'namemap=file']
            unamemap_path = conf_dir / 'unamefile.txt'
            unamemap_path.touch()
            gnamemap_path = conf_dir / 'gnamefile.txt'
            gnamemap_path.touch()
            cmdline += ['-o', f'unamefile={unamemap_path}', '-o', f'gnamefile={gnamemap_path}']

    new_env = dict(os.environ)  # copy, don't modify

    # Abort on warnings from glib
    new_env['G_DEBUG'] = 'fatal-warnings'

    with subprocess.Popen(cmdline, env=new_env) as mount_process:
        try:  # noqa: no-else-return
            wait_for_mount(mount_process, mnt_dir)
            yield SshfsDirs(src_dir, mnt_dir, cache_timeout)
        except:  # noqa: E722
            cleanup(mount_process, mnt_dir)
            raise
        else:
            umount(mount_process, mnt_dir)


def create_sshfs_dirs_fixture(name_map: TestNamemapType) -> Callable:
    @pytest.fixture(
        scope='session',
        params=product_dict_values(
            {
                'debug': [False, True],
                'cache_timeout': [0, 1],
                'sync_rd': [True, False],
                'multiconn': [True, False],
            }
        ),
    )
    def fixture(  # noqa: too-many-statements
        tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest
    ) -> Generator[SshfsDirs, None, None]:
        param_dict = dict(request.param)
        with mount_sshfs(
            tmp_path_factory,
            param_dict['debug'],
            param_dict['cache_timeout'],
            param_dict['sync_rd'],
            param_dict['multiconn'],
            name_map,
        ) as sshfs_dirs_:
            yield sshfs_dirs_

    return fixture


sshfs_dirs = create_sshfs_dirs_fixture(TestNamemapType.NONE)
sshfs_dirs_namemap_user = create_sshfs_dirs_fixture(TestNamemapType.USER)
sshfs_dirs_namemap_file = create_sshfs_dirs_fixture(TestNamemapType.FILE)
sshfs_dirs_namemap_file_not_found = create_sshfs_dirs_fixture(TestNamemapType.FILE_EMPTY)
