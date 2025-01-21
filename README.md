This is a fork/update of https://github.com/MikeGIn713/Side-Projects 

I added some error checking.<BR>
I moved the config variables to a .env file
I changed the runtime before the process ends to a week, from a day.
I added in a check for the date of the tweets it's pulling in, and if they're older than 2 days I do not use them. This fixed an issue where sometimes edited/deleted tweets caused the list to go wonky and re-send tweets to bluesky.

Next up:
tweet team press releases.
