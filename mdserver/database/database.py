#
# A simple database of domain information used to manage data for the
# md_server system.
#
# The database consists of a persistent store containg some metadata about the
# database, and a list of entries recording data about domains. The list of
# entries can be searched, using indices created on specific fields; the list
# can also be iterated over. Entries can be added and updated, and the backend
# may also implement deletion of entries (though this is not required).
#
# A database can be shared between multiple mdserver instances. In this mode
# of operation all mdservers sharing the same database will generate the same
# set of DNS and DHCP configurations, allowing them to exist on a shared
# metadata network without causing confusion - however, this will more often
# be used with separate metadata networks on each host, with domains that can
# be migrated between hosts.
#
# The format of the metadata structure is:
# {
#   "initialised": <timestamp>,
#   "updated": <timestamp>,
#   "locations": {
#     "location_1": {
#       "hostname": "<hostname>",
#       "version": "<mdserver version>",
#       "first_seen": <timestamp>,
#       "last_seen": <timestamp>
#     },
#     "location_2": {
#       ...
#     }
#   },
# }
#
# The format of each entry is:
# {
#   "location": "<location name>",
#   "domain_name": "<name>",
#   "domain_uuid": "<uuid>",
#   "domain_metadata": {
#     "md_key1": "<string>",
#     "md_key2": "<string>",
#   },
#   "mds_mac": "<MAC address>",
#   "mds_ipv4": "<IPv4 address>",
#   "mds_ipv6": "<IPv6 address>",
#   "first_seen": <timestamp>,
#   "last_update": <timestamp>
# }

import ipaddress
import json
import logging
import os
import random
import time

logger = logging.getLogger("mdserver.database")


class DbFormatUnknown(Exception):
    def __init__(self, message):
        self.message = message


class Database(object):
    """A database of host and lease information.

    Contains a collection of information about hosts/instances recognised by
    the system. This information is used to generate DNS and DHCP
    configurations, manage aspects of instance metadata and userdata, and some
    aspects of instance lifecycle management.

    Multiple backend implementations can be supported, with the default being
    a single-file JSON formatted data store. Despite being single-file this
    can safely be shared across multiple mdserver instances, as long as the
    shared storage supports fcntl advisory locks. This should be the case for
    modern NFS on Linux, as well as CEPHfs.

    Database objects do not provide a persistent connection to the back end
    data store - rather, they encapsulate a single transaction against the
    data store.
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

    @classmethod
    def new_metadata(cls, locations={}):
        """Return a new metadata structure.

        Supplied keyword arguments prefill the new entry, otherwise all keys
        will be empty.
        """
        return {
            "initialised": None,
            "updated": None,
            "locations": locations,
        }

    @classmethod
    def new_location(cls, hostname=None, version=None):
        """Return a new location entry.

        Supplied keyword arguments prefill the new entry, otherwise all values
        are None.
        """
        return {
            "hostname": hostname,
            "version": version,
            "first_seen": None,
            "last_seen": None,
        }

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
    def new_db(cls, metadata={}, entries=[]):
        """Return a new database structure.

        Supplied arguments will prefill the new structure, otherwise all keys
        will be empty.
        """
        return {
            "metadata": metadata,
            "entries": entries,
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
        """Create a new entry with the current entry schema, copying values
        from the supplied entry into the new entry.

        Fields in the current schema but not the old will recive the default
        field value (typically None); fields in the old schema but not the
        current one will be dropped.
        """
        new_entry = cls.new_entry()
        for key in new_entry:
            if key in entry:
                new_entry[key] = entry[key]
        return new_entry

    @classmethod
    def _get_entry_metadata(cls, entry, key):
        """Return the metadata in the entry associated with the given key, or
        None if not found.
        """
        if key in entry["domain_metadata"]:
            return entry["domain_metadata"][key]
        return None

    def _reindex(self):
        """Recreate database indices, normally after an update."""
        raise NotImplementedError

    def _refresh_format(self):
        """Reformat each entry in the database, updating the entry schema to
        the current version.

        Note: this will drop fields which are in the old schema but not the
        new schema.
        """
        raise NotImplementedError

    def add_or_update_location(self, location, entry):
        """Add the supplied location entry to the database metadata, keyed by
        the supplied location name.

        If an entry already exists for this location, non-None fields from the
        new entry will replace the corresponding field in the existing entry.

        The `first_seen value will be set to the current time for a new entry,
        subsequent updates will never change that value. The `last_seen` field
        will always be updated to the current time.
        """
        raise NotImplementedError

    def add_or_update_entry(self, entry, id_field="domain_name"):
        """Add an entry to the database, or update a matching entry. Returns
        the updated state of the entry.

        The `id_field` in the new entry is used to search for an existing
        entry - if no match is found a new entry is created. If a match is
        found the existing entry is updated, with each non-None field in the
        new entry replacing the corresponding field value in the existing
        entry.

        The `first_seen` value will be set to the current time for a new
        entry - subsequent updates will never change that value. The
        `last_seen` value will always be set to the current time.
        """
        raise NotImplementedError

    def del_entry(self, id, id_field="domain_name"):
        """Remove an entry from the database.

        Delete entry with the specified `id`. `id_field` must be an indexed
        database key, and defaults to "domain_name".

        Implementation of this is optional.
        """
        raise NotImplementedError

    def query(self, key, needle):
        """Search the database.

        Queries are made by specifying a key and a search term. The key is one
        of the indexed database key values, and the search term is a string
        value to be searched for.
        """
        raise NotImplementedError

    def store(self):
        """Store the current state of the database to persistent storage."""
        raise NotImplementedError

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

        def store(self):
            raise NotImplementedError


# JSON-backed implementation of the Database.
#
# Data is stored on disk in a JSON file. The JSON file consists of a dict with
# two keys: `metadata` and `entries`. The `metadata` key contains information
# about the database and the mdserver instances that are managing it; the
# `entries` key consists of a simple list of entry dicts.
#
# The JSON file is read in full and parsed into a Python structure in memory.
# Operations are then performed on the in-memory data - once complete, the
# in-memory data is either dropped (when no changes were made), or serialised
# back to JSON data and written out to disk, replacing the old version of the
# database file in full.
#
# The JSON file format is a very simple serialisation of the base database
# structures:
# {
#   "metadata": { <metadata structure> },
#   "entries": [
#     { <entry1 data> },
#     { <entry2 data> },
#     ...
#     { <entryn data> },
#   ]
# }
#
class JsonDatabase(Database):
    """A simple single-file JSON database with host and lease information.

    Objects created by this class contain a Python dict storing the
    deserialised version of the JSON file contents, loaded at the time of
    creation. Operations are performed exclusively on the in-memory data,
    which persists for the life of the object. The in-memory data will be
    dropped when the object is cleaned up; it may be stored back to disk via
    `store()` method.
    """

    def __init__(self, dbfile=None):
        """Create a new in-memory database, loading the data from the specified
        database file.

        If dbfile is None, the database will be entirely transient unless an
        explicit file is passed to the `store()` method.
        """
        self.dbfile = dbfile
        if self.dbfile is None:
            dbfile = ""
        try:
            (self.db_meta, self.db_entries) = self._load_dbfile(dbfile)
        except FileNotFoundError:
            self.db_meta = self.new_metadata()
            self.db_entries = []
        except DbFormatUnknown as e:
            logger.error("Unable to load database file at %s: %s", dbfile, e.message)
            raise TypeError("Unsupported database format")
        self._refresh_format()
        self._reindex()

    def _load_dbfile(self, dbfile):
        # handle the transition from the old style plain entry list to the new
        # style metadata plus entry list
        #
        # NOTE: this is a one-way transition, the format change is not
        # backwards compatible.
        with open(dbfile, "r") as dbf:
            db = json.load(dbf)
            if isinstance(db, list):
                # old style, we need to set up a new metadata struct to
                # transition to the new format
                logger.info(
                    "Updating old-style database file at %s to new format", dbfile
                )
                md = self.new_metadata()
                return (md, db)
            elif isinstance(db, dict):
                # new style, we just need to make sure the right keys are
                # available
                if "metadata" in db and "entries" in db:
                    return (db["metadata"], db["entries"])
                # we have a dict, but not one we recognise . . .
                logger.error("Unrecognised dict database format file at %s", dbfile)
            # nothing we recognised, so throw the error upstream
            raise DbFormatUnknown("Unrecognised database format")

    def _reindex(self):
        self.indices = {}
        for key in self.index_keys:
            self.indices[key] = {
                e[key]: e for e in self.db_entries if e[key] is not None
            }

    def _refresh_format(self):
        new_core = []
        for entry in self.db_entries:
            try:
                self._check_entry(entry)
                new_core.append(entry)
            except ValueError:
                new_core.append(self._reformat_entry(entry))
        self.db_entries = new_core

    def store(self, dbfile=None):
        """Store the current state of the database to disk."""
        if not dbfile:
            # support in-memory only databases
            if self.dbfile is None:
                return
            dbfile = self.dbfile
        db = self.new_db(metadata=self.db_meta, entries=self.db_entries)
        dbtext = json.dumps(db, indent=4)
        tmpfile = dbfile + ".tmp"
        with open(tmpfile, "w") as dbf:
            dbf.write(dbtext)
        os.rename(tmpfile, dbfile)
        logger.info("Wrote %s records to %s", len(self.db_entries), self.dbfile)

    def add_or_update_location(self, name, location):
        if name in self.db_meta["locations"]:
            oe = self.db_meta["locations"][name]
            for key in location:
                if location[key] is not None and key != "first_seen":
                    oe[key] = location[key]
            logger.info("Updated location data for %s", name)
        else:
            location["first_seen"] = time.time()
            location["last_seen"] = time.time()
            self.db_meta["locations"][name] = location
            logger.info("Added location data for %s", name)

    def add_or_update_entry(self, entry, id_field="domain_name"):
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
            entry["last_seen"] = time.time()
            logger.info("Updated entry for %s (using %s)", entry[id_field], id_field)
        else:
            entry["first_seen"] = time.time()
            entry["last_seen"] = time.time()
            self.db_entries.append(entry)
            logger.info("Added entry for %s", entry[id_field])
        self._reindex()
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

    def __iter__(self):
        return self.db_entries.__iter__()
