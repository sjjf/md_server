Introduction
============

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

Setup
=====
Add the EC2 IP to the virtbr0 bridge

```
# ip addr add 169.254.169.254 dev virbr0
```

Install the following dependencies (either with pip or distro
packages):

- bottle (>= 0.12.0)
- libvirt-python (>= 1.22)
- xmltodict (>= -.11.0)

To install requirements using pip run the following:

```
# pip install -r requirements.txt
```

Build and Install the mdserver package

```
# python setup.py bdist_rpm
# rpm -ivh dist/mdserver-<version>.noarch.rpm
```

or

```
# python setup.py install
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

Usage
=====
By default most vendor supplied cloud images will run cloud-init
at boot, which will attempt to contact an EC2 metadata server on
the "magic" IP 169.254.169.254:80. mdserver will listen on this
address, and look for dnsmasq configuration based on the net_name
specified in the config (default 'mds').

mdserver responds to requests by determining the libvirt domain
based on the client IP address and looking for a userdata file
based on the instance name or MAC address, searching for the
following:

- <userdata_dir>/<instance>
- <userdata_dir>/<instance>.yaml
- <userdata_dir>/<MAC>
- <userdata_dir>/<MAC>.yaml

A default template userdata file can be also specified in the
configuration which will be used as a fallback if nothing more
specific is found, otherwise a minimal hard-coded template will
be used.

Note that more recent versions of cloud-init will complain about
the Ec2 datasource being unrecognised. Fixes for this are being
worked on, and will hopefully be submitted to the cloud-init
project at some point - until then you can either ignore the
errors, or in the worst case make some minor changes to the
systemd configuration in the images to force the network bring-up
to run, and to force cloud-init to run after that. The detailed
changes will vary based on the distribution.

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
