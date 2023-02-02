#!/bin/bash
#
# Install system integration components.
#
# To make this more workable we assume that we have full control of the
# directories that we're installing into, and that we don't have to worry
# about malicious users - essentially speaking, anyone running this script
# does so at their own risk.
#
# We need to do several things: firstly, install a number of files into system
# level locations:
#  - config files
#  - systemd unit files
#  - libvirt hook script
#
# Secondly, we need to create the persistent data dir.
#
# Finally, we need to create the user dnsmasq will run under, and ensure that
# permissions allow this user to write to the permistent data dir (otherwise
# dnsmasq will need to run as root, which is less than ideal).
#

# defaults and overrides
#
# PREFIX is a suffix applied to all the install paths (except the new user's
# home directory, which will always be the unprefixed data dir path)
real_prefix=""
if [ -n "$PREFIX" ]; then
        real_prefix="$PREFIX"
fi

# The dnsmasq user
real_user="mdserver"
if [ -n "$MDS_DNSMASQ_USER" ]; then
        real_user="$MDS_DNSMASQ_USER"
fi

# The persistent data dir
real_ddir="/var/lib/mdserver"
if [ -n "$MDS_DATA_DIR" ]; then
        real_ddir="$MDS_DATA_DIR"
fi

# all source files are under ./etc

# create the user first
useradd -r -U -d "$real_ddir" "$real_user" &>/dev/null
err=$?
case $err in
        0|9)
                # ignore it if the user already exists
                ;;
        1|10)
                echo "Cannot update user or group files - are you root?"
                ;;
        *)
                echo "Could not create user - useradd error $err"
                ;;
esac

# mdserver config files
install -v -d -m 0750 -g "$real_user" "$real_prefix/etc/mdserver/userdata"
install -v -C -m 0750 -g "$real_user" "etc/mdserver/mdserver.conf" "$real_prefix/etc/mdserver/"
install -v -d -m 0750 -g "$real_user" "$real_prefix/etc/mdserver/mdserver.conf.d"

# libvirt hook script
install -v -d -m 0755 "$real_prefix/etc/libvirt/hooks"
install -v -C -m 0755 "etc/libvirt/qemu.hook" "$real_prefix/etc/libvirt/hooks/qemu"

# systemd unit files
install -v -C -m 0644 -t "$real_prefix/etc/systemd/system/" etc/systemd/*

# data directory
install -v -d -m 0755 "$real_ddir"
install -v -d -m 0755 -o "$real_user" -g "$real_user" "$real_ddir/dnsmasq"
