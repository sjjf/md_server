#
# Copyright 2016-2019 Australian National University
#
# Please see the LICENSE.txt file for details.

import logging
import os
import sys
import time
from datetime import datetime
from functools import wraps

import bottle
from bottle import abort
from bottle import error
from bottle import install
from bottle import request
from bottle import response
from bottle import route
from bottle import run
from bottle import template

import mdserver.config as mds_config
from mdserver.database import Database
from mdserver.dnsmasq import Dnsmasq
from mdserver.libvirt import get_domain_data
from mdserver.util import strtobool
from mdserver.util import strtobool_or_val

USERDATA_TEMPLATE = """\
#cloud-config
hostname: {{hostname}}
local-hostname: {{hostname}}
fqdn: {{hostname}}.localdomain
manage_etc_hosts: true
ssh_authorized_keys:
    - {{public_key_default}}
"""


logger = logging.getLogger("mdserver")


def early_logging():
    """Set up an early logging mechanism."""
    early_logger = logging.getLogger("early_logger")
    formatter = logging.Formatter(
        fmt="%(asctime)s EARLY: %(message)s", datefmt="%Y-%m-%d %X"
    )
    stdout_log = logging.StreamHandler()
    stdout_log.setLevel(logging.DEBUG)
    stdout_log.setFormatter(formatter)
    early_logger.setLevel(logging.DEBUG)
    early_logger.addHandler(stdout_log)


def log_to_logger(fn):
    """
    Wrap a Bottle request so that a log line is emitted after it's handled.
    (This decorator can be extended to take the desired logger as a param.)
    """

    @wraps(fn)
    def _log_to_logger(*args, **kwargs):
        request_time = datetime.now()
        actual_response = fn(*args, **kwargs)
        # modify this to log exactly what you need:
        logger.info(
            "%s %s %s %s %s",
            request.remote_addr,
            request_time,
            request.method,
            request.url,
            response.status,
        )
        return actual_response

    return _log_to_logger


class MetadataHandler(object):
    def __init__(self):
        self.default_template = USERDATA_TEMPLATE
        self.public_keys = {}

    def _set_public_keys(self, config):
        # we store the public key name here, and use tht to retrieve the
        # actual key strig from the config when it's required
        keys = [k.split(".")[1] for k in config if k.startswith("public-keys")]
        for i, k in enumerate(keys):
            self.public_keys[i] = k

    def _set_default_template(self, template_file):
        try:
            tf = open(template_file, "r")
            self.default_template = tf.read()
            tf.close()
        except IOError:
            logger.error(
                "Default template file specified (%s), but file not found!",
                template_file,
            )

    def gen_versions(self):
        client_host = bottle.request.get("REMOTE_ADDR")
        logger.debug("Getting versions for %s", client_host)
        config = bottle.request.app.config
        versions = [v + "/" for v in self._get_ec2_versions(config)]
        return self.make_content(versions)

    def gen_base(self):
        client_host = bottle.request.get("REMOTE_ADDR")
        logger.debug("Getting base for %s", client_host)

        return self.make_content(["meta-data/", "user-data"])

    def gen_metadata(self):
        client_host = bottle.request.get("REMOTE_ADDR")
        logger.debug("Getting metadata for %s", client_host)

        return self.make_content(["instance-id", "hostname", "public-keys/"])

    # See if we can find a userdata template file (which may be a plain
    # cloud-init config) in the userdata directory. Files are named
    # <userdata_dir>/<domain>, with a fallback to <userdata_dir>/<mac> if the
    # domain file isn't found. Also, since this is almost certainly going to be
    # a cloud-init config we'll search for the same with an appended .yaml
    #
    # 2023-02-02: adding support for domain metadata specifying a userdata
    # prefix to search for. If set it specifies the filename (minus suffix) to
    # look for, bypassing the normal search list.
    def _try_userdata_template(self, prefix, client_host, config):
        userdata_dir = config["mdserver.userdata_dir"]
        userdata_suffixes = config["mdserver.userdata_suffixes"]
        for sfx in userdata_suffixes.split(":"):
            name = os.path.join(userdata_dir, prefix) + sfx
            if os.path.exists(name):
                logger.debug("Found userdata for %s at %s", client_host, name)
                return name
        return None

    # we shouldn't get to this point without a valid database entry
    def _get_userdata_template(self, client_host, config):
        hostname = config["hostname"]
        db = Database(config["mdserver.db_file"])
        domain = db.query("mds_ipv4", client_host)
        # if we have the userdata prefix metadata set we fail if resolving
        # the userdata template using this doesn't work.
        ud_p = db._get_metadata(domain, "userdata_prefix")
        if ud_p is not None:
            name = self._try_userdata_template(ud_p, client_host, config)
            if name is not None:
                return open(name).read()
            logger.debug(
                "Domain specified userdata prefix %s failed for %s (%s)",
                ud_p,
                hostname,
                client_host,
            )
            abort(404, "Metadata prefix userdata not found for %s" % (client_host))

        # didn't return early
        mac = domain["mds_mac"]
        prefixes = [p for p in [hostname, mac] if p is not None]
        for prefix in prefixes:
            name = self._try_userdata_template(prefix, client_host, config)
            if name is not None:
                return open(name).read()
        return self.make_content(self.default_template)

        logger.debug("Userdata not found for %s", hostname)
        abort(404, "Userdata not found for %s" % (client_host))

    def _get_template_data(self, config):
        keys = [k.split(".")[1] for k in config if k.startswith("template-data")]
        # make sure that we can't overwrite a core config element
        for key in keys:
            if key not in config:
                config[key] = config["template-data." + key]
        if "template-data._config_items_" in config:
            # copy these items from the rest of the configuration, eliding the
            # section name - i.e. dnsmasq.prefix becomes prefix
            for item in config["template-data._config_items_"].split(","):
                if item in config:
                    key = item.split(".")[1]
                    if key not in config:
                        config[key] = config[item]
        return config

    def _get_public_keys(self, config):
        keys = [k.split(".")[1] for k in config if k.startswith("public-keys")]
        pkeys = {}
        for key in keys:
            pkeys[key] = config["public-keys." + key]
            config["public_key_" + key] = config["public-keys." + key]
        if len(pkeys) > 0:
            config["public_keys"] = pkeys
        return config

    def gen_userdata(self):
        client_host = bottle.request.get("REMOTE_ADDR")
        config = bottle.request.app.config
        logger.debug("Getting userdata for %s", client_host)
        hostname = self._get_hostname(client_host, config)
        if hostname is None:
            abort(400)
        config["hostname"] = hostname

        # Note: _get_public_keys() and _get_template_data() rewrite the
        # contents of config, hence this chain of calls.
        config = self._get_public_keys(config)
        config = self._get_template_data(config)
        if config["mdserver.password"]:
            config["mdserver_password"] = config["mdserver.password"]
        user_data_template = self._get_userdata_template(client_host, config)
        try:
            user_data = template(user_data_template, **config)
        except Exception as e:
            logger.error("Exception %s: template for %s failed?", e, hostname)
            abort(500, "Userdata templating failure for %s" % (hostname))
        if strtobool(config["mdserver.debug_userdata"]):
            udata_path = os.path.join("/tmp", client_host + ".userdata")
            with open(udata_path, "w") as udf:
                udf.write(user_data)
                logger.debug("Wrote user_data to %s", udata_path)
        return self.make_content(user_data)

    def _get_hostname(self, client_host, config):
        db = Database(config["mdserver.db_file"])
        entry = db.query("mds_ipv4", client_host)
        if entry is None:
            logger.info("Failed to find MAC for %s in database", client_host)
            return None
        return entry["domain_name"]

    def gen_hostname(self):
        client_ip = bottle.request.get("REMOTE_ADDR")
        logger.debug("Getting hostname for %s", client_ip)
        config = bottle.request.app.config
        name = self._get_hostname(client_ip, config)
        if name is None:
            abort(400, "Unknown client")
        return name

    def gen_public_keys(self):
        client_host = bottle.request.get("REMOTE_ADDR")
        logger.debug("Getting public keys for %s", client_host)

        keys = ["{}={}".format(i, k) for i, k in self.public_keys.items()]
        return self.make_content(keys)

    def gen_public_key_dir(self, key):
        client_host = bottle.request.get("REMOTE_ADDR")
        logger.debug("Getting public key directory for %s", client_host)
        res = ""
        if int(key) in self.public_keys:
            res = "openssh-key"
        elif key in self.public_keys.values():
            # technically this shouldn't work, but it doesn't hurt, I think
            res = "openssh-key"
        else:
            abort(404, "Not found")
        return self.make_content(res)

    def gen_public_key_file(self, key="default"):
        client_host = bottle.request.get("REMOTE_ADDR")
        logger.debug("Getting public key file for %s", client_host)
        # if we have one of the key indices, map it to a key name, otherwise
        # just look for the key by name
        if int(key) in self.public_keys:
            key = self.public_keys[int(key)]
        res = bottle.request.app.config["public-keys.%s" % key]
        return self.make_content(res)

    def gen_instance_id(self):
        client_host = bottle.request.get("REMOTE_ADDR")
        logger.debug("Getting instance-id for %s", client_host)
        iid = "i-%s" % client_host
        return self.make_content(iid)

    def gen_service_info(self):
        client_host = bottle.request.get("REMOTE_ADDR")
        logger.debug("Getting service info for %s", client_host)
        return self.make_content(
            [
                "name",
                "type",
                "version",
                "location",
                "ec2_versions",
            ]
        )

    def gen_service_name(self):
        client_host = bottle.request.get("REMOTE_ADDR")
        logger.debug("Getting service name for %s", client_host)
        config = bottle.request.app.config
        return self.make_content(config["service.name"])

    def gen_service_type(self):
        client_host = bottle.request.get("REMOTE_ADDR")
        logger.debug("Getting service type for %s", client_host)
        config = bottle.request.app.config
        return self.make_content(config["service.type"])

    def gen_service_location(self):
        client_host = bottle.request.get("REMOTE_ADDR")
        logger.debug("Getting service location for %s", client_host)
        config = bottle.request.app.config
        return self.make_content(config["service.location"])

    def gen_service_version(self):
        client_host = bottle.request.get("REMOTE_ADDR")
        logger.debug("Getting service version for %s", client_host)
        config = bottle.request.app.config
        vstring = "{version} ({release_date})".format(
            version=config["service.version"],
            release_date=config["service.release_date"],
        )
        return self.make_content(vstring)

    def gen_service_config(self):
        """Dump the service configuration, to support coordination with other
        services on the local node."""
        client_host = bottle.request.get("REMOTE_ADDR")
        logger.debug("Getting service config for %s", client_host)
        app = bottle.request.app
        # only allow config dump from the local host
        if client_host != app.config["mdserver.listen_address"]:
            abort(401, "access denied")
        config_strings = mds_config.dump(app)
        return self.make_content(config_strings)

    def gen_ec2_versions(self):
        client_host = bottle.request.get("REMOTE_ADDR")
        logger.debug("Getting EC2 versions for %s", client_host)
        config = bottle.request.app.config
        return self.make_content(self._get_ec2_versions(config))

    def make_content(self, res):
        # note that we only test against str here - this excludes unicode
        # strings on python2, but nothing we're doing here should be unicode
        # so we can safely ignore that
        if isinstance(res, list):
            return "\n".join(res)
        elif isinstance(res, str):
            return "%s" % res

    def _get_ec2_versions(self, config):
        vraw = config["service.ec2_versions"].split(",")
        versions = [v.strip() for v in vraw if len(v.strip()) > 0]
        return versions

    def instance_upload(self):
        client_host = bottle.request.get("REMOTE_ADDR")
        config = bottle.request.app.config
        # for whatever reason, the source address ends up being the same as
        # the listen address when connections are coming from localhost
        if client_host != config["mdserver.listen_address"]:
            abort(401, "access denied")
        data = bottle.request.body.getvalue()
        logger.debug("Got instance upload with data %s", data[0:25])
        # new default entry pre-filled with the domain data
        dbentry = get_domain_data(data, config["dnsmasq.net_name"])
        logger.info(
            "Got instance upload: %s (%s)",
            dbentry["domain_name"],
            dbentry["domain_uuid"],
        )
        # update the entry with anything that needs updating
        dbentry["last_update"] = time.time()
        dbentry["location"] = config["service.location"]
        # and actually update the database
        db = Database(config["mdserver.db_file"])
        entry = db.add_or_update_entry(dbentry)
        # if there's no ipv4 address allocated we need to fix that, and update
        # the database again
        if entry["mds_ipv4"] is None:
            entry["mds_ipv4"] = db.gen_ip(
                config["dnsmasq.net_address"],
                config["dnsmasq.net_prefix"],
                exclude=[config["dnsmasq.gateway"]],
            )
            if entry["mds_ipv4"] is None:
                logger.warning(
                    "Failed to allocate address for %s", entry["domain_name"]
                )
            db.add_or_update_entry(entry)
        db.store()
        dnsmasq = Dnsmasq(config)
        dnsmasq.gen_dhcp_hosts(db)
        dnsmasq.gen_dns_hosts(db)
        dnsmasq.hup()

    # error handlers, so we have a cleaner presentation of the common errors
    @error(400)
    def error400(error):
        client_host = bottle.request.get("REMOTE_ADDR")
        return "Unknown client: %s" % (client_host)

    @error(401)
    def error401(error):
        client_host = bottle.request.get("REMOTE_ADDR")
        return "Unauthorised client: %s" % (client_host)

    @error(404)
    def error404(error):
        body = error.body
        if body is None:
            body = bottle.request.fullpath
        return "Resource not found: %s" % (body)


def main():
    early_logging()
    elog = logging.getLogger("early_logger")
    app = bottle.default_app()
    mds_config.set_defaults(app)

    if len(sys.argv) > 1:
        config_file = sys.argv[1]
        elog.info("Loading config file: %s" % config_file)
        if os.path.isfile(config_file):
            mds_config.load(app, config_file)
    for i in app.config:
        elog.info("%s = %s" % (i, app.config[i]))

    # We're going to assume that we're running in a systemd context - this
    # means we can skip trying to double check everything. In this context
    # stdout/stderr should go into the journal, so we want to treat them the
    # same way we treat other logging targets. The only difference is that we
    # set the default loglevel to INFO rather than DEBUG, so that we have to
    # explicitly choose to spam stdout.
    #
    # However, if debug is set, then we set the stdout log level to DEBUG
    # unconditionally.
    base_level = app.config["loglevels.base"].upper()
    stream_level = app.config["loglevels.stream"].upper()
    file_level = app.config["loglevels.file"].upper()
    base_loglevel = getattr(logging, base_level)
    stream_loglevel = getattr(logging, stream_level)
    file_loglevel = getattr(logging, file_level)
    # set up the logger
    elog.info("Base loglevel: %s", base_level)
    logger.setLevel(base_loglevel)
    formatter = logging.Formatter(
        fmt="%(asctime)s %(name)s[%(levelname)s]: %(message)s", datefmt="%Y-%m-%d %X"
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setStream(sys.stdout)
    stream_handler.setLevel(stream_loglevel)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    file_handler = logging.FileHandler(app.config["mdserver.logfile"])
    file_handler.setLevel(file_loglevel)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # debug overrides all the log levels
    debug = app.config["mdserver.debug"]
    if strtobool(debug):
        # send output to stdout
        elog.info("Debug logging to all targets")
        logger.setLevel(logging.DEBUG)
        file_handler.setLevel(logging.DEBUG)
        stream_handler.setLevel(logging.DEBUG)
    else:
        # logging to a file, dump the config settings there as well
        mds_config.log(app, "mdserver")

    install(log_to_logger)

    db = Database(app.config["mdserver.db_file"])
    dnsmasq = Dnsmasq(app.config)
    dnsmasq.gen_dnsmasq_config()
    dnsmasq.gen_dhcp_hosts(db)
    dnsmasq.gen_dns_hosts(db)

    if app.config["public-keys.default"] == "__NOT_CONFIGURED__":
        logger.info("============Default public key not set !!!=============")

    mdh = MetadataHandler()

    if app.config["mdserver.default_template"]:
        mdh._set_default_template(app.config["mdserver.default_template"])

    # sanitise prefix and domain strings
    prefix = app.config["dnsmasq.prefix"]
    app.config["dnsmasq.prefix"] = strtobool_or_val(prefix)
    domain = app.config["dnsmasq.domain"]
    app.config["dnsmasq.domain"] = strtobool_or_val(domain)

    mdh._set_public_keys(app.config)

    route("/", "GET", mdh.gen_versions)
    route("/service/", "GET", mdh.gen_service_info)
    route("/service/name", "GET", mdh.gen_service_name)
    route("/service/type", "GET", mdh.gen_service_type)
    route("/service/location", "GET", mdh.gen_service_location)
    route("/service/version", "GET", mdh.gen_service_version)
    route("/service/configuration", "GET", mdh.gen_service_config)
    route("/service/ec2_versions", "GET", mdh.gen_ec2_versions)

    for md_base in mdh._get_ec2_versions(app.config):
        # skip empty strings - it makes no sense to put metadata directly
        # under /
        if len(md_base) == 0:
            continue
        # make sure the path is always properly rooted
        if len(md_base) > 0 and md_base[0] != "/":
            md_base = "/" + md_base
        route(md_base + "/", "GET", mdh.gen_base)
        route(md_base + "/meta-data/", "GET", mdh.gen_metadata)
        route(md_base + "/user-data", "GET", mdh.gen_userdata)
        route(md_base + "/meta-data/hostname", "GET", mdh.gen_hostname)
        route(md_base + "/meta-data/instance-id", "GET", mdh.gen_instance_id)
        route(md_base + "/meta-data/public-keys/", "GET", mdh.gen_public_keys)
        route(md_base + "/meta-data/public-keys/<key>/", "GET", mdh.gen_public_key_dir)
        route(
            (md_base + "/meta-data/public-keys/<key>/openssh-key"),
            "GET",
            mdh.gen_public_key_file,
        )

    # support for uploading instance data
    route("/instance-upload", "POST", mdh.instance_upload)

    svr_port = app.config.get("mdserver.port")
    listen_addr = app.config.get("mdserver.listen_address")
    run(host=listen_addr, port=svr_port)


if __name__ == "__main__":
    main()
