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
#  https://mcoliver.com

import os
import urllib.parse
import sqlite3

SQL = """
SELECT p.ZAUTHOR, p.ZTITLE, e.ZTITLE, e.ZASSETURL, e.ZPUBDATE
from ZMTEPISODE e
join ZMTPODCAST p
    on e.ZPODCASTUUID = p.ZUUID
where ZASSETURL NOTNULL;
"""


def check_imports():
    ''' Prompts for password to install dependencies, if needed '''
    try:
        import mutagen
    except ImportError:
        os.system(
            """osascript -e 'do shell script "/usr/bin/pip3 install mutagen" with administrator privileges'""")


def get_downloaded_episodes(db_path):
    '''Run SQL Query'''
    return sqlite3.connect(db_path).execute(SQL).fetchall()


def main(db_path):
    '''Itterate through the database and re-encode the mp3s'''
    for author, podcast, title, path, zpubdate \
            in get_downloaded_episodes(db_path):

        src_path = urllib.parse.unquote(path[len('file://'):])
        print(f"Updating: {src_path}")
        if os.path.exists(src_path):
            try:
                mp3 = MP3(src_path, ID3=EasyID3)
                if mp3.tags is None:
                    mp3.add_tags()
                mp3.tags['artist'] = author
                mp3.tags['album'] = podcast
                mp3.tags['title'] = title
                mp3.tags['genre'] = "Podcast"
                mp3.save()
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


if __name__ == "__main__":
    db_path = os.path.expanduser(
        "~/Library/Group Containers/243LU875E5.groups.com.apple.podcasts/Documents/MTLibrary.sqlite")

    check_imports()
    from mutagen.mp3 import MP3, HeaderNotFoundError
    from mutagen.easyid3 import EasyID3

    main(db_path)
