#!/bin/bash

if [ ! -f './mdserver.urls.test' ]; then
        echo "Could not find test URL list"
        exit 1
fi

if [ ! -f './mdserver.conf' ]; then
        echo "Could not find test config"
        exit 1
fi

if [ ! -e '../bin/mdserver' ]; then
        echo "Could not find executable"
        exit 1
fi

now=`date +%s`
echo "Executing server" >test-$now.log
coproc ../bin/mdserver ./mdserver.conf &>> test-$now.log
sleep 5

for i in `cat ./mdserver.urls.test`; do
       echo -e "Request:\n$i\n" |tee -a test-$now.log
       echo -e "Response:" |tee -a test-$now.log
       curl -s 127.0.0.1:8001$i |tee -a test-$now.log
       sleep 1
       echo -e "\n------\n" |tee -a test-$now.log
done

kill $COPROC_PID
