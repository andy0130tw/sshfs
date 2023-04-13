import random
import re
import string
import sys
import time
from pathlib import Path
from typing import Generator

import pytest


__all__ = ['CaptureFixture']


class CaptureFixture(pytest.CaptureFixture):
    false_positives = []

    def register_output(self, pattern: str, count: int = 1, flags: re.RegexFlag = re.MULTILINE) -> None:
        '''Register *pattern* as false positive for output checking

        This prevents the test from failing because the output otherwise
        appears suspicious.
        '''

        self.false_positives.append((pattern, flags, count))


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


@pytest.fixture()
def pass_capfd(request: pytest.FixtureRequest, capfd: CaptureFixture) -> None:
    '''Provide capfd object to UnitTest instances'''
    request.instance.capfd = capfd


def check_test_output(capfd: CaptureFixture) -> None:
    (stdout, stderr) = capfd.readouterr()

    # Write back what we've read (so that it will still be printed.
    sys.stdout.write(stdout)
    sys.stderr.write(stderr)

    # Strip out false positives
    for pattern, flags, count in capfd.false_positives:
        cp = re.compile(pattern, flags)
        (stdout, cnt) = cp.subn('', stdout, count=count)
        if count == 0 or count - cnt > 0:
            stderr = cp.sub('', stderr, count=count - cnt)

    patterns = [
        rf'\b{x}\b'
        for x in (
            'exception',
            'error',
            'warning',
            'fatal',
            'traceback',
            'fault',
            'crash(?:ed)?',
            'abort(?:ed)',
            'uninitiali[zs]ed',
        )
    ]
    patterns += ['^==[0-9]+== ']
    for pattern in patterns:
        cp = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
        hit = cp.search(stderr)
        if hit:
            raise AssertionError(f'Suspicious output to stderr (matched "{hit.group(0)}")')
        hit = cp.search(stdout)
        if hit:
            raise AssertionError(f'Suspicious output to stdout (matched "{hit.group(0)}")')


@pytest.fixture(scope='session', autouse=True)
def data_file(tmp_path_factory: pytest.TempPathFactory) -> (Path, bytes):
    data_dir = tmp_path_factory.mktemp('data')
    test_data_file = data_dir / 'data.txt'
    random.seed(12345)
    test_data = ''.join(random.choices(string.ascii_letters + string.digits, k=2048)).encode()
    with test_data_file.open('wb') as fh:
        fh.write(test_data)
    return test_data_file, test_data


# This is a terrible hack that allows us to access the fixtures from the
# pytest_runtest_call hook. Among a lot of other hidden assumptions, it probably
# relies on tests running sequential (i.e., don't dare to use e.g. the xdist
# plugin)
current_capfd: CaptureFixture | None = None


@pytest.yield_fixture(autouse=True)
def save_cap_fixtures(request, capfd: CaptureFixture) -> Generator[None, None, None]:
    global current_capfd  # noqa
    capfd.false_positives = []

    # Monkeypatch in a function to register false positives
    type(capfd).register_output = CaptureFixture.register_output

    if request.config.getoption('capture') == 'no':
        capfd = None
    current_capfd = capfd
    bak = current_capfd
    yield

    # Try to catch problems with this hack (e.g. when running tests
    # simultaneously)
    assert bak is current_capfd
    current_capfd = None


@pytest.hookimpl(trylast=True)
def pytest_runtest_call(item: pytest.Item) -> None:  # noqa: unused-argument
    capfd = current_capfd
    if capfd is not None:
        check_test_output(capfd)
