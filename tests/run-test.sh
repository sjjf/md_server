#!/bin/bash
#
# Copyright 2019 Australian National University
#
# Please see the LICENSE.txt file for details.

if [ ! -f './mdserver.urls.test' ]; then
        echo "Could not find test URL list"
        exit 1
fi

if [ ! -f './mdserver.conf' ]; then
        echo "Could not find test config"
        exit 1
fi

mdserver=$(which mdserver)
if [ ! -e "$mdserver" ]; then
        echo "Could not find executable"
        exit 1
fi

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

# set up the dnsmasq directory
rm -rf ./dnsmasq
mkdir ./dnsmasq

# regex that input lines are tested against to determine if they should be
# run
filter=".*"
if [ -n "$1" ]; then
        filter="$1"
fi

now=$(date +%s)
echo "Executing server $mdserver" >"test-$now.log"
coproc "$mdserver" ./mdserver.conf &>> "test-$now.log"
sleep 5

while IFS= read -r line; do
        # apply filter
        if ! echo "$line" |grep -E "$filter" &>/dev/null; then
                continue
        fi
        method=$(echo "$line" |cut -d\; -f 1)
        path=$(echo "$line" |cut -d\; -f 2)
        data=$(echo "$line" |cut -d\; -f 3)
        echo -e "Request:\n$method $path\n" |tee -a "test-$now.log"
        echo -e "Response:" |tee -a "test-$now.log"
        # read the data from a file if required
        case $data in
                FILE=*)
                        fname=${data:5}
                        data=$(cat "$fname")
                        ;;
                *)
                        ;;
        esac

        case $method in
                GET)
                        curl -s "127.0.0.1:8001$path" |tee -a "test-$now.log"
                        ;;
                POST)
                        curl -s -d "$data" "127.0.0.1:8001$path" |tee -a "test-$now.log"
                        ;;
                *)
                        echo "Unknown request $method;$path;$data"
                        ;;
        esac

        sleep 1
        echo -e "\n------\n" |tee -a "test-$now.log"
done <./mdserver.urls.test

kill "$COPROC_PID"
