import sys
import os
import logging
import json

import bottle
from bottle import route, run, template

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)
LOG.addHandler(logging.StreamHandler())
def log_to_logger(fn):
    '''
    Wrap a Bottle request so that a log line is emitted after it's handled.
    (This decorator can be extended to take the desired logger as a param.)
    '''
    @wraps(fn)
    def _log_to_logger(*args, **kwargs):
        request_time = datetime.now()
        actual_response = fn(*args, **kwargs)
        # modify this to log exactly what you need:
        logger.info('%s %s %s %s %s' % (request.remote_addr,
                                        request_time,
                                        request.method,
                                        request.url,
                                        response.status))
        return actual_response
    return _log_to_logger


USERDATA_TEMPLATE = """\
#cloud-config
hostname: {{hostname}}
local-hostname: {{hostname}}
fqdn: {{hostname}}.localdomain
manage_etc_hosts: true
password: {{password}}
chpasswd: { expire: False }
ssh_pwauth: True
ssh_authorized_keys:
    - {{public_key_default}}
"""


class MetadataHandler(object):

    def _get_mgmt_mac(self):
        lease_file = '/var/lib/libvirt/dnsmasq/default.leases'
        client_host = bottle.request.get('REMOTE_ADDR')
        for line in open(lease_file):
            line_parts = line.split(" ")
            if client_host == line_parts[2]:
                mac = line_parts[1]
                return mac

    def _get_hostname_from_libvirt_domain(self):
        mac_addr = self._get_mgmt_mac()
        domain_mac_db = open('/etc/libvirt/qemu_db').readline()
        json_db = json.loads(domain_mac_db)
        domain_name = json_db.get(mac_addr)
        return domain_name

    def gen_metadata(self):
        res = ["instance-id",
               "hostname",
               "public-keys",
               ""]
        return self.make_content(res)

    def gen_userdata(self):
        config = bottle.request.app.config
        config['public_key_default'] = config['public-keys.default']
        config['hostname'] = self.gen_hostname().strip('\n')
        user_data = template(USERDATA_TEMPLATE, **config)
        return self.make_content(user_data)

    def gen_hostname_old(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        prefix = bottle.request.app.config['hostname-prefix']
        res = "%s-%s" % (prefix, client_host.split('.')[-1])
        return self.make_content(res)

    def gen_hostname(self):
        hostname = self._get_hostname_from_libvirt_domain()
        if not hostname:
            hostname = self.gen_hostname_old()
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

def set_logging(log_file):
    file_handler = logging.FileHandler(log_file)
    formatter = logging.Formatter('%(msg)s')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    LOG.addHandler(file_handler)


def main():
    app = bottle.default_app()
    app.config['md-base'] = "/2009-04-04"
    app.config['password'] = "password"
    app.config['hostname-prefix'] = 'vm'
    app.config['public-keys.default'] = "__NOT_CONFIGURED__"
    app.config['mdserver.port'] = 80


    if len(sys.argv) > 1:
        config_file = sys.argv[1]
        print "Loading config file: %s" % config_file
        if os.path.exists(config_file):
            app.config.load_config(config_file)
        # for i in app.config:
        #     print "%s = %s" % (i, app.config[i])
        if (sys.argv[0] == '/usr/bin/mdserver' and 
            sys.argv[1] == '/etc/mdserver/mdserver.conf'):
            log_file = '/var/log/mdserver.log'
        else:
            log_file = '/tmp/mdserver.log'

        set_logging(log_file)
        app.install(log_to_logger)

    if app.config['public-keys.default'] == "__NOT_CONFIGURED__":
        LOG.info("================Default public key not set !!!==============")

    mdh = MetadataHandler()
    route(app.config['md-base'] + '/meta-data/',
          'GET', mdh.gen_metadata)
    route(app.config['md-base'] + '/user-data',
          'GET', mdh.gen_userdata)
    route(app.config['md-base'] + '/meta-data/hostname',
          'GET', mdh.gen_hostname)
    route(app.config['md-base'] + '/meta-data/instance-id',
          'GET', mdh.gen_instance_id)
    route(app.config['md-base'] + '/meta-data/public-keys',
          'GET', mdh.gen_public_keys)
    route(app.config['md-base'] + '/meta-data/public-keys/',
          'GET', mdh.gen_public_keys)
    route(app.config['md-base'] + '/meta-data/<key>',
          'GET', mdh.gen_public_key_dir)
    route(app.config['md-base'] + '/meta-data/<key>/',
          'GET', mdh.gen_public_key_dir)
    route(app.config['md-base'] + '/meta-data/<key>/openssh-key',
          'GET', mdh.gen_public_key_file)
    route(app.config['md-base'] + '/meta-data//<key>/openssh-key',
          'GET', mdh.gen_public_key_file)
    route(app.config['md-base'] + '/meta-data/public-keys//<key>/openssh-key',
          'GET', mdh.gen_public_key_file)
    route('/latest' + '/meta-data/public-keys//<key>/openssh-key',
          'GET', mdh.gen_public_key_file)
    svr_port = app.config.get('mdserver.port')
    run(host='169.254.169.254', port=svr_port)

if __name__ == '__main__':
    main()
