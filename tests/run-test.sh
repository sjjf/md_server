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

# don't change these, they're built into the gitignore file
top_dir=$(realpath ../)
test_dir="$top_dir"/tests
run_dir="$test_dir"/run_tests
venv_dir="$test_dir"/.venv
log_dir="$test_dir"/logs

export top_dir test_dir run_dir venv_dir log_dir

run_start=$(date +%s)

# set up a venv to install the code in
/usr/bin/python -m venv --clear "$venv_dir"

# install the code (note that we don't activate the venv, just run stuff
# from within it)
"$venv_dir"/bin/pip install -U --upgrade-strategy eager "$top_dir"

if [ ! -f "$test_dir"/mdserver.urls.test ]; then
        echo "Could not find test URL list"
        exit 1
fi

if [ ! -f "$test_dir"/mdserver.conf.in ]; then
        echo "Could not find test config"
        exit 1
fi

# set up a fresh run directory
rm -rf "$run_dir"
mkdir -p "$run_dir"

# set up the dnsmasq dir
mkdir -p "$run_dir"/dnsmasq

# create the conf dir
export conf_dir="$run_dir"/mdserver.conf.d
mkdir -p "$conf_dir"

# create the userdata dir and copy in our test file
export udata_dir="$run_dir"/userdata
mkdir -p "$udata_dir"
cp "$test_dir"/testing.yaml "$udata_dir"/

# make sure the logs dir is available
mkdir -p "$log_dir"

mdserver=$(realpath "$venv_dir"/bin/mdserver)
if [ ! -e "$mdserver" ]; then
        echo "Could not find executable - did venv build fail?"
        exit 1
fi
# exported so that it can be used by do_mdserver
export mdserver

# fix up things if necessary
#
# fix up the dnsmasq user
dnsmasq_fixup="$conf_dir"/dnsmasq.conf
cat <<EOF >"$dnsmasq_fixup"
[dnsmasq]
base_dir = $run_dir/dnsmasq
run_dir = $run_dir/dnsmasq
listen_address = 127.1.0.1
EOF
if ! id mdserver &>/dev/null; then
        me=$(id -un)
        echo "user = $me" >> "$dnsmasq_fixup"
fi

# set the logfile to something useful
mdserver_fixup="$conf_dir"/mdserver.conf
cat <<EOF >"$mdserver_fixup"
[mdserver]
logfile = $log_dir/mdserver-$run_start.log
userdata_dir = $run_dir/userdata
db_file = $run_dir/mds_db.json
EOF

# set up the initial db file - we should get back the "test-localhost" domain
# name in our hostname queries
cat <<EOF >"$run_dir"/mds_db.json
[
    {
        "domain_name": "test-localhost",
        "domain_uuid": "7e5a544d-d555-4133-a443-8229415be723",
        "domain_metadata": {},
        "mds_mac": "52:54:00:2b:5f:00",
        "mds_ipv4": "127.1.0.2",
        "mds_ipv6": null,
        "first_seen": 1594545538.672943,
        "last_update": 1594545616.2650845
    },
    {
        "domain_name": "invalid-entry",
        "domain_uuid": "c3dfdea4-798c-4970-a308-9536ef4fc419",
        "mds_mac": "52:54:00:1a:4f:00",
        "mds_ipv4": "127.1.0.9",
        "first_seen": 1594545538.672943,
        "last_update": 1594545616.2650845
    },
    {
        "domain_name": "test1",
        "domain_uuid": "becb25c7-b581-4ecd-b60e-a9942ad18879",
        "domain_metadata": {},
        "mds_mac": "52:54:00:3a:cf:00",
        "mds_ipv4": "127.1.10.1",
        "mds_ipv6": null,
        "first_seen": 1594545538.672943,
        "last_update": 1594545616.2650845
    },
    {
        "domain_name": "test2",
        "domain_uuid": "5a70f424-4d89-4c73-a390-6217393cecb5",
        "domain_metadata": {},
        "mds_mac": "52:54:00:3b:ce:00",
        "mds_ipv4": "127.1.10.2",
        "mds_ipv6": null,
        "first_seen": 1594545538.672943,
        "last_update": 1594545616.2650845
    }
]
EOF

# create the conf file
conf_file="$run_dir"/mdserver.conf
cp "$test_dir"/mdserver.conf.in "$conf_file"
echo "directories = $conf_dir" >> "$conf_file"

# regex that input lines are tested against to determine if they should be
# run
filters=(".*")
if [ -n "$1" ]; then
        filters=("${@}")
        echo "filters: ${filters[*]}"
fi

tlog="$log_dir/test-$run_start.log"
echo "Executing server $mdserver" >"$tlog"
./do_mdserver "$conf_file" "$tlog" &

# spin on the mds.conf file
#
# Note: this would benefit from inotifywait, but I don't want to require
# something like that so instead we just spin like this . . .
retries=5
while [ ! -f "$run_dir/dnsmasq/mds.conf" ]; do
        sleep 2
        retries=$((retries - 1))
        if [ "$retries" -le 0 ]; then
                echo "dnsmasq config not created - bailing!"
                exit 1
        fi
done

# should be good to start up now
echo "Executing do_dnsmasq with $run_dir/dnsmasq/mds.conf" >"$tlog"
./do_dnsmasq "$run_dir/dnsmasq/mds.conf" "$tlog" &

# spin on the mds.pid file
retries=5
while [ ! -f "$run_dir/dnsmasq/mds.pid" ]; do
        sleep 2
        retries=$((retries - 1))
        if [ "$retries" -le 0 ]; then
                echo "dnsmasq pidfile not created - bailing!"
                exit 1
        fi
done

echo "Log file:"
echo "$tlog"
echo ""

# load the urls file and apply the filter
lines=()
for filter in "${filters[@]}"; do
        mapfile -t _lines < <(grep -E "$filter" <"$test_dir"/mdserver.urls.test)
        lines=("${lines[@]}" "${_lines[@]}")
done

for line in "${lines[@]}"; do
        read -r sip method path data < <(echo "${line//;/ }")
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
                        curl --interface "$sip" -s "127.1.0.1:8001$path" |tee -a "$tlog"
                        ;;
                POST)
                        curl --interface "$sip" -s -d "$data" "127.1.0.1:8001$path" |tee -a "$tlog"
                        ;;
                *)
                        echo "Unknown request $sip;$method;$path;$data"
                        ;;
        esac

        sleep 1
        echo -e "\n------\n" |tee -a "$tlog"
done

kill %1 %2
