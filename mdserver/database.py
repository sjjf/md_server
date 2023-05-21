#
# mdserver/database.py: a simple single-file database of domain information
# used to manage data for the md_server system.
#
# Data is stored on disk in a simple JSON format, with data being stored as a
# list of JSON objects, each specifying domain information. In memory the data
# is stored as dict keyed by the domain name.
#
# Read operations are performed by reading the whole file, parsing the JSON,
# then creating the domain name keyed dict, along with additional indices to
# asist with queries.
#
# Update operations do the same loading and processing, but then update the
# core dict with new data. The whole core dict is then serialised back to JSON
# and written to a temp file, which is then renamed over the original file to
# complete the process.
#
# JSON format is as follows:
# [
#   {
#     "location": "hostname",
#     "domain_name": "foo",
#     "domain_uuid": "xx-xx-xx-xx-xx",
#     "domain_metadata": {
#       "md_key1": "value1",
#       "md_key2": "value2",
#     },
#     "mds_mac": "00:11:22:33:44:55",
#     "mds_ipv4": "10.128.0.2",
#     "mds_ipv6": "fe80::128:2",
#     "first_seen": 123456789,
#     "last_update": 123456799
#   }
# ]

import ipaddress
import json
import logging
import os
import random
import time

logger = logging.getLogger("mdserver.database")


class Database(object):
    """A simple database of host and lease information.

    Objects created by this class contain a Python dict storing data from a
    list of JSON objects loaded from an on-disk database file. Database objects
    are inherently ephemeral - they are created at the start of a transaction
    and are dropped at the end of the transaction, having either been queried
    or updated and stored back to disk.
    """

    # anything other than these keys are considered transient and are not
    # indexed
    index_keys = [
        "domain_name",
        "domain_uuid",
        "mds_mac",
        "mds_ipv4",
        "mds_ipv6",
    ]

    def __init__(self, dbfile=None):
        """Create a new in-memory database, loading the data from the specified
        database file.

        If dbfile is None, the database is entirely in-memory and transient.
        """
        self.dbfile = dbfile
        if self.dbfile is None:
            dbfile = ""
        try:
            with open(dbfile, "r") as dbf:
                self.db_core = json.load(dbf)
        except FileNotFoundError:
            self.db_core = []
        self._refresh_format()
        self._create_indices()

    def _create_indices(self):
        self.indices = {}
        for key in self.index_keys:
            self.indices[key] = {e[key]: e for e in self.db_core if e[key] is not None}

    def _refresh_format(self):
        new_core = []
        for entry in self.db_core:
            try:
                self._check_entry(entry)
                new_core.append(entry)
            except ValueError:
                new_core.append(self._reformat_entry(entry))
        self.db_core = new_core

    @classmethod
    def new_entry(
        cls,
        location=None,
        domain_name=None,
        domain_uuid=None,
        domain_metadata={},
        mds_mac=None,
        mds_ipv4=None,
        mds_ipv6=None,
    ):
        """Return a new database entry.

        Supplied arguments prefill the new entry, otherwise all values are
        None.
        """
        return {
            "location": location,
            "domain_name": domain_name,
            "domain_uuid": domain_uuid,
            "domain_metadata": domain_metadata,
            "mds_mac": mds_mac,
            "mds_ipv4": mds_ipv4,
            "mds_ipv6": mds_ipv6,
            "first_seen": None,
            "last_update": None,
        }

    @classmethod
    def _check_entry(cls, entry):
        """Verify that the supplied entry is the correct format."""
        new_entry = cls.new_entry()
        for key in new_entry:
            if key not in entry:
                raise ValueError("Entry missing key %s" % (key))
        for key in entry:
            if key not in new_entry:
                raise ValueError("Unknown entry key %s" % (key))

    @classmethod
    def _reformat_entry(cls, entry):
        """Update the supplied entry with any new fields set to the defaults,
        and deleted fields removed."""
        new_entry = cls.new_entry()
        for key in new_entry:
            if key in entry:
                new_entry[key] = entry[key]
        return new_entry

    @classmethod
    def _get_metadata(cls, entry, key):
        """Return the metadata in the entry associated with the given key, or
        None if not found.
        """
        if key in entry["domain_metadata"]:
            return entry["domain_metadata"][key]
        return None

    def store(self, dbfile=None):
        """Store the current state of the database to disk."""
        if not dbfile:
            dbfile = self.dbfile
            # support in-memory only databases
            if self.dbfile is None:
                return
        dbtext = json.dumps(self.db_core, indent=4)
        tmpfile = dbfile + ".tmp"
        with open(tmpfile, "w") as dbf:
            dbf.write(dbtext)
        os.rename(tmpfile, dbfile)
        logger.info("Wrote %s records to %s", len(self.db_core), self.dbfile)

    def add_or_update_entry(self, entry, id_field="domain_name"):
        """Add an entry to the database, or update when an existing entry is
        found. Returns the current state of the entry.

        Updates an existing entry with a matching `id_field` value (defaults
        to domain_name). During an update any None elements in the new entry
        will preserve the existing value for that element in the database.

        Will never update the 'first_seen' value.
        """
        # make sure the id_field is a valid index field
        if id_field not in self.index_keys:
            logger.error(
                "Invalid ID field in add_or_update_entry: %s not an index key", id_field
            )
            raise ValueError("{} is not a valid database key".format(id_field))

        self._check_entry(entry)
        if entry[id_field] in self.indices[id_field]:
            oe = self.indices[id_field][entry[id_field]]
            for key in entry:
                if entry[key] is not None and key != "first_seen":
                    oe[key] = entry[key]
            logger.info("Updated entry for %s (using %s)", entry[id_field], id_field)
        else:
            entry["first_seen"] = time.time()
            self.db_core.append(entry)
            logger.info("Added entry for %s", entry[id_field])
        self._create_indices()
        return self.query(id_field, entry[id_field])

    def del_entry(self, entry):
        """Remove an entry from the database."""
        pass

    def query(self, key, needle):
        """Search the database.

        Queries are made by specifying a key and a search term. The key is one
        of the database key values, and the search term is a simple string
        value to be searched for.
        """
        if key not in self.indices:
            raise ValueError("%s is not a valid database key" % (key))
        try:
            return self.indices[key][needle]
        except KeyError:
            return None

    def gen_ip(self, network, prefix, seed=None, exclude=[]):
        """Generate a new IP address within the specified network, excluding
        addresses from the specified exclude list. Addresses will be guaranteed
        not to exist in the current database.
        """
        random.seed(seed)
        version_keys = {
            4: "mds_ipv4",
            6: "mds_ipv6",
        }
        net = ipaddress.ip_network("%s/%s" % (network, prefix))
        ipvkey = version_keys[net.version]
        allocated_map = {a: a for a in self.indices[ipvkey]}
        for a in exclude:
            allocated_map[a] = a
        # exclude the network and broadcast addresses
        #
        # note that this isn't entirely correct for ipv6, but losing the all
        # ones address is hardly a major problem.
        allocated_map[str(net.network_address)] = str(net.network_address)
        allocated_map[str(net.broadcast_address)] = str(net.broadcast_address)
        tries = 0
        # note that this logic assumes we have addresses from exactly one
        # network, otherwise we're counting addresses from all known networks
        # against the current network
        while len(allocated_map) < net.num_addresses:
            offset = random.randrange(0, net.num_addresses)
            address = net.network_address + offset
            # test against the exclude list
            if str(address) in allocated_map:
                tries = tries + 1
                continue
            # then test against the database
            if self.query(version_keys[address.version], str(address)):
                allocated_map[str(address)] = str(address)
                tries = tries + 1
                continue
            logger.debug("Allocated %s after %d tries", address, tries)
            return str(address)
        logger.warning("No free addresses in %s network", str(net))
        return None

    def __iter__(self):
        return self.db_core.__iter__()
