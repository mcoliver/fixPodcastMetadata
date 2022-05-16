# Fix Podcasts Metadata
I sync my Garmin Forerunner 645m with Apple Podcasts to have something to listen to when I run.  Unfortunately many of the podcast shows I listen to do not have the proper metadata embeded in the mp3s cached by Apple's Podcast app.  

So on my watch I have a weird menu of nonsensical titles and episode names that make it impossible for me to locate the show I would like to listen to.  Using this script I can update the embeded metadata in the cached mp3s leveraging the data stored in Apple Podcasts sqlite database so on the next sync I get the proper experience on my watch.  

I wish Garmin would integrate this into their Garmin Express product.  It has been a problem for years.  They have the logic to do it as the Garmin Express app clearly shows the metadata for the epsidoes in the GUI.  I have reached out to a few people over at garmin via linkedin but have not heard back.

Original garmin reports [here](https://forums.garmin.com/sports-fitness/running-multisport/f/forerunner-645-645-m/208475/apple-podcasts-with-macos-10-15-catalina) and [here](https://forums.garmin.com/apps-software/mac-windows-software/f/garmin-express/281563/garmin-express-podcast-metadata-missing-causing-poor-user-experience).

# Requirements
* Python 3 ([https://www.python.org/downloads/](https://www.python.org/downloads/))
* Mutagen `pip3 install mutagen`

# Executing
You can run it from the terminal before you run Garmin Express.  Or setup a cron job so that it runs in the background every so often (say once a day) to keep things up to date.

```bash
python3 -c "$(curl -s https://raw.githubusercontent.com/mcoliver/fixPodcastMetadata/main/fixPodcastMetadata.py)"

```
