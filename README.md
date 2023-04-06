# Introduction

Standalone metadata server to simplify the use of vendor cloud images
with a standalone kvm/libvirt server

- supports a subset of the EC2 metadata "standard" (as documented
  by Amazon), compatible with the EC2 data source as implemented by
  cloud-init
- allows the user to configure cloud-init via user-data
- cloud-init config can be templated, making use of data from the
  system configuration as well as information about the host itself
- provides IP address and DNS management via dnsmasq, including
  generating host names from instance names and supporting DNS
  resolution of the generated names
- integrates with libvirt to automatically update records as new
  instances come online

See the sample config file for the full set of configuration options.

# Dependencies

mdserver as of 0.6.0 only supports Python3 - if you need to run with
Python2 use version 0.5.1 or later.

Package dependencies:

- bottle (>= 0.12.0)
- xmltodict (>= 0.9.0)

# Quick Start

Start by creating a libvirt network using the sample network XML
files in the distribution at `doc/mds-network.xml`:

```
# virsh net-define --file doc/mds-network.xml
```

This creates a NATed network using bridge `br-mds` with address
10.122.0.0/16, and the EC2 "magic" IP address 169.254.169.254. Any
instance that will be managed by mdserver needs to have its first
`<interface>` defined something like this:

```XML
    <interface type='network'>
      <source network='mds'/>
      <model type='virtio'/>
    </interface>
```

The MAC address assigned to this interface is the one that mdserver
adds to its database, and uses to create the DHCP configuration used
by dnsmasq.

To install requirements using pip run the following:

```
# pip3 install -r requirements.txt
```

Since mdserver is a system package it's not usefully installable
using the typical Python packaging tools - the core application can
be installed, but the additional system integration cannot. To work
around this a simple script is included to install these additional
components in default locations.

To install the core application from a source distribution:

```
# python3 setup.py install
```

Once that has been done, run the system integration script:

```
# ./system-integration.sh
```

This will install the main configuration file in
`/etc/mdserver/mdserver.conf`, the systemd unit files in
`/etc/systemd/system/`, and the libvirt hook script in
`/etc/libvirt/hooks/qemu`.

The default `mdserver.conf` file will need to be modified before the
system can do anything very useful - the file is well documented
and lists the default values for everything, so it should be easy
to adjust to your needs. You will also need to add your ssh public
keys to the config before you can ssh into instances configured via
mdserver.

User data files are sourced by default from
`/etc/mdserver/userdata`.

mdserver assumes that it's running in a systemd context, though
it doesn't strictly rely on any systemd features - in particular,
it uses systemd's support for defining relationships between units
in order to manage dnsmasq. In a non-systemd context this can
be emulated within a traditional init script, but this is not an
explicitly supported use case.

The supplied systemd unit files should work most of the time, but
will require editing if the location of the config file is changed,
or if the base dir is changed from the default `/var/lib/mdserver`.

Once set up is complete, starting the system can be done in the
expected way:

```
# systemctl start mdserver
```

This will bring up both mdserver and dnsmasq

The server can also be run manually:

```
/usr/local/bin/mdserver /etc/mdserver/mdserver.conf
```

In this case you will need to also start dnsmasq:

```
/usr/sbin/dnsmasq --conf-file=/var/lib/mdserver/dnsmasq/mds.conf --keep-in-foreground
```

In all cases you will need to ensure that the libvirt hook script
is installed in the appropriate location - typically this is
`/etc/libvirt/hooks/qemu` - and it will need to be made executable.

The libvirt hook is hard-coded with the listening address of the
mdserver process, since it needs to communicate with the mdserver
process - however you configure the mdserver to listen, it needs to
match the address in the default file.

Finally, by default logs go to `/var/log/mdserver.log`.

# Enabling cloud-init

Vendor supplied cloud images using newer versions of cloud-init will
not recognise md_server as a valid metadata source at the moment, and
will thus not even attempt to configure the instance.  This can be
worked around in two ways: either make your instance look like an AWS
instance, by setting appropriate BIOS data, or by editing the image
to force cloud-init to run after the network is up.

## Pretending to be AWS

Cloud-init determines that it's running on an AWS instance by looking
at the BIOS serial number and uuid values: they must be the same
string, and the string must start with 'ec2'. This can be achieved by
adding something like the following snippet to your domain XML file:

```XML
<os>
  ...other os data...
  <smbios mode='sysinfo'/>
</os>
<sysinfo type='smbios'>
  <system>
    <entry name='manufacturer'>Plain Old Virtual Machine</entry>
    <entry name='product'>Plain old VM</entry>
    <entry name='serial'>ec242E85-6EAB-43A9-8B73-AE498ED416A8</entry>
    <entry name='uuid'>ec242E85-6EAB-43A9-8B73-AE498ED416A8</entry>
  </system>
</sysinfo>
```

The uuid must be valid, so the easiest way to create this string is
to generate a fresh uuid and replace the first three characters with
'ec2'.

## Forcing cloud-init to run

Cloud-init can be forced to run by editing the systemd configuration
in the instance. This can be achieved by running the following
commands in the image (probably using something like guestfish):

```
# cat <<EOF >/etc/systemd/network/default.network
[Match]
Type=en*
Name=ens3

[Network]
DHCP=yes
EOF
# ln -s /lib/systemd/system/cloud-init.target /etc/systemd/system/multi-user.target.wants/
```

The network interface named in `default.network` must be on the mds
network for this to work.

# Usage

## Initialising the Database

mdserver maintains a persistent database, typically stored in
`/var/lib/mdserver/`, from which it gets the information that it
needs to respond to requests. A clean install of mdserver will have
an empty database, which must be initialised before mdserver can
respond usefully to anything.

Initialising the database is done by uploading the full domain
XML for each instance that wants to use it. The domain XML for an
instance can be acquired using the following command:

```sh
virsh dumpxml instance1 > instance1.xml
```

The resulting XML file can be uploaded to the mdserver using a simple
curl command (from the local host - access is denied from any other
IP address):

```sh
curl -s -d @instance1.xml http://169.254.169.254/instance-upload
```

The mdserver will parse the XML file, extract the information it
needs, allocate an IP address, and then store that information in
its database. It will then update the dnsmasq DHCP and DNS files so
that when the instance comes up and attempts to get on the network it
will receive a known IP address from dnsmasq, and its host name will
resolve to that IP address in a DNS lookup.

Thanks to the libvirt hook script any new instances will be uploaded
at start up, so this is a one time task (though this process can be
used to update the database if so desired).

## Request Handling

When cloud-init runs on boot it will attempt to contact an EC2
metadata server on the "magic" IP 169.254.169.254:80. mdserver
listens on this address for requests and generates a response based
on information from its database, using the source IP address of the
request to locate the host data in the database.

Most of the requests are quite simple, responding with a single line
generated from the database. However, the user-data request is far
more involved.

When mdserver receives a user-data request it starts by resolving
the instance in the database, and then searches for a file in the
userdata directory (typically `/etc/mdserver/userdata/`) using the
following filenames:

- `<userdata_dir>/<instance>`
- `<userdata_dir>/<instance>.yaml`
- `<userdata_dir>/<MAC>`
- `<userdata_dir>/<MAC>.yaml`

A default template userdata file can also be specified in the
configuration which will be used as a fallback if nothing
more specific is found - this is typically something like
`<userdata_dir>/base.yaml`. If the default template path is not set
then a minimal hard-coded template will be used instead.

Once the template to use is determined it is processed using Bottle's
Simple Template library, with details about the instance made
available to the template processor along with the following values
from the mdserver configuration:

- all public keys, in the form `public_key_<entry name>` i.e.
  an entry in the `[public-keys]` section named `default`
  will be available in the userdata template as a value named
  `public_key_default`
- all public keys in a hash keyed by the key name, under the name
  `public_keys` - i.e. the `default` key would be
  `public_keys['default']`
- a default password (`mdserver_password`) - only if set by the
  user!
- the host name (`hostname`)

Additional key-value data to be made available to the template can be
specified in the `[template-data]` section of the config file. e.g:

```ini
[template-data]
foo=bar
```

would result in `bar` being added to the template data under the key
`foo`.

In addition, the "magic" key `_config_items_` may be used to specify
a list of broader config items to be made available to the template -
this is a comma separated list of `section.key` values, each of which
will have the value copied to a top level `key` entry in the data
presented to the template. e.g.:

```ini
[template-data]
_config_items_ = 'dnsmasq.prefix,dnsmasq.domain'
```

would result in the `prefix` and `domain` settings from the `dnsmasq`
section being visible in the templates' namespace as `prefix` and
`domain`.

Any values visible to the template can be interpolated into the file
using the `{{<key>}}` syntax. More sophisticated template behaviour
can be used, including embedding arbitrary python code - see the
Bottle templating engine documentation for more details.

The output of the template processing is then returned to the client.

# DNS Management

By default mdserver will generate a DNS hosts file that dnsmasq will
read and track updates to over time. This means that if you attempt
to resolve the name of an instance through this dnsmasq instance you
will get the correct IP address, and vice-versa for A look-ups.

By default the dnsmasq DHCP configuration does not specify any DNS
servers, but it can be configured to specify the mdserver-managed
dnsmasq instance as a DNS server by adding

```ini
[dnsmasq]
use_dns=yes
```

to the mdserver configuration. Since dnsmasq acts as a forwarding
resolver this will generally work without issues, however the
reliability in any given network cannot be guaranteed.

Adding the dnsmasq instance to the hypervisor resolv.conf should also
work without issues, but again the exact details of performance and
reliability will depend on the local circumstances.
