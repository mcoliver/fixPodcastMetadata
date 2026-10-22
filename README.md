# Fix Podcasts Metadata
I sync my Garmin Forerunner 645m with Apple Podcasts to have something to listen to when I run.  Unfortunately many of the podcast shows I listen to do not have the proper metadata embeded in the mp3s cached by Apple's Podcast app.  

So on my watch I have a weird menu of nonsensical titles and episode names that make it impossible for me to locate the show I would like to listen to.  Using this script I can update the embeded metadata in the cached mp3s leveraging the data stored in Apple Podcasts sqlite database so on the next sync I get the proper experience on my watch.  

I wish Garmin would integrate this into their Garmin Express product.  It has been a problem for years.  They have the logic to do it as the Garmin Express app clearly shows the metadata for the epsidoes in the GUI.  I have reached out to a few people over at garmin via linkedin but have not heard back.

Original garmin reports [here](https://forums.garmin.com/sports-fitness/running-multisport/f/forerunner-645-645-m/208475/apple-podcasts-with-macos-10-15-catalina) and [here](https://forums.garmin.com/apps-software/mac-windows-software/f/garmin-express/281563/garmin-express-podcast-metadata-missing-causing-poor-user-experience).

# Copying episodes to the watch
Garmin Express 7.29 stopped syncing podcasts.  It shows "storage limit reached" or "Error sending Files" even when the watch has plenty of space.  Behind the scenes it creates the podcast folders on the watch and then can't find them again.

So if your watch is plugged in, the script now copies your downloaded episodes onto it directly, into the same `Podcasts/<show>/<episode>` folders Garmin Express uses.  Episodes already on the watch are skipped.

Garmin Express has to be closed while this happens, because it blocks other apps from talking to the watch.  If it's open, the script will offer to quit it for you.  When the script says it's done, unplug the watch and your podcasts will be there.

[Garmin Forums report](https://forums.garmin.com/apps-software/mac-windows-software/f/garmin-express/446687/garmin-express-7-29-3-mac-can-t-sync-podcasts-storage-limit-reached-or-error-sending-files-workaround-inside)

# Requirements
* Python 3 ([https://www.python.org/downloads/](https://www.python.org/downloads/))

Everything else (mutagen, pyusb and libusb) is downloaded automatically the first time you run the script.  It goes into `~/Library/Application Support/fixPodcastMetadata`, so nothing else on your Mac is changed and Homebrew isn't needed.

# Executing
1. Download the episodes you want in Apple Podcasts.
2. Plug in your watch.
3. Open Terminal and paste:

```bash
python3 -c "$(curl -s https://raw.githubusercontent.com/mcoliver/fixPodcastMetadata/main/fixPodcastMetadata.py)"
```

You can also run it as a cron job to keep the metadata up to date.  When it runs in the background and Garmin Express is open, it fixes the metadata but leaves Garmin Express alone and skips the copy.
