# /usr/bin/env python

import unittest
from unittest.mock import patch

from mdserver.database import Database
from mdserver.libvirt import get_domain_data

# Note: this is /not/ a usable domain definition!
domxml = """
<domain type='kvm' id='7'>
  <name>test</name>
  <uuid>aecb25c7-b581-4ecd-b60e-a9942ad18879</uuid>
  <metadata xmlns:mdserver="urn:md_server:domain_metadata">
    <mdserver:userdata_prefix>testing</mdserver:userdata_prefix>
  </metadata>
  <memory unit='KiB'>8388608</memory>
  <currentMemory unit='KiB'>8388608</currentMemory>
  <vcpu placement='static'>2</vcpu>
  <resource>
    <partition>/machine</partition>
  </resource>
  <os>
    <type arch='x86_64' machine='pc-i440fx-bionic'>hvm</type>
    <boot dev='cdrom'/>
    <boot dev='hd'/>
    <bootmenu enable='yes' timeout='3000'/>
  </os>
  <features>
    <acpi/>
    <apic/>
  </features>
  <cpu mode='custom' match='exact' check='full'>
    <model fallback='forbid'>Broadwell</model>
  </cpu>
  <clock offset='utc'>
    <timer name='rtc' tickpolicy='catchup'/>
    <timer name='pit' tickpolicy='delay'/>
    <timer name='hpet' present='no'/>
  </clock>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>restart</on_crash>
  <pm>
    <suspend-to-mem enabled='no'/>
    <suspend-to-disk enabled='no'/>
  </pm>
  <devices>
    <emulator>/usr/bin/kvm-spice</emulator>
    <disk type='file' device='disk'>
      <driver name='qemu' type='raw'/>
      <source file='/var/lib/libvirt/images/test.img'/>
      <backingStore/>
      <target dev='vda' bus='virtio'/>
      <alias name='virtio-disk0'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x07' function='0x0'/>
    </disk>
    <interface type='network'>
      <mac address='52:54:00:3a:cf:41'/>
      <source network='mds' bridge='virbr0'/>
      <target dev='vnet19'/>
      <model type='virtio'/>
      <alias name='net0'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x03' function='0x0'/>
    </interface>
    <interface type='bridge'>
      <mac address='52:54:00:cf:51:b2'/>
      <source network='mgmt' bridge='brmgmt'/>
      <target dev='vnet20'/>
      <model type='virtio'/>
      <alias name='net1'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x04' function='0x0'/>
    </interface>
  </devices>
</domain>
"""  # noqa: 501

db_entry = {
    "domain_name": "test",
    "domain_uuid": "aecb25c7-b581-4ecd-b60e-a9942ad18879",
    "mds_mac": "52:54:00:3a:cf:41",
    "mds_ipv4": None,
    "mds_ipv6": None,
    "domain_metadata": {
        "userdata_prefix": "testing",
    },
    "first_seen": 1594887717,
    "last_update": 1594887717,
}


class test_all(unittest.TestCase):
    # test parsing of the domain data to get the elements we want
    def test_get_domain_data(self):
        dbentry = get_domain_data(domxml, "mds")
        self.assertEqual(dbentry["domain_name"], "test")
        self.assertEqual(dbentry["domain_uuid"], "aecb25c7-b581-4ecd-b60e-a9942ad18879")
        self.assertEqual(dbentry["mds_mac"], "52:54:00:3a:cf:41")
        self.assertEqual(dbentry["domain_metadata"]["userdata_prefix"], "testing")
        self.assertEqual(dbentry["mds_ipv4"], None)
        self.assertEqual(dbentry["mds_ipv6"], None)

    # test IP address generation and allocation
    @patch("random.seed")
    @patch("random.randrange")
    def test_ip_allocation(self, random_randrange, random_seed):
        random_seed.return_value = None
        db = Database()
        new_entry = db.add_or_update_entry(db_entry)
        self.assertEqual(new_entry["mds_ipv4"], None)
        self.assertEqual(new_entry["mds_ipv6"], None)
        random_randrange.return_value = 1500
        new_entry["mds_ipv4"] = db.gen_ip("10.122.0.0", "16", seed="seed")
        random_randrange.return_value = 1500000
        new_entry["mds_ipv6"] = db.gen_ip("2001:db8::", "32", seed="seed")
        self.assertEqual(new_entry["mds_ipv4"], "10.122.5.220")
        self.assertEqual(new_entry["mds_ipv6"], "2001:db8::16:e360")


if __name__ == "__main__":
    unittest.main()
