#!/bin/bash
set -x
if [[ $1 == "" || $2 == "" ]]; then
    echo "Usage: vm_name vm_image"
    exit 1
else
    vm_name=$1
    vm_image=$2
fi
virsh destroy $vm_name
cp ${vm_image}.1 $vm_image
virsh start $vm_name
virsh console $vm_name
