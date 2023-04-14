import itertools
import os
import random
import string
import subprocess
import time
from pathlib import Path
from typing import Generator, NamedTuple

import pytest
from util import base_cmdline, basename, cleanup, umount, wait_for_mount


__all__ = ['DataFile', 'SshfsDirs']


# If a test fails, wait a moment before retrieving the captured
# stdout/stderr. When using a server process, this makes sure that we capture
# any potential output of the server that comes *after* a test has failed. For
# example, if a request handler raises an exception, the server first signals an
# error to FUSE (causing the test to fail), and then logs the exception. Without
# the extra delay, the exception will go into nowhere.
@pytest.mark.hookwrapper
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
    with test_data_file.open('wb') as fh:
        fh.write(test_data)
    return DataFile(test_data_file, test_data)


def product_dict_values(options: dict) -> list:
    return [tuple(zip(options.keys(), value_combo)) for value_combo in itertools.product(*options.values())]


class SshfsDirs(NamedTuple):
    src_dir: Path
    mnt_dir: Path
    cache_timeout: int


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
def sshfs_dirs(  # noqa: too-many-statements
    tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest
) -> Generator[SshfsDirs, None, None]:
    param_dict = dict(request.param)
    debug: bool = param_dict['debug']
    cache_timeout: int = param_dict['cache_timeout']
    sync_rd: bool = param_dict['sync_rd']
    multiconn: bool = param_dict['multiconn']

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
