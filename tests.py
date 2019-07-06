# /usr/bin/env python

import unittest
from mock import MagicMock

from mdserver.server import MetadataHandler

# Note: this is /not/ a usable domain definition!
domxml = """
<domain type='kvm' id='7'>
  <name>test</name>
  <uuid>aecb25c7-b581-4ecd-b60e-a9942ad18879</uuid>
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
"""


class test_all(unittest.TestCase):

    default_filter = {
        'tag': 'source',
        'attrs': {
            'network': 'mds',
        }
    }

    empty_filter = {}

    # we need to test the xml parsing code, since this is code that isn't
    # testable in the simple test script - that script runs in an environment
    # where libvirt isn't available to provide any domain xml.
    #
    # So, the function we need to test is
    # MetadataHandler._get_domain_interfaces(), and to do that we need to mock
    # the domain object, supplying the domain.XMLDesc() return value from a
    # sample domain XML file. We'll just include the XML data as a string here,
    # for simplicity's sake.
    def test_get_domain_interfaces(self):
        domain = MagicMock()
        domain.XMLDesc = MagicMock()
        domain.XMLDesc.return_value = domxml
        mdhandler = MetadataHandler()
        interfaces1 = mdhandler._get_domain_interfaces(
            domain,
            filter=self.default_filter
        )
        interfaces2 = mdhandler._get_domain_interfaces(
            domain,
            filter=self.empty_filter
        )
        self.assertEqual(len(interfaces1), 1)
        self.assertEqual(interfaces1[0]['mac']['@address'],
                         '52:54:00:3a:cf:41')
        self.assertEqual(len(interfaces2), 2)
        self.assertEqual(interfaces2[0]['mac']['@address'],
                         '52:54:00:3a:cf:41')
        self.assertEqual(interfaces2[1]['mac']['@address'],
                         '52:54:00:cf:51:b2')


if __name__ == '__main__':
    unittest.main()
