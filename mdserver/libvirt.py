#
# libvirt.py: extract data from libvirt and libvirt domain XML
#
# Copyright 2020 Australian National University
#
# Please see the LICENSE.txt file for details.

import xmltodict

from mdserver.database import Database
from mdserver.util import _removeprefix


def get_domain_data(domain, net):
    """Extract key data from the supplied domain XML.

    Data extracted are: the name, uuid, and MAC address from the mds network
    interface.
    """

    ddata = Database.new_entry()
    dom = xmltodict.parse(domain)
    ddata["domain_name"] = dom["domain"]["name"]
    ddata["domain_uuid"] = dom["domain"]["uuid"]
    if "metadata" in dom["domain"]:
        ddata["domain_metadata"] = {
            _removeprefix(key, "mdserver:"): dom["domain"]["metadata"][key]
            for key in dom["domain"]["metadata"]
            if key.startswith("mdserver:")
        }
    interfaces = dom["domain"]["devices"]["interface"]
    if not isinstance(interfaces, list):
        interfaces = [interfaces]
    try:
        mds_interfaces = [
            i
            for i in interfaces
            if "@network" in i["source"] and i["source"]["@network"] == net
        ]
        ddata["mds_mac"] = mds_interfaces[0]["mac"]["@address"]
    except KeyError:
        return None
    return ddata
