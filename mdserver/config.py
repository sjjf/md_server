#
# mdserver/config.py: manage md_server configuration.
#
# We use the in-built Bottle configuration mechanism (i.e. the `app.config`
# ConfigDict object created by default), setting defaults, then where a
# config file has been specified loading that using the `ConfigDict.load_file`
# method.
#
# We support loading additional files (and files from a directory), with files
# or directories to load specified in the `[_include_]` section of the main
# config file.
#

import logging
import os
import socket

import mdserver.version as version

early_logger = logging.getLogger("early_logger")


class ConfigError(Exception):
    def __init__(self, message):
        self.message = message


# old to new key mappings.
#
# Note: if we find one of the old keys, the value of that key will /always/
# override the value of the new key, regardless of whether both are set.
old_to_new = {
    "mdserver.loglevel": "loglevels.base",
}


def set_defaults(app):
    """Set the hard-coded configuration defaults for app.

    These defaults should work for very basic cases, but really need to be
    modified for a real-world deployment.
    """
    app.config["service.name"] = "mdserver"
    app.config["service.type"] = "mdserver"
    app.config["service.version"] = version.VERSION
    app.config["service.release_date"] = version.RELEASE_DATE
    app.config["service.location"] = socket.getfqdn().split(".")[0]
    app.config["service.ec2_versions"] = "2009-04-04"
    app.config["mdserver.password"] = None
    app.config["mdserver.hostname_prefix"] = "vm"
    app.config["public-keys.default"] = "__NOT_CONFIGURED__"
    app.config["mdserver.port"] = 80
    app.config["mdserver.userdata_dir"] = "/etc/mdserver/userdata"
    app.config["mdserver.userdata_suffixes"] = ":.yaml"
    app.config["mdserver.logfile"] = "/var/log/mdserver.log"
    app.config["mdserver.debug"] = "no"
    app.config["mdserver.debug_userdata"] = "no"
    app.config["mdserver.listen_address"] = "169.254.169.254"
    app.config["mdserver.default_template"] = None
    app.config["mdserver.db_file"] = "/var/lib/mdserver/db_file.json"
    app.config["loglevels.base"] = "info"
    app.config["loglevels.stream"] = "info"
    app.config["loglevels.file"] = "debug"
    app.config["dnsmasq.user"] = "mdserver"
    app.config["dnsmasq.base_dir"] = "/var/lib/mdserver/dnsmasq"
    app.config["dnsmasq.run_dir"] = "/var/run/mdserver"
    app.config["dnsmasq.net_name"] = "mds"
    app.config["dnsmasq.net_address"] = "10.122.0.0"
    app.config["dnsmasq.net_prefix"] = "16"
    app.config["dnsmasq.gateway"] = "10.122.0.1"
    app.config["dnsmasq.use_dns"] = False
    app.config["dnsmasq.interface"] = "br-mds"
    app.config["dnsmasq.listen_address"] = None
    app.config["dnsmasq.lease_len"] = 86400
    app.config["dnsmasq.prefix"] = False
    app.config["dnsmasq.domain"] = False
    app.config["dnsmasq.entry_order"] = "base"


def log(app, logname):
    """Log the contents of ConfigDict.

    Note: this gets the target logger rather than using this module's early_logger,
    so that the output goes with the caller's logging rather than stdout.
    """
    logger = logging.getLogger(logname)
    for i in app.config:
        logger.debug("%s = %s", i, app.config[i])


def _update_config(app):
    """Update the contents of the app config hash to reflect changes in the
    application configuration scheme."""
    early_logger.debug("Refreshing config schema")
    for old in old_to_new:
        if old in app.config:
            new = old_to_new[old]
            early_logger.warning("Updating config schema: %s => %s", old, new)
            app.config[new] = app.config[old]
            del app.config[old]


def dump(app):
    """Dump the contents of the running configuration to text."""
    secret_values = ["password", "public-keys", "template-data"]
    lines = []
    for i in app.config:
        for secret in secret_values:
            if secret in i:
                break
        else:
            lines.append("{key}={value}".format(key=i, value=app.config[i]))
    return lines


def load_dir(app, dirname):
    """Load the contents of all `*.conf` files found in the given directory, in
    lexical order.
    """
    early_logger.debug("Loading files from %s", dirname)
    files = []
    with os.scandir(dirname) as dir:
        for entry in dir:
            if (
                entry.is_file()
                and entry.name.endswith(".conf")
                and not entry.name.startswith(".")
            ):
                files.append(entry.name)

    files.sort()
    abs_files = [os.path.join(dirname, fname) for fname in files]
    for f in abs_files:
        early_logger.debug("Loading config from %s", f)
        app.config.load_config(f)
    app.config["_files_." + dirname] = ",".join(files)


def load_files(app, files):
    """Load the contents of the specified files."""
    for f in files:
        early_logger.debug("Loading config from %s", f)
        app.config.load_config(f)
    app.config["_files_.files"] = ",".join(files)


def load(app, filename):
    """Load configuration into app.config from the specified file.

    File is a standard ini style config file.

    Additional files and/or directories can be included via the `_include_`
    section, with a colon-separated list of files or paths specified via the
    `files` and `directories` keys, with directories read first followed by
    individually specified files. Files are read in the order specified, left
    to right; directories are searched in the order specified, left to right,
    with all `.conf` files in each directory read in lexical order. The last
    version of a particular section/key found wins.
    """

    conf_dirs = []
    conf_files = []
    if os.path.exists(filename):
        early_logger.debug("Loading config from %s", filename)
        app.config.load_config(filename)
        app.config["_files_.main"] = filename
        if "_include_.directories" in app.config:
            dirlist = app.config["_include_.directories"]
            conf_dirs = [
                os.path.abspath(d) for d in dirlist.split(":") if os.path.isdir(d)
            ]
        if "_include_.files" in app.config:
            filelist = app.config["_include_.files"]
            conf_files = [
                os.path.abspath(f) for f in filelist.split(":") if os.path.isfile(f)
            ]
        for conf_dir in conf_dirs:
            load_dir(app, conf_dir)
        load_files(app, conf_files)
    # last thing we do is refresh the config schema
    _update_config(app)
