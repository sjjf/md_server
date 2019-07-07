# Introduction

Standalone metadata server to simplify the use of vendor cloud
images with a standalone kvm/libvirt server

- allows the user to provide a cloud-init config via userdata
- cloud-init.conf can be templated, providing access to system
  wide ssh keys as well as manually specified keys
- can manage dnsmasq additional hosts file to enable access to
  instances via their libvirt domain name rather than IP address
- supports setting hostnames, passwords and ssh keys in the
  default configuration

See the sample config file for the full set of configuration
options.

# Setup

Add the EC2 IP to the virtbr0 bridge

```
# ip addr add 169.254.169.254 dev virbr0
```

Install the following dependencies (either with pip or distro
packages):

- bottle (>= 0.12.0)
- libvirt-python (>= 1.22)
- xmltodict (>= 0.9.0)

To install requirements using pip run the following:

```
# pip install -r requirements.txt
```

Depending on your target system, you can either install directly
or by building an RPM package and installing that.

To install directly from the source:

```
# python setup.py install
```

To build an RPM package and install the package:

```
# python setup.py bdist_rpm
# rpm -ivh dist/mdserver-<version>.noarch.rpm
```

The configuration file will be installed by default in
`/etc/mdserver/mdserver.conf`, along with a daemon config file in
`/etd/default/mdserver`. The default configuration in will not be
very useful - edit it to at least add your root/admin user's ssh
key as default, if you plan to use ssh to log into your
instances.

User data files are sourced by default from
`/etc/mdserver/userdata`.

A unit file is provided for systemd based systems, along with a
SysV init script. Start the metadata server as per usual:

```
# systemctl start mdserver
```

or

```
# /etc/init.d/mdserver start
```

The server can also be run manually:

```
/usr/local/bin/mdserver /etc/mdserver/mdserver.conf
```

Logs by default go to `/var/log/mdserver.log`.

# Enabling cloud-init

Vendor supplied cloud images using newer versions of cloud-init
will not recognise md_server as a valid metadata source at the
moment, and will thus not even attempt to configure the instance.
This can be worked around in two ways: either make your instance
look like an AWS instance, by setting appropriate BIOS data, or
by editing the image to force cloud-init to run after the network
is up.

## Pretending to be AWS
Cloud-init determines that it's running on an AWS instance by
looking at the BIOS serial number and uuid values: they must be
the same string, and the string must start with 'ec2'. This can
be achieved by adding something like the following snippet to
your domain XML file:

```
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

The uuid must be valid, so the easiest way to create this string
is to generate a fresh uuid and replace the first three characters
with 'ec2'.

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

The network interface named in `default.network` must be on the
mds network for this to work.

# Usage

However you get cloud-init to run, it will attempt to contact an
EC2 metadata server on the "magic" IP 169.254.169.254:80. mdserver
listens on this address, and looks for dnsmasq configuration
based on the net_name specified in the config (default 'mds').

mdserver responds to requests by determining the libvirt domain
based on the client IP address and looking for a userdata file
based on the instance name or MAC address, searching for the
following:

- `<userdata_dir>/<instance>`
- `<userdata_dir>/<instance>.yaml`
- `<userdata_dir>/<MAC>`
- `<userdata_dir>/<MAC>.yaml`

A default template userdata file can be also specified in the
configuration which will be used as a fallback if nothing more
specific is found - this is typically something like
`<userdata_dir>/base.yaml`. If all else failse a minimal
hard-coded template will be used.

Userdata files are run through Bottle's templating engine,
allowing the user to substitute a number of values from the
mdserver configuration into the generated userdata file. The
currently supported values are:

- all public keys, in the form `public_key_<entry name>`
  i.e. an entry in the `[public-keys]` section named `default` will
  be available in the userdata template as a value named
  `public_key_default`
- a default password (`mdserver_password`) - only if set by the
  user!
- the hostname (`hostname`)

Additional key-value data to be made available to the template
can be specified in the `[template-data]` section of the config
file. e.g:

```
[template-data]
foo=bar
```

would result in `bar` being added to the template data under the
key `foo`.

Values can be interpolated into the file using the `{{<key>}}`
syntax - more sophisticated template behaviour can be used, see
the Bottle templating engine documentation for more details.

mdserver can be configured to manage the dnsmasq additional hosts
file for the metadata network. Adding the EC2 magic IP address to
the server's resolv.conf file will allow users to ssh to the
instance by name rather than IP address, greatly simplifying
usage. This functionality is disabled by default, but can be
enabled by setting the `manage_addnhosts` config entry to true.
When enabled, mdserver will create a DNS entry for the instance
IP address pointing at the instance's libvirt domain name.

Additionally, setting the `dnsmasq.prefix` config entry will cause
mdserver to add a DNS entry in the form `<prefix><name>` (i.e.
`test-vm145`; setting the `dnsmasq.domain` config entry will cause
it to add an entry for the fully qualified domain name (i.e.
`test-vm145.example.com`, or `vm145.example.com` if the prefix is
not set). This behaviour is disabled by default.
