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

now=$(date +%s)
echo "Executing server $mdserver" >"test-$now.log"
coproc "$mdserver" ./mdserver.conf &>> "test-$now.log"
sleep 5

cat ./mdserver.urls.test | while IFS= read -r line; do
        method=$(echo "$line" |cut -d\; -f 1)
        path=$(echo "$line" |cut -d\; -f 2)
        data=$(echo "$line" |cut -d\; -f 3)
        echo -e "Request:\n$method $path\n" |tee -a "test-$now.log"
        echo -e "Response:" |tee -a "test-$now.log"
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
        done

kill "$COPROC_PID"
