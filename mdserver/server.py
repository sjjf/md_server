import sys
import os
import logging
import json
import libvirt
from xml.dom import minidom

import bottle
from bottle import route, run, template

LOG = logging.getLogger(__name__)
LOG.addHandler(logging.StreamHandler())


USERDATA_TEMPLATE = """\
#cloud-config
hostname: {{hostname}}
local-hostname: {{hostname}}
fqdn: {{hostname}}.localdomain
manage_etc_hosts: true
password: {{mdserver_password}}
chpasswd: { expire: False }
ssh_pwauth: True
ssh_authorized_keys:
    - {{public_key_default}}
"""


class MetadataHandler(object):

    def _get_all_domains(self):
        conn = libvirt.open()
        return conn.listAllDomains(0)

    # filters work by specifying a tag, and a set of attributes on that tag
    # which need to be matched: {'tag': 'source', 'attrs': {'network': 'mds'}}
    # matches source tags that have the network attribute set to 'mds'. Only a
    # single tag is supported, but potentially more than one attribute. An empty
    # filter means return all interfaces
    def _get_domain_interfaces(self, domain, filter={}):
        raw_xml = domain.XMLDesc(0)
        xml = minidom.parseString(raw_xml)
        interfaces = xml.getElementsByTagName('interface')
        try:
            tag = filter['tag']
            attrs = filter['attrs']
        except KeyError:
            return interfaces

        # this is a bit klunky, but the data structure we're testing is fiddly
        # to work with.
        #
        # We start by finding a node that matches the tag, and then we check
        # that the node has all the attributes that we're matching against, then
        # we check that all those attributes match the filter.
        accum = []
        for interface in interfaces:
            nodes = interface.childNodes
            for node in nodes:
                if node.nodeName == tag:
                    required = len(attrs.keys())
                    for attr in node.attributes.keys():
                        if attr in attrs:
                            if node.attributes[attr].value == attrs[attr]:
                                required -= 1
                    if required == 0:
                        accum.append(interface)
        return accum

    def _get_mac_from_interface(self, interface):
        nodes = interface.childNodes
        for node in nodes:
            if node.nodeName == 'mac':
                return node.attributes['address'].value

    def _get_domain_macs(self, network):
        macs = {}
        domains = self._get_all_domains()
        net_filter = {
            'tag': 'source',
            'attrs': {
                'network': network,
            }
        }
        for domain in domains:
            interfaces = self._get_domain_interfaces(domain, filter=net_filter)
            for interface in interfaces:
                mac = self._get_mac_from_interface(interface)
                macs[mac] = domain
        return macs

    def _get_mgmt_mac(self):
        mds_net = bottle.request.app.config['mdserver.net-name']
        # the leases/mac/whatever file is either a <net>.leases file in a
        # simple line-oriented "mac host" format, or an <interface>.status file
        # in a json format. The interface is configured in the <net>.conf file.
        client_host = bottle.request.get('REMOTE_ADDR')
        try:
            lease_file = '/var/lib/libvirt/dnsmasq/' + mds_net + '.leases'
            for line in open(lease_file):
                line_parts = line.split(" ")
                if client_host == line_parts[2]:
                    mac = line_parts[1]
                    return mac
        except IOError:
            conf_file = '/var/lib/libvirt/dnsmasq/' + mds_net + '.conf'
            interface = None
            for line in open(conf_file):
                line_parts = line.split("=")
                if "interface" == line_parts[0]:
                    interface = line_parts[1].rstrip()
            try:
                lease_file = '/var/lib/libvirt/dnsmasq/' + interface + '.status'
                status = json.load(open(lease_file))
                for host in status:
                    if host['ip-address'] == client_host:
                        return host['mac-address']
            except IOError, e:
                LOG.warning("Error reading lease file: %s" % (e))

    # We have the IP address of the remote host, and we want to convert that
    # into a domain name we can use as a hostname. This needs to go via the MAC
    # address that dnsmasq records for the IP address, since that's the only
    # identifying information we have available.
    def _get_hostname_from_libvirt_domain(self):
        mds_net = bottle.request.app.config['mdserver.net-name']
        mac_addr = self._get_mgmt_mac()
        mac_domain_mapping = self._get_domain_macs(mds_net)
        domain_name = mac_domain_mapping[mac_addr].name()
        return domain_name

    def gen_metadata(self):
        res = ["instance-id",
               "hostname",
               "public-keys",
               ""]
        return self.make_content(res)

    # See if we can find a userdata template file (which may be a plain
    # cloud-init config) in the userdata directory. Files are named
    # <userdata_dir>/<domain>, with a fallback to <userdata_dir>/<mac> if the
    # domain file isn't found. Also, since this is almost certainly going to be
    # a cloud-init config we'll search for the same with an appended .yaml
    def _get_userdata_template(self):
        userdata_dir = bottle.request.app.config['mdserver.userdata_dir']
        hostname = self.gen_hostname().rstrip()
        mac = self._get_mgmt_mac()
        name = "%s/%s" % (userdata_dir, hostname)
        if os.path.exists(name):
            return open(name).read()
        name = "%s.yaml" %(name)
        if os.path.exists(name):
            return open(name).read()
        name = "%s/%s" % (userdata_dir, mac)
        if os.path.exists(name):
            return open(name).read()
        name = "%s.yaml" % (name)
        if os.path.exists(name):
            return open(name).read()
        return USERDATA_TEMPLATE

    def gen_userdata(self):
        config = bottle.request.app.config
        config['public_key_default'] = config['public-keys.default']
        config['mdserver_password'] = config['mdserver.password']
        config['hostname'] = self.gen_hostname().strip('\n')
        user_data_template = self._get_userdata_template()
        user_data = template(user_data_template, **config)
        return self.make_content(user_data)

    def gen_hostname_old(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        prefix = bottle.request.app.config['mdserver.hostname-prefix']
        res = "%s-%s" % (prefix, client_host.split('.')[-1])
        return self.make_content(res)

    def gen_hostname(self):
        try:
            hostname = self._get_hostname_from_libvirt_domain()
        except Exception, e:
            LOG.error("Exception %s" % e)
            return self.gen_hostname_old()

        if not hostname:
            return self.gen_hostname_old()
        return hostname

    def gen_public_keys(self):
        res = bottle.request.app.config.keys()
        _keys = filter(lambda x: x.startswith('public-keys'), res)
        keys = map(lambda x: x.split('.')[1], _keys)
        keys.append("")
        return self.make_content(keys)

    def gen_public_key_dir(self, key):
        res = ""
        if key in self.gen_public_keys():
            res = "openssh-key"
        return self.make_content(res)

    def gen_public_key_file(self, key='default'):
        if key not in self.gen_public_keys():
            key = 'default'
        res = bottle.request.app.config['public-keys.%s' % key]
        return self.make_content(res)

    def gen_instance_id(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        iid = "i-%s" % client_host
        return self.make_content(iid)

    def make_content(self, res):
        if isinstance(res, list):
            return "\n".join(res)
        elif isinstance(res, basestring):
            return "%s\n" % res


def main():
    app = bottle.default_app()
    app.config['mdserver.md-base'] = "/2009-04-04"
    app.config['mdserver.password'] = "password"
    app.config['mdserver.hostname-prefix'] = 'vm'
    app.config['public-keys.default'] = "__NOT_CONFIGURED__"
    app.config['mdserver.port'] = 80
    app.config['mdserver.net-name'] = 'default'
    app.config['mdserver.loglevel'] = 'info'
    app.config['mdserver.userdata_dir'] = '/etc/mdserver/userdata'


    if len(sys.argv) > 1:
        config_file = sys.argv[1]
        print "Loading config file: %s" % config_file
        if os.path.exists(config_file):
            app.config.load_config(config_file)
        for i in app.config:
            print "%s = %s" % (i, app.config[i])

    loglevel = app.config['mdserver.loglevel']
    LOG.setLevel(getattr(logging, loglevel.upper()))

    if app.config['public-keys.default'] == "__NOT_CONFIGURED__":
        LOG.info("================Default public key not set !!!==============")

    mdh = MetadataHandler()
    route(app.config['mdserver.md-base'] + '/meta-data/',
          'GET', mdh.gen_metadata)
    route(app.config['mdserver.md-base'] + '/user-data',
          'GET', mdh.gen_userdata)
    route(app.config['mdserver.md-base'] + '/meta-data/hostname',
          'GET', mdh.gen_hostname)
    route(app.config['mdserver.md-base'] + '/meta-data/instance-id',
          'GET', mdh.gen_instance_id)
    route(app.config['mdserver.md-base'] + '/meta-data/public-keys',
          'GET', mdh.gen_public_keys)
    route(app.config['mdserver.md-base'] + '/meta-data/public-keys/',
          'GET', mdh.gen_public_keys)
    route(app.config['mdserver.md-base'] + '/meta-data/<key>',
          'GET', mdh.gen_public_key_dir)
    route(app.config['mdserver.md-base'] + '/meta-data/<key>/',
          'GET', mdh.gen_public_key_dir)
    route(app.config['mdserver.md-base'] + '/meta-data/<key>/openssh-key',
          'GET', mdh.gen_public_key_file)
    route(app.config['mdserver.md-base'] + '/meta-data//<key>/openssh-key',
          'GET', mdh.gen_public_key_file)
    route(app.config['mdserver.md-base'] + '/meta-data/public-keys//<key>/openssh-key',
          'GET', mdh.gen_public_key_file)
    route('/latest' + '/meta-data/public-keys//<key>/openssh-key',
          'GET', mdh.gen_public_key_file)
    svr_port = app.config.get('mdserver.port')
    run(host='169.254.169.254', port=svr_port)

if __name__ == '__main__':
    main()
