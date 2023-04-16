# syntax = docker/dockerfile:1.2

FROM archlinux

RUN echo 'Server = https://sydney.mirror.pkgbuild.com/$repo/os/$arch' > /etc/pacman.d/mirrorlist

# `makepkg` cannot (and should not) be run as root:
RUN useradd --create-home build && \
    mkdir /etc/sudoers.d/ && \
    echo "build ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/build

# Install packages
RUN --mount=type=cache,sharing=locked,target=/var/cache/pacman \
    pacman --sync --refresh --sysupgrade --noconfirm --needed \
        base-devel \
        fakeroot \
        fuse3 \
        git \
        openssh \
        python-exceptiongroup \
        python-pytest \
        python-pytest-xdist \
        which

# Continue execution (and `CMD`) as build:
USER build
WORKDIR /home/build

# Install *Green End SFTP Server*
COPY --chown=build:wheel gesftpserver-git gesftpserver-git
RUN \
    cd gesftpserver-git/ && \
    makepkg --noconfirm --syncdeps --rmdeps --install --clean

# Setup *SSH*
USER root
RUN ssh-keygen -A
RUN echo -n "localhost " > ~/.ssh/known_hosts
RUN cat /etc/ssh/ssh_host_rsa_key.pub >> ~/.ssh/known_hosts
RUN ssh-keygen -b 1024 -t rsa -f ~/.ssh/id_rsa -P ''
RUN cat ~/.ssh/id_rsa.pub >> ~/.ssh/authorized_keys
RUN chmod 600 ~/.ssh/authorized_keys
