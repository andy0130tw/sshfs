#!/bin/sh

set -o xtrace
set -e

export DOCKER_HOST="unix://${XDG_RUNTIME_DIR}/docker.sock"

container_name=sshfs_container
image_name=sshfs_image
dir=$(dirname "$0")

docker container stop $container_name || true
docker container rm $container_name || true

docker container run \
    --detach \
    --rm \
    --interactive \
    --name $container_name \
    --mount type=bind,source="${dir}",target=/tmp/sshfs/test/ \
    --cap-add SYS_ADMIN \
    --device /dev/fuse \
    $image_name

docker exec $container_name groupadd bar_group
docker exec $container_name useradd foo_user --no-user-group --gid bar_group

docker exec $container_name mkdir /tmp/sshfs/build/
docker cp $dir/../build/sshfs $container_name:/tmp/sshfs/build/

docker exec $container_name sh -c '/usr/bin/sshd -Dp 22 &'
docker exec --workdir /tmp/sshfs/ $container_name python -m pytest --numprocesses 10 test/

docker container stop $container_name
