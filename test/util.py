import os
import stat
import subprocess
import time
from pathlib import Path

import pytest


__all__ = ['base_cmdline', 'basename', 'cleanup', 'fuse_test_marker', 'safe_sleep', 'umount', 'wait_for_mount']

basename = Path(__file__).parent.parent


def wait_for_mount(mount_process: subprocess.Popen, mnt_dir: Path, test_fn=os.path.ismount) -> None:
    elapsed = 0
    while elapsed < 30:
        if test_fn(mnt_dir):
            return
        if mount_process.poll() is not None:
            pytest.fail('file system process terminated prematurely')
        time.sleep(0.1)
        elapsed += 0.1
    pytest.fail('mountpoint failed to come up')


def cleanup(mount_process: subprocess.Popen, mnt_dir: Path) -> None:
    subprocess.call(['fusermount3', '-z', '-u', str(mnt_dir)], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    mount_process.terminate()
    try:
        mount_process.wait(1)
    except subprocess.TimeoutExpired:
        mount_process.kill()


def umount(mount_process: subprocess.Popen, mnt_dir: Path) -> None:
    subprocess.check_call(['fusermount3', '-z', '-u', str(mnt_dir)])
    assert not os.path.ismount(mnt_dir)
    try:
        code = mount_process.wait(30)
        if code != 0:
            pytest.fail(f'file system process terminated with code {code}')
    except subprocess.TimeoutExpired:
        pytest.fail('mount process did not terminate')


def safe_sleep(secs: int) -> None:
    '''Like time.sleep(), but sleep for at least *secs*

    `time.sleep` may sleep less than the given period if a signal is
    received. This function ensures that we sleep for at least the
    desired time.
    '''

    now = time.time()
    end = now + secs
    while now < end:
        time.sleep(end - now)
        now = time.time()


def fuse_test_marker() -> pytest.MarkDecorator:
    '''Return a pytest.marker that indicates FUSE availability

    If system/user/environment does not support FUSE, return
    a `pytest.mark.skip` object with more details. If FUSE is
    supported, return `pytest.mark.uses_fuse()`.
    '''

    def skip(x):
        return pytest.mark.skip(reason=x)

    with subprocess.Popen(['which', 'fusermount3'], stdout=subprocess.PIPE, universal_newlines=True) as which:
        fusermount_path = which.communicate()[0].strip()

    if not fusermount_path or which.returncode != 0:
        return skip('Can\'t find fusermount3 executable')

    if not Path('/dev/fuse').exists():
        return skip('FUSE kernel module does not seem to be loaded')

    if os.getuid() == 0:
        return pytest.mark.uses_fuse()

    mode = Path(fusermount_path).stat().st_mode
    if mode & stat.S_ISUID == 0:
        return skip('fusermount3 executable not setuid, and we are not root.')

    try:
        with os.fdopen(os.open('/dev/fuse', os.O_RDWR)) as _:
            pass
    except OSError as exc:
        return skip(f'Unable to open /dev/fuse: {exc.strerror}')

    return pytest.mark.uses_fuse()


# Use valgrind if requested
if os.environ.get('TEST_WITH_VALGRIND', 'no').lower().strip() not in ('no', 'false', '0'):
    base_cmdline = ['valgrind', '-q', '--']
else:
    base_cmdline = []
