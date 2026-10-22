#!/usr/bin/env python3
#
#  Add metadata from Apple Podcasts to cached mp3s
#  so they sync to Garmin Watches with appropriate
#  metadata
#  ---------------
#  Michael Oliver, 2022, MIT License
#
#  Standing on the shoulders of giants:
#  Modified prior art and inspiration by Douglas Watson
#  https://douglas-watson.github.io/post/2020-05_export_podcasts/
#
#  Intended for use as a cron job or to be run before Garmin Express
#
#  Queries the Apple Podcasts database for episodes that have been
#  downloaded, then updates the metadata embeded in those files
#  so that the mp3's have the correct metadata
#
#  If a Garmin watch is plugged in, the episodes are then copied to
#  it directly over MTP. Garmin Express 7.29 creates the podcast
#  folders on the watch but then fails to find them, reporting
#  "storage limit reached" or "Error sending files".
#
#  https://mcoliver.com

import importlib
import importlib.util
import os
import re
import struct
import subprocess
import sys
import time
import urllib.parse
import sqlite3

# Newer versions of Apple Podcasts store the downloaded file location
# in ZMTMEDIAENCLOSURE.ZLOCALURL instead of ZMTEPISODE.ZASSETURL
SQL = """
SELECT p.ZAUTHOR, p.ZTITLE, e.ZTITLE, m.ZLOCALURL, e.ZPUBDATE
from ZMTEPISODE e
join ZMTPODCAST p
    on e.ZPODCASTUUID = p.ZUUID
join ZMTMEDIAENCLOSURE m
    on e.ZCURRENTMEDIAENCLOSURE = m.Z_PK
where m.ZLOCALURL NOTNULL;
"""

# Older versions of Apple Podcasts
LEGACY_SQL = """
SELECT p.ZAUTHOR, p.ZTITLE, e.ZTITLE, e.ZASSETURL, e.ZPUBDATE
from ZMTEPISODE e
join ZMTPODCAST p
    on e.ZPODCASTUUID = p.ZUUID
where ZASSETURL NOTNULL;
"""

# Python module name -> pip package that provides it. libusb-package
# bundles the libusb library so nothing needs to come from Homebrew.
REQUIREMENTS = {
    "mutagen": "mutagen",
    "usb": "pyusb",
    "libusb_package": "libusb-package",
}

GARMIN_VENDOR_ID = 0x091e

# PTP/MTP container types, operations and codes used below
CONTAINER_COMMAND = 1
CONTAINER_DATA = 2
OP_OPEN_SESSION = 0x1002
OP_CLOSE_SESSION = 0x1003
OP_GET_STORAGE_IDS = 0x1004
OP_GET_STORAGE_INFO = 0x1005
OP_GET_OBJECT_HANDLES = 0x1007
OP_GET_OBJECT_INFO = 0x1008
OP_DELETE_OBJECT = 0x100B
OP_SEND_OBJECT_INFO = 0x100C
OP_SEND_OBJECT = 0x100D
OP_GET_PARTIAL_OBJECT = 0x101B
RESPONSE_OK = 0x2001
RESPONSE_SESSION_ALREADY_OPEN = 0x201E
FORMAT_FOLDER = 0x3001
FORMAT_MP3 = 0x3009
ASSOCIATION_GENERIC_FOLDER = 0x0001
ROOT_FOLDER = 0xFFFFFFFF

USB_TIMEOUT_MS = 10000
# Large enough for the watch to finish writing a file before it answers
SEND_TIMEOUT_MS = 120000
CHUNK_SIZE = 1024 * 1024


def check_imports():
    ''' Installs dependencies for the running interpreter, if needed '''
    # Install into a private folder using the same python that is running
    # this script. Avoids mismatched pip/python (e.g. Homebrew vs system)
    # and PEP 668 "externally-managed-environment" errors.
    lib_dir = os.path.expanduser(
        "~/Library/Application Support/fixPodcastMetadata/site-packages")
    sys.path.insert(0, lib_dir)
    missing = [package for module, package in REQUIREMENTS.items()
               if importlib.util.find_spec(module) is None]
    if missing:
        print(f"Installing {', '.join(missing)} (one time only)...")
        subprocess.check_call([sys.executable, "-m", "pip", "install",
                               "--quiet", "--target", lib_dir] + missing)
        importlib.invalidate_caches()


def get_downloaded_episodes(db_path):
    '''Run SQL Query'''
    conn = sqlite3.connect(db_path)
    has_enclosures = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ZMTMEDIAENCLOSURE'"
    ).fetchone()
    return conn.execute(SQL if has_enclosures else LEGACY_SQL).fetchall()


def tag_episodes(db_path):
    '''Fix the embedded metadata and return the episodes ready for the watch'''
    ready = []
    for author, podcast, title, path, zpubdate \
            in get_downloaded_episodes(db_path):

        src_path = urllib.parse.unquote(path[len('file://'):])
        print(f"Updating: {src_path}")
        if os.path.exists(src_path):
            try:
                mp3 = MP3(src_path, ID3=EasyID3)
                if mp3.tags is None:
                    mp3.add_tags()
                wanted = {'artist': author, 'album': podcast,
                          'title': title, 'genre': "Podcast"}
                # Only rewrite the file when a tag actually changes, so
                # Garmin Express doesn't see it as a new file every run
                changed = {k: v for k, v in wanted.items()
                           if v and mp3.tags.get(k) != [v]}
                if changed:
                    for key, value in changed.items():
                        mp3.tags[key] = value
                    mp3.save()
                else:
                    print("Tags already up to date.")
                ready.append((podcast, title, src_path))
            except HeaderNotFoundError:
                print(f"Corrupted file: {podcast} - {title}")
                continue
            except IsADirectoryError:
                print(
                    f"Failed to export {podcast} - {title}, media file is a movie")
                continue
            except FileNotFoundError:
                print("File does not exist. skipping")
                continue
        else:
            print (f"File does not Exist {src_path}")
    return ready


class MTPError(Exception):
    pass


class GarminWatch:
    '''Just enough of an MTP client to put podcast episodes on a watch'''

    def __init__(self, dev, interface, ep_in, ep_out):
        self.dev = dev
        self.interface = interface
        self.ep_in = ep_in
        self.ep_out = ep_out
        self.transaction_id = 0
        self.storage_id = None
        self.folders = {}
        # Garmin watches send a data header and its payload as separate
        # USB transfers and expect the same back. Detected like libmtp.
        self.split_header = False

    @classmethod
    def find(cls):
        '''Returns the first plugged in Garmin device that speaks MTP'''
        backend = libusb_package.get_libusb1_backend()
        for dev in usb.core.find(find_all=True, idVendor=GARMIN_VENDOR_ID,
                                 backend=backend):
            for config in dev:
                for intf in config:
                    if intf.bInterfaceClass not in (6, 255):
                        continue
                    bulk = [e for e in intf if usb.util.endpoint_type(
                        e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK]
                    ep_in = next((e for e in bulk if usb.util.endpoint_direction(
                        e.bEndpointAddress) == usb.util.ENDPOINT_IN), None)
                    ep_out = next((e for e in bulk if usb.util.endpoint_direction(
                        e.bEndpointAddress) == usb.util.ENDPOINT_OUT), None)
                    if ep_in and ep_out:
                        return cls(dev, intf.bInterfaceNumber, ep_in, ep_out)
        return None

    def __enter__(self):
        try:
            usb.util.claim_interface(self.dev, self.interface)
        except usb.core.USBError as e:
            raise MTPError("Could not connect to the watch. Make sure Garmin "
                           "Express is closed and try again.") from e
        code, _, _ = self.transaction(OP_OPEN_SESSION, [1])
        if code not in (RESPONSE_OK, RESPONSE_SESSION_ALREADY_OPEN):
            raise MTPError(f"The watch refused the connection (0x{code:04x})")
        storage_ids = self.unpack_array(
            self.request(OP_GET_STORAGE_IDS)[1])
        if not storage_ids:
            raise MTPError("The watch did not report any storage")
        self.storage_id = storage_ids[0]
        return self

    def __exit__(self, *exc):
        try:
            self.transaction(OP_CLOSE_SESSION)
        except (usb.core.USBError, MTPError):
            pass
        usb.util.release_interface(self.dev, self.interface)
        usb.util.dispose_resources(self.dev)

    def write(self, data, timeout=USB_TIMEOUT_MS):
        self.ep_out.write(data, timeout)
        # A transfer ending on a packet boundary needs a zero-length
        # packet so the watch knows it is complete
        if len(data) % self.ep_out.wMaxPacketSize == 0:
            self.ep_out.write(b'', timeout)

    def read_container(self, timeout=USB_TIMEOUT_MS):
        buf = b''
        while not buf:  # skip stray zero-length packets
            buf = bytes(self.ep_in.read(CHUNK_SIZE, timeout))
        length, kind, code, _ = struct.unpack('<IHHI', buf[:12])
        if len(buf) == 12 and length > 12:
            self.split_header = True
        while len(buf) < length:
            buf += bytes(self.ep_in.read(CHUNK_SIZE, timeout))
        return kind, code, buf[12:length]

    def transaction(self, op, params=(), data=None, file_path=None,
                    timeout=USB_TIMEOUT_MS):
        '''Runs one MTP operation, returning (response code, params, data)'''
        self.transaction_id += 1
        self.write(struct.pack(f'<IHHI{len(params)}I', 12 + 4 * len(params),
                               CONTAINER_COMMAND, op, self.transaction_id,
                               *params))
        if data is not None or file_path is not None:
            size = len(data) if data is not None \
                else os.path.getsize(file_path)
            header = struct.pack('<IHHI', 12 + size, CONTAINER_DATA, op,
                                 self.transaction_id)
            if self.split_header:
                self.write(header)
                header = b''
            if data is not None:
                self.write(header + data)
            else:
                self.send_file_data(header, file_path, size)

        kind, code, payload = self.read_container(timeout)
        received = None
        if kind == CONTAINER_DATA:
            received = payload
            kind, code, payload = self.read_container(timeout)
        return code, self.unpack_params(payload), received

    def request(self, op, params=(), **kwargs):
        '''Like transaction() but raises unless the watch says OK'''
        code, response, received = self.transaction(op, params, **kwargs)
        if code != RESPONSE_OK:
            raise MTPError(f"Watch returned error 0x{code:04x} for "
                           f"operation 0x{op:04x}")
        return response, received

    def send_file_data(self, header, file_path, size):
        # Every write except the last must be a whole number of packets,
        # or the watch treats the short packet as the end of the file
        with open(file_path, 'rb') as f:
            chunk = header + f.read(CHUNK_SIZE - len(header))
            while chunk:
                self.ep_out.write(chunk, SEND_TIMEOUT_MS)
                chunk = f.read(CHUNK_SIZE)
        if (len(header) + size) % self.ep_out.wMaxPacketSize == 0:
            self.ep_out.write(b'', SEND_TIMEOUT_MS)

    @staticmethod
    def unpack_params(payload):
        return list(struct.unpack(f'<{len(payload) // 4}I',
                                  payload[:len(payload) // 4 * 4]))

    @staticmethod
    def unpack_array(data):
        count = struct.unpack('<I', data[:4])[0]
        return list(struct.unpack(f'<{count}I', data[4:4 + 4 * count]))

    @staticmethod
    def pack_string(text):
        if not text:
            return b'\x00'
        encoded = (text + '\0').encode('utf-16-le')
        return struct.pack('<B', len(encoded) // 2) + encoded

    @staticmethod
    def unpack_string(data, offset):
        length = data[offset]
        raw = data[offset + 1:offset + 1 + 2 * length]
        return raw.decode('utf-16-le', errors='replace').rstrip('\0')

    def free_space(self):
        _, data = self.request(OP_GET_STORAGE_INFO, [self.storage_id])
        return struct.unpack('<HHHQQ', data[:22])[4]

    def list_folder(self, parent):
        '''Returns {lowercase name: (handle, format, size)} for a folder'''
        if parent not in self.folders:
            _, data = self.request(OP_GET_OBJECT_HANDLES,
                                   [self.storage_id, 0, parent])
            contents = {}
            for handle in self.unpack_array(data):
                _, info = self.request(OP_GET_OBJECT_INFO, [handle])
                object_format, _, size = struct.unpack('<HHI', info[4:12])
                name = self.unpack_string(info, 52)
                contents[name.lower()] = (handle, object_format, size)
            self.folders[parent] = contents
        return self.folders[parent]

    def object_info(self, name, parent, object_format, size=0):
        association = ASSOCIATION_GENERIC_FOLDER \
            if object_format == FORMAT_FOLDER else 0
        parent_field = 0 if parent == ROOT_FOLDER else parent
        return struct.pack('<IHHIHIIIIIIIHII', self.storage_id,
                           object_format, 0, size, 0, 0, 0, 0, 0, 0, 0,
                           parent_field, association, 0, 0) + \
            self.pack_string(name) + b'\x00\x00\x00'

    def folder(self, parent, name):
        '''Finds a folder by name, creating it if needed'''
        contents = self.list_folder(parent)
        existing = contents.get(name.lower())
        if existing and existing[1] == FORMAT_FOLDER:
            return existing[0]
        response, _ = self.request(
            OP_SEND_OBJECT_INFO, [self.storage_id, parent],
            data=self.object_info(name, parent, FORMAT_FOLDER))
        handle = response[2]
        contents[name.lower()] = (handle, FORMAT_FOLDER, 0)
        self.folders[handle] = {}
        return handle

    def file_status(self, parent, file_path):
        '''Returns "missing", "current" or "stale" for the watch's copy'''
        entry = self.list_folder(parent).get(
            os.path.basename(file_path).lower())
        if entry is None or entry[1] == FORMAT_FOLDER:
            return "missing"
        handle, _, size = entry
        if size != os.path.getsize(file_path):
            return "stale"
        # Retagging can keep the same file size, so compare the tags too
        local_tags = id3_tag_bytes(file_path)
        if local_tags:
            _, watch_tags = self.request(OP_GET_PARTIAL_OBJECT,
                                         [handle, 0, len(local_tags)])
            if watch_tags != local_tags:
                return "stale"
        return "current"

    def delete_file(self, parent, name):
        handle = self.list_folder(parent).pop(name.lower())[0]
        self.request(OP_DELETE_OBJECT, [handle, 0])

    def send_file(self, parent, file_path):
        name = os.path.basename(file_path)
        size = os.path.getsize(file_path)
        response, _ = self.request(
            OP_SEND_OBJECT_INFO, [self.storage_id, parent],
            data=self.object_info(name, parent, FORMAT_MP3, size))
        self.request(OP_SEND_OBJECT, file_path=file_path,
                     timeout=SEND_TIMEOUT_MS)
        self.list_folder(parent)[name.lower()] = (response[2], FORMAT_MP3,
                                                  size)


def id3_tag_bytes(file_path):
    '''Returns the raw ID3v2 tag at the start of an mp3, or b'' if none'''
    with open(file_path, 'rb') as f:
        header = f.read(10)
        if len(header) < 10 or header[:3] != b'ID3':
            return b''
        size = 0
        for byte in header[6:10]:  # syncsafe integer, 7 bits per byte
            size = size << 7 | (byte & 0x7f)
        footer = 10 if header[5] & 0x10 else 0
        return header + f.read(size + footer)


def folder_name(name):
    '''Matches the folder names Garmin Express uses on the watch'''
    return re.sub(r'[\\/:*?"<>|]', '', name or '').strip().rstrip('.') \
        or "Unknown"


def garmin_express_running():
    # Match process names, not arguments, so this script never matches itself
    return subprocess.run(["pgrep", "^Garmin Express"],
                          capture_output=True).returncode == 0


def close_garmin_express():
    '''Garmin Express holds the watch's USB connection while it is open'''
    if not garmin_express_running():
        return True
    print("\nGarmin Express is open, which stops episodes being copied "
          "to your watch.")
    if not sys.stdin.isatty():
        print("Quit Garmin Express and run this again to copy episodes "
              "to your watch.")
        return False
    answer = input("Quit Garmin Express now? [Y/n] ").strip().lower()
    if answer not in ('', 'y', 'yes'):
        print("Leaving Garmin Express open. Episodes were not copied "
              "to your watch.")
        return False
    subprocess.run(["osascript", "-e", 'quit app "Garmin Express"'],
                   capture_output=True)
    subprocess.run(["pkill", "^Garmin Express"], capture_output=True)
    for _ in range(10):
        if not garmin_express_running():
            time.sleep(2)  # give the watch a moment to be released
            return True
        time.sleep(1)
    print("Garmin Express did not close. Quit it from the menu bar and "
          "run this again.")
    return False


def copy_to_watch(episodes):
    '''Copies episodes into Podcasts/<show>/<episode>/ on the watch'''
    if not close_garmin_express():
        return
    watch = GarminWatch.find()
    if watch is None:
        print("\nNo Garmin watch found. Plug in your watch and run this "
              "again to copy episodes to it.")
        return

    print("\nCopying episodes to your watch...")
    copied = 0
    try:
        with watch:
            podcasts = watch.folder(ROOT_FOLDER, "Podcasts")
            for podcast, title, src_path in episodes:
                show = watch.folder(podcasts, folder_name(podcast))
                episode = watch.folder(show, folder_name(title))
                status = watch.file_status(episode, src_path)
                if status == "current":
                    print(f"Already on watch: {podcast} - {title}")
                    continue
                if status == "stale":
                    # e.g. copied before its metadata was fixed
                    print(f"Replacing outdated copy: {podcast} - {title}")
                    watch.delete_file(episode, os.path.basename(src_path))
                size = os.path.getsize(src_path)
                if size > watch.free_space():
                    print(f"Not enough space on watch for: {podcast} - {title}")
                    continue
                print(f"Copying ({size // 1_000_000} MB): {podcast} - {title}")
                watch.send_file(episode, src_path)
                copied += 1
    except (MTPError, usb.core.USBError) as e:
        print(f"\nStopped copying to the watch: {e}")
        print("Unplug the watch, plug it back in, and run this again.")
        return
    print(f"\nDone. Copied {copied} new episode(s). Unplug your watch "
          "to listen.")


def main(db_path):
    '''Fix the metadata on downloaded episodes, then copy them to the watch'''
    episodes = tag_episodes(db_path)
    if episodes:
        copy_to_watch(episodes)


if __name__ == "__main__":
    db_path = os.path.expanduser(
        "~/Library/Group Containers/243LU875E5.groups.com.apple.podcasts/Documents/MTLibrary.sqlite")

    check_imports()
    from mutagen.mp3 import MP3, HeaderNotFoundError
    from mutagen.easyid3 import EasyID3
    import usb.core
    import usb.util
    import libusb_package

    main(db_path)
