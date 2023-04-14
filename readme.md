# SSHFS
## About
This is an updated version of *SSHFS*, that supports the latest version of *SFTP*, v6. For ease of maintenance, only the latest version will be supported.

It supports *Green End SFTP Server*.

Compared to the *SFTP* spec, it does not support:

- Custom line endings.

*SSHFS* allows you to mount a remote filesystem using *SFTP*. Most *SSH* servers support and enable this *SFTP* access by default, so *SSHFS* is very simple to use - there's nothing to do on the server-side.

## How to use
Once *SSHFS* is installed (see next section) running it is very simple:

    sshfs [user@]hostname:[directory] mountpoint

It is recommended to run *SSHFS* as regular user (not as root). For this to work the mountpoint must be owned by the user. If username is omitted *SSHFS* will use the local username. If the directory is omitted, *SSHFS* will mount the (remote) home directory. If you need to enter a password *SSHFS* will ask for it (actually it just runs *SSH* which asks for the password if needed).

Also many *SSH* options can be specified (see the manual pages for *sftp(1)* and *ssh_config(5)*), including the remote port number (`-oport=PORT`)

To unmount the filesystem:

    fusermount3 -u mountpoint

On *BSD* and *macOS*, to unmount the filesystem:

    umount mountpoint

## Installation
First, download the latest *SSHFS* release. You also need [libfuse](http://github.com/libfuse/libfuse) 3.1.0 or newer (or a similar library that provides a libfuse3 compatible interface for your operating system). Finally, you need the [Glib](https://developer.gnome.org/glib/stable/) library with development headers (which should be available from your operating system's package manager).

To build and install, we recommend to use [Meson](http://mesonbuild.com/) (version 0.38 or newer) and [Ninja](https://ninja-build.org/). After extracting the sshfs tarball, create a (temporary) build directory and run Meson:

    $ mkdir build; cd build
    $ meson ..

Normally, the default build options will work fine. If you nevertheless want to adjust them, you can do so with the *mesonconf* command:

    $ mesonconf                  # list options
    $ mesonconf -D strip=true    # set an option

To build, test and install *SSHFS*, you then use *Ninja* (running the tests requires the [py.test](http://www.pytest.org/) *Python* module):

    $ ninja
    $ python -m pytest --numprocesses 10 test/
    $ sudo ninja install
