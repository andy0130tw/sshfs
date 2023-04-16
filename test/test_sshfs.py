#!/usr/bin/env python
import errno
import filecmp
import os
import pwd
import shutil
import stat
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest
from conftest import DataFile, SshfsDirs
from util import fuse_test_marker, safe_sleep


pytestmark = fuse_test_marker()


class NameGenerator:
    counter = 0

    def __call__(self) -> str:
        self.counter += 1
        return f'testfile_{self.counter}'


name_generator = NameGenerator()


def name_in_dir(name: str, path: Path) -> bool:
    return any(name == cur_path.name for cur_path in path.iterdir())


def os_create(path: Path) -> None:
    with os.fdopen(os.open(path, os.O_CREAT | os.O_RDWR)) as _:
        pass


def test_utimens(sshfs_dirs: SshfsDirs) -> None:
    path = sshfs_dirs.mnt_dir / name_generator()
    path.mkdir()
    fstat = path.lstat()

    atime_ns = fstat.st_atime_ns + 42
    mtime_ns = fstat.st_mtime_ns - 42
    os.utime(path, None, ns=(atime_ns, mtime_ns))

    fstat = path.lstat()

    assert fstat.st_atime_ns == atime_ns
    assert fstat.st_mtime_ns == mtime_ns


def test_utimens_now(sshfs_dirs: SshfsDirs) -> None:
    path = sshfs_dirs.mnt_dir / name_generator()
    os_create(path)
    os.utime(path, None)

    fstat = path.lstat()
    # We should get now-timestamps
    assert fstat.st_atime_ns != 0
    assert fstat.st_mtime_ns != 0


def test_statvfs(sshfs_dirs: SshfsDirs) -> None:
    os.statvfs(sshfs_dirs.mnt_dir)


def test_chmod(sshfs_dirs: SshfsDirs) -> None:
    mode = 0o600
    path = sshfs_dirs.mnt_dir / name_generator()
    os_create(path)
    path.chmod(mode)

    assert path.lstat().st_mode & 0o777 == mode


def test_create(sshfs_dirs: SshfsDirs) -> None:
    name = name_generator()
    path = sshfs_dirs.mnt_dir / name
    with pytest.raises(OSError) as exc_info:
        path.lstat()
    assert exc_info.value.errno == errno.ENOENT
    assert not name_in_dir(name, sshfs_dirs.mnt_dir)

    os_create(path)

    assert name_in_dir(name, sshfs_dirs.mnt_dir)
    fstat = path.lstat()
    assert stat.S_ISREG(fstat.st_mode)
    assert fstat.st_nlink == 1
    assert fstat.st_size == 0
    assert fstat.st_uid == os.getuid()
    assert fstat.st_gid == os.getgid()


def test_open_read(sshfs_dirs: SshfsDirs, data_file: DataFile) -> None:
    name = name_generator()
    with (sshfs_dirs.src_dir / name).open('wb') as fh_out, data_file.path.open('rb') as fh_in:
        shutil.copyfileobj(fh_in, fh_out)

    assert filecmp.cmp(sshfs_dirs.mnt_dir / name, data_file.path, False)


def test_open_write(sshfs_dirs: SshfsDirs, data_file: DataFile) -> None:
    name = name_generator()
    fd = os.open(sshfs_dirs.src_dir / name, os.O_CREAT | os.O_RDWR)
    os.close(fd)
    path = sshfs_dirs.mnt_dir / name
    with path.open('wb') as fh_out, data_file.path.open('rb') as fh_in:
        shutil.copyfileobj(fh_in, fh_out)

    assert filecmp.cmp(path, data_file.path, False)


def test_append(sshfs_dirs: SshfsDirs) -> None:
    name = name_generator()
    os_create(sshfs_dirs.src_dir / name)
    path = sshfs_dirs.mnt_dir / name
    with os.fdopen(os.open(path, os.O_WRONLY), 'wb') as fd:
        fd.write(b'foo\n')
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_APPEND), 'ab') as fd:
        fd.write(b'bar\n')

    with path.open('rb') as fh_:
        assert fh_.read() == b'foo\nbar\n'


def test_seek(sshfs_dirs: SshfsDirs) -> None:
    name = name_generator()
    os_create(sshfs_dirs.src_dir / name)
    path = sshfs_dirs.mnt_dir / name
    with os.fdopen(os.open(path, os.O_WRONLY), 'wb') as fd:
        fd.seek(1, os.SEEK_SET)
        fd.write(b'foobar\n')
    with os.fdopen(os.open(path, os.O_WRONLY), 'wb') as fd:
        fd.seek(4, os.SEEK_SET)
        fd.write(b'com')

    with path.open('rb') as fh_:
        assert fh_.read() == b'\0foocom\n'


def test_truncate_path(sshfs_dirs: SshfsDirs, data_file: DataFile) -> None:
    assert len(data_file.data) > 1024

    path = sshfs_dirs.mnt_dir / name_generator()
    with path.open('wb') as fh_:
        fh_.write(data_file.data)

    fstat = path.lstat()
    size = fstat.st_size
    assert size == len(data_file.data)

    # Add zeros at the end
    os.truncate(path, size + 1024)
    assert path.lstat().st_size == size + 1024
    with path.open('rb') as fh_:
        assert fh_.read(size) == data_file.data
        assert fh_.read(1025) == b'\0' * 1024

    # Truncate data
    os.truncate(path, size - 1024)
    assert path.lstat().st_size == size - 1024
    with path.open('rb') as fh_:
        assert fh_.read(size) == data_file.data[: size - 1024]

    path.unlink()


def test_truncate_fd(sshfs_dirs: SshfsDirs, data_file: DataFile) -> None:
    assert len(data_file.data) > 1024
    with NamedTemporaryFile('w+b', 0, dir=sshfs_dirs.mnt_dir) as fh_:
        fd = fh_.fileno()
        fh_.write(data_file.data)
        fstat = os.fstat(fd)
        size = fstat.st_size
        assert size == len(data_file.data)

        # Add zeros at the end
        os.ftruncate(fd, size + 1024)
        assert os.fstat(fd).st_size == size + 1024
        fh_.seek(0)
        assert fh_.read(size) == data_file.data
        assert fh_.read(1025) == b'\0' * 1024

        # Truncate data
        os.ftruncate(fd, size - 1024)
        assert os.fstat(fd).st_size == size - 1024
        fh_.seek(0)
        assert fh_.read(size) == data_file.data[: size - 1024]


def test_passthrough(sshfs_dirs: SshfsDirs) -> None:
    name = name_generator()
    src_path = sshfs_dirs.src_dir / name
    mnt_path = sshfs_dirs.src_dir / name
    assert not name_in_dir(name, sshfs_dirs.src_dir)
    assert not name_in_dir(name, sshfs_dirs.mnt_dir)
    with src_path.open('w', encoding='utf-8') as fh_:
        fh_.write('Hello, world')
    assert name_in_dir(name, sshfs_dirs.src_dir)
    if sshfs_dirs.cache_timeout:
        safe_sleep(sshfs_dirs.cache_timeout + 1)
    assert name_in_dir(name, sshfs_dirs.mnt_dir)
    assert src_path.lstat() == mnt_path.lstat()

    name = name_generator()
    src_path = sshfs_dirs.src_dir / name
    mnt_path = sshfs_dirs.src_dir / name
    assert not name_in_dir(name, sshfs_dirs.src_dir)
    assert not name_in_dir(name, sshfs_dirs.mnt_dir)
    with mnt_path.open('w', encoding='utf-8') as fh_:
        fh_.write('Hello, world')
    assert name_in_dir(name, sshfs_dirs.src_dir)
    if sshfs_dirs.cache_timeout:
        safe_sleep(sshfs_dirs.cache_timeout + 1)
    assert name_in_dir(name, sshfs_dirs.mnt_dir)
    assert src_path.lstat() == mnt_path.lstat()


def test_mkdir(sshfs_dirs: SshfsDirs) -> None:
    dirname = name_generator()
    path = sshfs_dirs.mnt_dir / dirname
    path.mkdir()
    fstat = path.lstat()
    assert stat.S_ISDIR(fstat.st_mode)
    assert not list(path.iterdir())
    assert fstat.st_nlink in (1, 2)
    assert name_in_dir(dirname, sshfs_dirs.mnt_dir)


def test_readdir(sshfs_dirs: SshfsDirs, data_file: DataFile) -> None:
    newdir = name_generator()
    src_newdir = sshfs_dirs.src_dir / newdir
    mnt_newdir = sshfs_dirs.mnt_dir / newdir
    file_ = src_newdir / name_generator()
    subdir = src_newdir / name_generator()
    subfile = subdir / name_generator()

    src_newdir.mkdir()
    shutil.copyfile(data_file.path, file_)
    subdir.mkdir()
    shutil.copyfile(data_file.path, subfile)

    listdir_is = sorted(path.name for path in mnt_newdir.iterdir())
    listdir_should = [file_.name, subdir.name]
    listdir_should.sort()
    assert listdir_is == listdir_should

    file_.unlink()
    subfile.unlink()
    subdir.rmdir()
    src_newdir.rmdir()


def test_rmdir(sshfs_dirs: SshfsDirs) -> None:
    dirname = name_generator()
    path = sshfs_dirs.mnt_dir / dirname
    (sshfs_dirs.src_dir / dirname).mkdir()
    if sshfs_dirs.cache_timeout:
        safe_sleep(sshfs_dirs.cache_timeout + 1)
    assert name_in_dir(dirname, sshfs_dirs.mnt_dir)
    path.rmdir()
    with pytest.raises(OSError) as exc_info:
        path.lstat()
    assert exc_info.value.errno == errno.ENOENT
    assert not name_in_dir(dirname, sshfs_dirs.mnt_dir)
    assert not name_in_dir(dirname, sshfs_dirs.src_dir)


def test_rename(sshfs_dirs: SshfsDirs) -> None:
    name1 = name_generator()
    name2 = name_generator()
    path1 = sshfs_dirs.mnt_dir / name1
    path2 = sshfs_dirs.mnt_dir / name2

    data1 = b'foo'
    with path1.open('wb', buffering=0) as fh_:
        fh_.write(data1)

    fstat1 = path1.lstat()
    path1.rename(path2)
    if sshfs_dirs.cache_timeout:
        safe_sleep(sshfs_dirs.cache_timeout)

    fstat2 = path2.lstat()

    with path2.open('rb', buffering=0) as fh_:
        data2 = fh_.read()

    for attr in ('st_mode', 'st_dev', 'st_uid', 'st_gid', 'st_size', 'st_atime_ns', 'st_mtime_ns', 'st_ino'):
        assert getattr(fstat1, attr) == getattr(fstat2, attr)
    assert getattr(fstat2, 'st_ctime_ns') >= getattr(fstat1, 'st_ctime_ns')

    assert name_in_dir(path2.name, sshfs_dirs.mnt_dir)
    assert data1 == data2


def test_link(sshfs_dirs: SshfsDirs, data_file: DataFile) -> None:
    path1 = sshfs_dirs.mnt_dir / name_generator()
    path2 = sshfs_dirs.mnt_dir / name_generator()
    shutil.copyfile(data_file.path, path1)
    assert filecmp.cmp(path1, data_file.path, False)

    fstat1 = path1.lstat()
    assert fstat1.st_nlink == 1

    path2.hardlink_to(path1)

    # The link operation changes st_ctime, and if we're unlucky
    # the kernel will keep the old value cached for path1, and
    # retrieve the new value for path2 (at least, this is the only
    # way I can explain the test failure). To avoid this problem,
    # we need to wait until the cached value has expired.
    if sshfs_dirs.cache_timeout:
        safe_sleep(sshfs_dirs.cache_timeout)

    fstat1 = path1.lstat()
    assert fstat1.st_nlink == 2

    fstat2 = path2.lstat()
    for attr in ('st_mode', 'st_dev', 'st_uid', 'st_gid', 'st_size', 'st_atime_ns', 'st_mtime_ns', 'st_ctime_ns'):
        assert getattr(fstat1, attr) == getattr(fstat2, attr)

    assert name_in_dir(path2.name, sshfs_dirs.mnt_dir)
    assert filecmp.cmp(path1, path2, False)

    path2.unlink()

    assert not name_in_dir(path2.name, sshfs_dirs.mnt_dir)
    with pytest.raises(FileNotFoundError):
        path2.lstat()

    path1.unlink()


def test_symlink(sshfs_dirs: SshfsDirs) -> None:
    linkname = name_generator()
    path = sshfs_dirs.mnt_dir / linkname
    path.symlink_to('/imaginary/dest')
    fstat = path.lstat()
    assert stat.S_ISLNK(fstat.st_mode)
    assert str(path.readlink()) == '/imaginary/dest'
    assert fstat.st_nlink == 1
    assert name_in_dir(linkname, sshfs_dirs.mnt_dir)


def test_unlink(sshfs_dirs: SshfsDirs) -> None:
    name = name_generator()
    path = sshfs_dirs.mnt_dir / name
    with (sshfs_dirs.src_dir / name).open('wb') as fh_:
        fh_.write(b'hello')
    if sshfs_dirs.cache_timeout:
        safe_sleep(sshfs_dirs.cache_timeout + 1)
    assert name_in_dir(name, sshfs_dirs.mnt_dir)
    path.unlink()
    with pytest.raises(OSError) as exc_info:
        path.lstat()
    assert exc_info.value.errno == errno.ENOENT
    assert not name_in_dir(name, sshfs_dirs.mnt_dir)
    assert not name_in_dir(name, sshfs_dirs.src_dir)


def test_open_unlink(sshfs_dirs: SshfsDirs) -> None:
    name = name_generator()
    data1 = b'foo'
    data2 = b'bar'
    path = sshfs_dirs.mnt_dir / name
    with path.open('wb+', buffering=0) as fh_:
        fh_.write(data1)
        path.unlink()
        with pytest.raises(OSError) as exc_info:
            path.lstat()
        assert exc_info.value.errno == errno.ENOENT
        assert not name_in_dir(name, sshfs_dirs.mnt_dir)
        fh_.write(data2)
        fh_.seek(0)
        assert fh_.read() == data1 + data2


def test_namemap_user(sshfs_dirs_namemap_user: SshfsDirs) -> None:
    if os.getuid() != 0:
        pytest.skip('Root required')

    name = name_generator()
    src_path = sshfs_dirs_namemap_user.src_dir / name
    src_path.mkdir()

    mnt_path = sshfs_dirs_namemap_user.mnt_dir / name
    assert mnt_path.owner() == 'root'
    assert mnt_path.group() == 'root'


def test_namemap_file(sshfs_dirs_namemap_file: SshfsDirs) -> None:
    if os.getuid() != 0:
        pytest.skip('Root required')

    name = name_generator()
    src_path = sshfs_dirs_namemap_file.src_dir / name
    src_path.mkdir()

    mnt_path = sshfs_dirs_namemap_file.mnt_dir / name
    assert mnt_path.owner() == 'foo_user'
    assert mnt_path.group() == 'bar_group'


def test_chown(sshfs_dirs_namemap_file: SshfsDirs) -> None:
    if os.getuid() != 0:
        pytest.skip('Root required')

    path = sshfs_dirs_namemap_file.mnt_dir / name_generator()
    path.mkdir()
    fstat = path.lstat()
    gid = fstat.st_gid

    pw_new = pwd.getpwnam('foo_user')

    uid_new = pw_new.pw_uid
    os.chown(path, uid_new, -1)
    fstat = path.lstat()
    assert fstat.st_uid == uid_new
    assert fstat.st_gid == gid

    gid_new = pw_new.pw_gid
    os.chown(path, -1, gid_new)
    fstat = path.lstat()
    assert fstat.st_uid == uid_new
    assert fstat.st_gid == gid_new


if __name__ == '__main__':
    sys.exit(pytest.main([__file__] + sys.argv[1:]))
