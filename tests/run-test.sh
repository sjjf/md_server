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
