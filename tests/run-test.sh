#!/bin/bash
#
# Copyright 2019 Australian National University
#
# Please see the LICENSE.txt file for details.
#
# This implements a simple set of funciontal tests, consisting of a set of
# queries that will be run against a test instance of the server. The output
# from the results needs to be matched against the expected output (not yet
# implemented).
#
# The code is installed in a venv (created here, and recreated with every
# run), and then run directly from there. Configuration is done via the
# `mdserver.conf` file in this directory, with any necessary adjustments made
# via additional files in the `mdserver.conf.d/` directory (this can include
# adjustments to things like the user that dnsmasq is configured to run as).

if [ -n "$DEBUG" ]; then
        set -x
fi

run_start=$(date +%s)

# set up a venv to install the code in
/usr/bin/python -m venv --clear .venv

# install the code (note that we don't activate the venv, just run stuff
# from within it)
./.venv/bin/pip install -U --upgrade-strategy eager ../

if [ ! -f './mdserver.urls.test' ]; then
        echo "Could not find test URL list"
        exit 1
fi

if [ ! -f './mdserver.conf' ]; then
        echo "Could not find test config"
        exit 1
fi

# recreate the conf dir
rm -rf ./mdserver.conf.d
mkdir -p ./mdserver.conf.d

# make sure the logs dir is available
mkdir -p ./logs

mdserver=$(realpath ".venv/bin/mdserver")
if [ ! -e "$mdserver" ]; then
        echo "Could not find executable - did venv build fail?"
        exit 1
fi

# fix up things if necessary
#
# fix up the dnsmasq user
dnsmasq_fixup="./mdserver.conf.d/dnsmasq.conf"
echo "[dnsmasq]" > "$dnsmasq_fixup"
if ! id mdserver &>/dev/null; then
        me=$(id -un)
        echo "user = $me" >> "$dnsmasq_fixup"
fi

# set the logfile to something useful
mdserver_fixup="./mdserver.conf.d/mdserver.conf"
echo "[mdserver]" > "$mdserver_fixup"
echo "logfile = ./logs/mdserver-$run_start.log" >> "$mdserver_fixup"

# set up the initial db file - we should get back the "test-localhost" domain
# name in our hostname queries
cat <<EOF >./mds_db.json
[
    {
        "domain_name": "test-localhost",
        "domain_uuid": "7e5a544d-d555-4133-a443-8229415be723",
        "mds_mac": "52:54:00:2b:5f:63",
        "mds_ipv4": "127.0.0.1",
        "mds_ipv6": null,
        "first_seen": 1594545538.672943,
        "last_update": 1594545616.2650845
    }
]
EOF

# set up a fresh run directory
rm -rf ./run_tests
mkdir -p ./run_tests/dnsmasq

# regex that input lines are tested against to determine if they should be
# run
filter=".*"
if [ -n "$1" ]; then
        filter="$1"
fi

tlog="./logs/test-$run_start.log"
echo "Executing server $mdserver" >"$tlog"
coproc "$mdserver" ./mdserver.conf &>>"$tlog"
sleep 5

# load the urls file and apply the filter
mapfile -t lines < <(grep -E "$filter" <./mdserver.urls.test)
for line in "${lines[@]}"; do
        read -r method path data < <(echo "${line//;/ }")
        echo -e "Request:\n$method $path\n" |tee -a "$tlog"
        echo -e "Response:" |tee -a "$tlog"
        # read the data from a file if required
        case $data in
                FILE=*)
                        fname=${data#*=}
                        data=$(cat "$fname")
                        ;;
                *)
                        ;;
        esac

        case $method in
                GET)
                        curl -s "127.0.0.1:8001$path" |tee -a "$tlog"
                        ;;
                POST)
                        curl -s -d "$data" "127.0.0.1:8001$path" |tee -a "$tlog"
                        ;;
                *)
                        echo "Unknown request $method;$path;$data"
                        ;;
        esac

        sleep 1
        echo -e "\n------\n" |tee -a "$tlog"
done

kill %1
