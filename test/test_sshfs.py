#!/usr/bin/env python
import errno
import filecmp
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest
from conftest import CaptureFixture
from util import base_cmdline, basename, cleanup, fuse_test_marker, safe_sleep, umount, wait_for_mount


pytestmark = fuse_test_marker()


class NameGenerator:
    counter = 0

    def __call__(self) -> str:
        self.counter += 1
        return f'testfile_{self.counter}'


name_generator = NameGenerator()


def name_in_dir(name: str, path: Path) -> bool:
    return any(name == cur_path.name for cur_path in path.iterdir())


@pytest.mark.parametrize('debug', (False, True))
@pytest.mark.parametrize('cache_timeout', (0, 1))
@pytest.mark.parametrize('sync_rd', (True, False))
@pytest.mark.parametrize('multiconn', (True, False))
def test_sshfs(  # noqa: too-many-statements
    tmp_path_factory: pytest.TempPathFactory,
    data_file: (Path, bytes),
    debug: bool,
    cache_timeout: int,
    sync_rd: bool,
    multiconn: bool,
    capfd: CaptureFixture,
) -> None:
    # Avoid false positives from debug messages
    # if debug:
    #     capfd.register_output(r'^   unique: [0-9]+, error: -[0-9]+ .+$', count=0)

    # Avoid false positives from storing key for localhost
    capfd.register_output(r'Warning: Permanently added "localhost" .+', count=0)

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
    test_data_file, test_data = data_file

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

            tst_utimens(mnt_dir)
            tst_utimens_now(mnt_dir)
            tst_statvfs(mnt_dir)
            tst_chmod(mnt_dir)

            tst_create(mnt_dir)
            tst_open_read(src_dir, mnt_dir, test_data_file)
            tst_open_write(src_dir, mnt_dir, test_data_file)
            tst_append(src_dir, mnt_dir)
            tst_seek(src_dir, mnt_dir)
            tst_truncate_path(mnt_dir, test_data)
            tst_truncate_fd(mnt_dir, test_data)
            tst_passthrough(src_dir, mnt_dir, cache_timeout)

            tst_mkdir(mnt_dir)
            tst_readdir(src_dir, mnt_dir, test_data_file)
            tst_rmdir(src_dir, mnt_dir, cache_timeout)

            tst_rename(mnt_dir, cache_timeout)
            tst_link(mnt_dir, test_data_file, cache_timeout)
            tst_symlink(mnt_dir)
            tst_unlink(src_dir, mnt_dir, cache_timeout)
            tst_open_unlink(mnt_dir)

            if os.getuid() == 0:
                tst_chown(mnt_dir)
        except:  # noqa: E722
            cleanup(mount_process, mnt_dir)
            raise
        else:
            umount(mount_process, mnt_dir)


def os_create(path: Path) -> None:
    with os.fdopen(os.open(path, os.O_CREAT | os.O_RDWR)) as _:
        pass


def tst_utimens(mnt_dir: Path) -> None:
    path = mnt_dir / name_generator()
    path.mkdir()
    fstat = path.lstat()

    atime_ns = fstat.st_atime_ns + 42
    mtime_ns = fstat.st_mtime_ns - 42
    os.utime(path, None, ns=(atime_ns, mtime_ns))

    fstat = path.lstat()

    assert fstat.st_atime_ns == atime_ns
    assert fstat.st_mtime_ns == mtime_ns


def tst_utimens_now(mnt_dir: Path) -> None:
    path = mnt_dir / name_generator()
    os_create(path)
    os.utime(path, None)

    fstat = path.lstat()
    # We should get now-timestamps
    assert fstat.st_atime_ns != 0
    assert fstat.st_mtime_ns != 0


def tst_statvfs(mnt_dir: Path) -> None:
    os.statvfs(mnt_dir)


def tst_chmod(mnt_dir: Path) -> None:
    mode = 0o600
    path = mnt_dir / name_generator()
    os_create(path)
    path.chmod(mode)

    assert path.lstat().st_mode & 0o777 == mode


def tst_create(mnt_dir: Path) -> None:
    name = name_generator()
    path = mnt_dir / name
    with pytest.raises(OSError) as exc_info:
        path.lstat()
    assert exc_info.value.errno == errno.ENOENT
    assert not name_in_dir(name, mnt_dir)

    os_create(path)

    assert name_in_dir(name, mnt_dir)
    fstat = path.lstat()
    assert stat.S_ISREG(fstat.st_mode)
    assert fstat.st_nlink == 1
    assert fstat.st_size == 0
    assert fstat.st_uid == os.getuid()
    assert fstat.st_gid == os.getgid()


def tst_open_read(src_dir: Path, mnt_dir: Path, test_data_file: Path) -> None:
    name = name_generator()
    with (src_dir / name).open('wb') as fh_out, test_data_file.open('rb') as fh_in:
        shutil.copyfileobj(fh_in, fh_out)

    assert filecmp.cmp(mnt_dir / name, test_data_file, False)


def tst_open_write(src_dir: Path, mnt_dir: Path, test_data_file: Path) -> None:
    name = name_generator()
    fd = os.open(src_dir / name, os.O_CREAT | os.O_RDWR)
    os.close(fd)
    path = mnt_dir / name
    with path.open('wb') as fh_out, test_data_file.open('rb') as fh_in:
        shutil.copyfileobj(fh_in, fh_out)

    assert filecmp.cmp(path, test_data_file, False)


def tst_append(src_dir: Path, mnt_dir: Path) -> None:
    name = name_generator()
    os_create(src_dir / name)
    path = mnt_dir / name
    with os.fdopen(os.open(path, os.O_WRONLY), 'wb') as fd:
        fd.write(b'foo\n')
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_APPEND), 'ab') as fd:
        fd.write(b'bar\n')

    with path.open('rb') as fh_:
        assert fh_.read() == b'foo\nbar\n'


def tst_seek(src_dir: Path, mnt_dir: Path) -> None:
    name = name_generator()
    os_create(src_dir / name)
    path = mnt_dir / name
    with os.fdopen(os.open(path, os.O_WRONLY), 'wb') as fd:
        fd.seek(1, os.SEEK_SET)
        fd.write(b'foobar\n')
    with os.fdopen(os.open(path, os.O_WRONLY), 'wb') as fd:
        fd.seek(4, os.SEEK_SET)
        fd.write(b'com')

    with path.open('rb') as fh_:
        assert fh_.read() == b'\0foocom\n'


def tst_truncate_path(mnt_dir: Path, test_data: bytes) -> None:
    assert len(test_data) > 1024

    path = mnt_dir / name_generator()
    with path.open('wb') as fh_:
        fh_.write(test_data)

    fstat = path.lstat()
    size = fstat.st_size
    assert size == len(test_data)

    # Add zeros at the end
    os.truncate(path, size + 1024)
    assert path.lstat().st_size == size + 1024
    with path.open('rb') as fh_:
        assert fh_.read(size) == test_data
        assert fh_.read(1025) == b'\0' * 1024

    # Truncate data
    os.truncate(path, size - 1024)
    assert path.lstat().st_size == size - 1024
    with path.open('rb') as fh_:
        assert fh_.read(size) == test_data[: size - 1024]

    path.unlink()


def tst_truncate_fd(mnt_dir: Path, test_data: bytes) -> None:
    assert len(test_data) > 1024
    with NamedTemporaryFile('w+b', 0, dir=mnt_dir) as fh_:
        fd = fh_.fileno()
        fh_.write(test_data)
        fstat = os.fstat(fd)
        size = fstat.st_size
        assert size == len(test_data)

        # Add zeros at the end
        os.ftruncate(fd, size + 1024)
        assert os.fstat(fd).st_size == size + 1024
        fh_.seek(0)
        assert fh_.read(size) == test_data
        assert fh_.read(1025) == b'\0' * 1024

        # Truncate data
        os.ftruncate(fd, size - 1024)
        assert os.fstat(fd).st_size == size - 1024
        fh_.seek(0)
        assert fh_.read(size) == test_data[: size - 1024]


def tst_passthrough(src_dir: Path, mnt_dir: Path, cache_timeout: int) -> None:
    name = name_generator()
    src_path = src_dir / name
    mnt_path = src_dir / name
    assert not name_in_dir(name, src_dir)
    assert not name_in_dir(name, mnt_dir)
    with src_path.open('w', encoding='utf-8') as fh_:
        fh_.write('Hello, world')
    assert name_in_dir(name, src_dir)
    if cache_timeout:
        safe_sleep(cache_timeout + 1)
    assert name_in_dir(name, mnt_dir)
    assert src_path.lstat() == mnt_path.lstat()

    name = name_generator()
    src_path = src_dir / name
    mnt_path = src_dir / name
    assert not name_in_dir(name, src_dir)
    assert not name_in_dir(name, mnt_dir)
    with mnt_path.open('w', encoding='utf-8') as fh_:
        fh_.write('Hello, world')
    assert name_in_dir(name, src_dir)
    if cache_timeout:
        safe_sleep(cache_timeout + 1)
    assert name_in_dir(name, mnt_dir)
    assert src_path.lstat() == mnt_path.lstat()


def tst_mkdir(mnt_dir: Path) -> None:
    dirname = name_generator()
    path = mnt_dir / dirname
    path.mkdir()
    fstat = path.lstat()
    assert stat.S_ISDIR(fstat.st_mode)
    assert not list(path.iterdir())
    assert fstat.st_nlink in (1, 2)
    assert name_in_dir(dirname, mnt_dir)


def tst_readdir(src_dir: Path, mnt_dir: Path, test_data_file: Path) -> None:
    newdir = name_generator()
    src_newdir = src_dir / newdir
    mnt_newdir = mnt_dir / newdir
    file_ = src_newdir / name_generator()
    subdir = src_newdir / name_generator()
    subfile = subdir / name_generator()

    src_newdir.mkdir()
    shutil.copyfile(test_data_file, file_)
    subdir.mkdir()
    shutil.copyfile(test_data_file, subfile)

    listdir_is = sorted(path.name for path in mnt_newdir.iterdir())
    listdir_should = [file_.name, subdir.name]
    listdir_should.sort()
    assert listdir_is == listdir_should

    file_.unlink()
    subfile.unlink()
    subdir.rmdir()
    src_newdir.rmdir()


def tst_rmdir(src_dir: Path, mnt_dir: Path, cache_timeout: int) -> None:
    dirname = name_generator()
    path = mnt_dir / dirname
    (src_dir / dirname).mkdir()
    if cache_timeout:
        safe_sleep(cache_timeout + 1)
    assert name_in_dir(dirname, mnt_dir)
    path.rmdir()
    with pytest.raises(OSError) as exc_info:
        path.lstat()
    assert exc_info.value.errno == errno.ENOENT
    assert not name_in_dir(dirname, mnt_dir)
    assert not name_in_dir(dirname, src_dir)


def tst_rename(mnt_dir: Path, cache_timeout: int) -> None:
    name1 = name_generator()
    name2 = name_generator()
    path1 = mnt_dir / name1
    path2 = mnt_dir / name2

    data1 = b'foo'
    with path1.open('wb', buffering=0) as fh_:
        fh_.write(data1)

    fstat1 = path1.lstat()
    path1.rename(path2)
    if cache_timeout:
        safe_sleep(cache_timeout)

    fstat2 = path2.lstat()

    with path2.open('rb', buffering=0) as fh_:
        data2 = fh_.read()

    for attr in ('st_mode', 'st_dev', 'st_uid', 'st_gid', 'st_size', 'st_atime_ns', 'st_mtime_ns', 'st_ino'):
        assert getattr(fstat1, attr) == getattr(fstat2, attr)
    assert getattr(fstat2, 'st_ctime_ns') >= getattr(fstat1, 'st_ctime_ns')

    assert name_in_dir(path2.name, mnt_dir)
    assert data1 == data2


def tst_link(mnt_dir: Path, test_data_file: Path, cache_timeout: int) -> None:
    path1 = mnt_dir / name_generator()
    path2 = mnt_dir / name_generator()
    shutil.copyfile(test_data_file, path1)
    assert filecmp.cmp(path1, test_data_file, False)

    fstat1 = path1.lstat()
    assert fstat1.st_nlink == 1

    path2.hardlink_to(path1)

    # The link operation changes st_ctime, and if we're unlucky
    # the kernel will keep the old value cached for path1, and
    # retrieve the new value for path2 (at least, this is the only
    # way I can explain the test failure). To avoid this problem,
    # we need to wait until the cached value has expired.
    if cache_timeout:
        safe_sleep(cache_timeout)

    fstat1 = path1.lstat()
    assert fstat1.st_nlink == 2

    fstat2 = path2.lstat()
    for attr in ('st_mode', 'st_dev', 'st_uid', 'st_gid', 'st_size', 'st_atime_ns', 'st_mtime_ns', 'st_ctime_ns'):
        assert getattr(fstat1, attr) == getattr(fstat2, attr)

    assert name_in_dir(path2.name, mnt_dir)
    assert filecmp.cmp(path1, path2, False)

    path2.unlink()

    assert not name_in_dir(path2.name, mnt_dir)
    with pytest.raises(FileNotFoundError):
        path2.lstat()

    path1.unlink()


def tst_symlink(mnt_dir: Path) -> None:
    linkname = name_generator()
    path = mnt_dir / linkname
    path.symlink_to('/imaginary/dest')
    fstat = path.lstat()
    assert stat.S_ISLNK(fstat.st_mode)
    assert str(path.readlink()) == '/imaginary/dest'
    assert fstat.st_nlink == 1
    assert name_in_dir(linkname, mnt_dir)


def tst_unlink(src_dir: Path, mnt_dir: Path, cache_timeout: int) -> None:
    name = name_generator()
    path = mnt_dir / name
    with (src_dir / name).open('wb') as fh_:
        fh_.write(b'hello')
    if cache_timeout:
        safe_sleep(cache_timeout + 1)
    assert name_in_dir(name, mnt_dir)
    path.unlink()
    with pytest.raises(OSError) as exc_info:
        path.lstat()
    assert exc_info.value.errno == errno.ENOENT
    assert not name_in_dir(name, mnt_dir)
    assert not name_in_dir(name, src_dir)


def tst_open_unlink(mnt_dir: Path) -> None:
    name = name_generator()
    data1 = b'foo'
    data2 = b'bar'
    path = mnt_dir / name
    with path.open('wb+', buffering=0) as fh_:
        fh_.write(data1)
        path.unlink()
        with pytest.raises(OSError) as exc_info:
            path.lstat()
        assert exc_info.value.errno == errno.ENOENT
        assert not name_in_dir(name, mnt_dir)
        fh_.write(data2)
        fh_.seek(0)
        assert fh_.read() == data1 + data2


def tst_chown(mnt_dir: Path) -> None:
    path = mnt_dir / name_generator()
    path.mkdir()
    fstat = path.lstat()
    uid = fstat.st_uid
    gid = fstat.st_gid

    uid_new = uid + 1
    os.chown(path, uid_new, -1)
    fstat = path.lstat()
    assert fstat.st_uid == uid_new
    assert fstat.st_gid == gid

    gid_new = gid + 1
    os.chown(path, -1, gid_new)
    fstat = path.lstat()
    assert fstat.st_uid == uid_new
    assert fstat.st_gid == gid_new


if __name__ == '__main__':
    sys.exit(pytest.main([__file__] + sys.argv[1:]))
