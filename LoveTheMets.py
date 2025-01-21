#########################################################################################
##                                                                                      
##   Script Name:   LoveTheMetsBot.py                              	                
##   Author:        Michael Donato (ceetar.bsky.social)                               
##   Creation Date: 20241106                                                        
##   Last Update:   20250121                                                      
##   Description:   Bluesky Bot to aggregate some useful Mets tweets
##
##		    Based off TwitterToBlueskyBot.py by Mike Giambra (mikegin713.bsky.social)  
##		    This script was built to run in a Python3 environment using         
##                  Bluesky development protocols (free) and RapidAPI (not free) to     
##                  mirror tweets to Bluesky.  It is designed to run in a standalone    
##                  manner (no database or other dependencies) and such that when it is 
##                  restarted it can pick up where it left off.                         
##                  Most of the customizable components are in the "User-defined        
##                  variables" section.    	






##Imports - Some of these may be extraneous, but there's little harm in keeping them.  Some libraries need to be downloaded using pip
import requests
from datetime import datetime, timedelta, timezone
import time
import re
import os
from atproto import Client, models
import json
import logging
import httpx
import typing as t
import smtplib
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

##User-defined variables
moduleName = str(os.getenv('moduleName'))  ## Used in the Subject line of status emails
gmailPW = str(os.getenv('gmailPW'))  ## This app uses gmail to send status alerts.  Set up an App Password in Google and copy the result here
gmailUser = str(os.getenv('gmailUser')) ## The email address associated with the google account
logFileName = str(os.getenv('logFileName')) ## The name of the log file to post status messages.  Useful for troubleshooting
blueskyHandle = str(os.getenv('blueskyHandle')) ## This is the full bluesky handle (e.g. myhandle.bsky.social)
blueskyPassword = str(os.getenv('blueskyPassword')) ## Create and use a so-called "app password" and post the result here
twitterListId = str(os.getenv('twitterListId')) ## This is a string variable that should correspond to the Twitter List's ID
rapidApiKey = str(os.getenv('rapidApiKey')) ## The so-called API Key which serves as your authentication string for any calls to the RapidAPI service
metspress = str(os.getenv('metspress'))
## Created this function to send alerts when the bot starts or stops.
## This is useful to understand when forced restarts happen as well as to identify when the process hangs
def emailStatus(message):
    msg = MIMEText(message)
    msg['Subject'] = "Email from " + moduleName
    msg['From'] = moduleName+ ' <blueskybot@gmail.com>'
    msg['To'] = str(gmailUser)

    server = smtplib.SMTP("smtp.gmail.com:587")
    server.starttls()
    server.login(gmailUser, gmailPW)
    server.sendmail(gmailUser, gmailUser, msg.as_string())

    #And to close server connection
    server.quit()

## Prepare the log file
logging.basicConfig(filename=logFileName,level=logging.DEBUG)
f = open(logFileName, "a")
f.truncate(100)
f.close()


_META_PATTERN = re.compile(r'<meta property="og:.*?>')
_CONTENT_PATTERN = re.compile(r'<meta[^>]+content="([^"]+)"')


## Helper function...
def _find_tag(og_tags: t.List[str], search_tag: str) -> t.Optional[str]:
    for tag in og_tags:
        if search_tag in tag:
            return tag

    return None


## Helper function...
def _get_tag_content(tag: str) -> t.Optional[str]:
    match = _CONTENT_PATTERN.match(tag)
    if match:
        return match.group(1)

    return None


## Helper function...
def _get_og_tag_value(og_tags: t.List[str], tag_name: str) -> t.Optional[str]:
    tag = _find_tag(og_tags, tag_name)
    if tag:
        return _get_tag_content(tag)

    return None


## Helper function...
def get_og_tags(url: str) -> t.Tuple[t.Optional[str], t.Optional[str], t.Optional[str]]:
    client = httpx.Client()
    request = client.build_request("GET", url)
    while request is not None:
        response = client.send(request)
        request = response.next_request
        
    og_tags = _META_PATTERN.findall(response.text)

    og_image = _get_og_tag_value(og_tags, 'og:image')
    og_title = _get_og_tag_value(og_tags, 'og:title')
    og_description = _get_og_tag_value(og_tags, 'og:description')

    return og_image, og_title, og_description

## Helper function...
def extract_url_byte_positions(text, *, aggressive: bool, encoding='UTF-8'):
    encoded_text = text.encode(encoding)

    if aggressive:
        pattern = rb'(?:[\w+]+\:\/\/)?(?:[\w\d-]+\.)*[\w-]+[\.\:]\w+\/?(?:[\/\?\=\&\#\.]?[\w-]+)+\/?'
    else:
        pattern = rb'https?\:\/\/(?:[\w\d-]+\.)*[\w-]+[\.\:]\w+\/?(?:[\/\?\=\&\#\.]?[\w-]+)+\/?'

    matches = re.finditer(pattern, encoded_text)
    url_byte_positions = []
    for match in matches:
        url_bytes = match.group(0)
        url = url_bytes.decode(encoding)
        url_byte_positions.append((url, match.start(), match.end()))

    return url_byte_positions

## This function below handles all posting to Bluesky.
def postToBluesky(message, backup_img_url, client):
    url_positions = extract_url_byte_positions(message, aggressive=False)
    facets = []
    embed_external = None
    uri = ''
    overage = ''

    for link_data in url_positions:
        uri, byte_start, byte_end = link_data
        facets.append(
            models.AppBskyRichtextFacet.Main(
                features=[models.AppBskyRichtextFacet.Link(uri=uri)],
                index=models.AppBskyRichtextFacet.ByteSlice(byte_start=byte_start, byte_end=byte_end),
            )
        )

    # AT requires URL to include http or https when creating the facet. Appends to URL if not present
    for link in url_positions:
        tempuri = link[0] if link[0].startswith('http') else f'https://{link[0]}'
        uri = tempuri

        ##decode uri
        response = requests.get(tempuri, allow_redirects = True)
        if response.history:
            for resp in response.history:  ##Loop until you get the final URL; using aliases leads to issues when retrieving images and video
                print(resp.url)
                uri = resp.url
        print('Original=' + tempuri);
        print('New=' + uri);

        img_url, title, description = get_og_tags(uri)
        ##if title is None or description is None:
        ##    raise ValueError('Required Open Graph Protocol (OGP) tags not found')

        thumb_blob = None
        if img_url:
            # Download image from og:image url and upload it as a blob
            img_data = httpx.get(img_url).content
            thumb_blob = client.upload_blob(img_data).blob

        if (not img_url) and backup_img_url:
            # Download image from og:image url and upload it as a blob
            img_data = httpx.get(backup_img_url).content
            thumb_blob = client.upload_blob(img_data).blob

        if len(message) > 300:
            iCutoff = message.rfind(' ', 1, 300)
            overage = message[iCutoff:]
            message = message[:iCutoff]

        embed_external = models.AppBskyEmbedExternal.Main(
            external=models.AppBskyEmbedExternal.External(
                title='Tweet',
                description=message,
                uri=uri,
                thumb=thumb_blob
            )
        )

    ##If no link, this still needs to be done.  Divide longer tweets into replies.
    if len(message) > 300:
        iCutoff = message.rfind(' ', 1, 300)
        overage = message[iCutoff:]
        message = message[1:iCutoff]

    ## CREATE THE ORIGINAL POST.  Any overages will be posted as replies to this post
    ##Only use embed if there's a valid uri
    resp = client.com.atproto.repo.create_record(
        models.ComAtprotoRepoCreateRecord.Data(
            repo=client.me.did,
            collection=models.ids.AppBskyFeedPost,
            record=models.AppBskyFeedPost.Record(created_at=client.get_current_time_iso(), text=message, embed=embed_external, facets=facets),
        )
    )

    ## This next section creates the replies using the overage text.
    ## Post segments sometimes get cut off for longer tweets.  Feel free to fix it and report back!
    strong_resp = models.utils.create_strong_ref(resp)
    parent_resp = strong_resp
    while len(overage) > 0:
        url_positions = extract_url_byte_positions(overage, aggressive=False)
        reply_facets = []

        for link_data in url_positions:
            uri, byte_start, byte_end = link_data
            reply_facets.append(
                models.AppBskyRichtextFacet.Main(
                    features=[models.AppBskyRichtextFacet.Link(uri=uri)],
                    index=models.AppBskyRichtextFacet.ByteSlice(byte_start=byte_start, byte_end=byte_end),
                )
            )
        if len(overage) > 300:
            iCutoff = overage.rfind(' ', 1, 300)
            resp = client.send_post(
                text=overage[1:iCutoff],
                reply_to=models.AppBskyFeedPost.ReplyRef(root=strong_resp, parent=parent_resp), facets=reply_facets,
            )
            parent_resp = models.utils.create_strong_ref(resp)
            overage = overage[iCutoff:]
        else:
            resp = client.send_post(
                text=overage,
                reply_to=models.AppBskyFeedPost.ReplyRef(root=strong_resp, parent=parent_resp), facets=reply_facets,
            )
            parent_resp = models.utils.create_strong_ref(resp)
            overage = ''

##Beginning of executable code. 
myDateTime = datetime.today().strftime('%Y-%m-%d %H:%M:%S')
emailStatus(moduleName + ' started at ' + myDateTime)

##Bluesky prefers that Client sessions are created once and kept alive as long as possible.  Connecting too many times in a 24-hour
## period will result in rate limits that keep you from posting for up to a day.
client = Client()
print(f"{blueskyHandle}hi{blueskyPassword} ")
profile = client.login(blueskyHandle, blueskyPassword)
print('Welcome,', profile.display_name)

myDate = datetime.today().strftime('%Y%m%d')

## This section connects to RapidAPI and retrieves the preferred list.  Note that RapidAPI's Twitter API ( by Alexander Vikhorev) offers several endpoints.
## I use the List endpoint to keep API calls down, thus saving $$$
urlTweets = "https://twitter-api45.p.rapidapi.com/listtimeline.php"
querystringTweets = {"list_id":twitterListId}
headersTweets = {
    "X-RapidAPI-Key": rapidApiKey,
    "X-RapidAPI-Host": "twitter-api45.p.rapidapi.com"
    }

## This section sets the baseline; loads all tweets available in the List timeline into memory.
## The following section depends on the Twitter API offered by RapidAPI. Various plans are offered depending on your usage.
## To keep costs down, we check multiple accounts via twitter lists and do so every 5 minutes.
responseTweets = requests.get(urlTweets, headers=headersTweets, params=querystringTweets)
listresults = json.loads(responseTweets.text)
timeline = listresults["timeline"]
xeets = list()
quotes = "\n\n💬\n\n"
for xeet in timeline:
    img_url = ''
    retweetedText = ''
    retweetedAuthor = ''
    quotedText = ''
    quotedAuthor = ''
    if "media" in xeet:
        if "photo" in xeet["media"]:
            img_url = xeet["media"]["photo"][0]["media_url_https"]
        if "video" in xeet["media"]:
            img_url = xeet["media"]["video"][0]["media_url_https"]
    if "retweeted_tweet" in xeet:
        retweetedText = xeet["retweeted_tweet"]["text"]
        retweetedText = retweetedText.replace('amp;', '')
        retweetedAuthor = "@" + xeet["retweeted_tweet"]["author"]["screen_name"] + ' '
    if "quoted" in xeet:
        quotedText = xeet["quoted"]["text"]
        quotedText = quotedText.replace('amp;', '')
        quotedAuthor = quotes + "@" + xeet["quoted"]["author"]["screen_name"] + ' '
    if xeet["text"] is not None:
        if not xeet["text"].startswith('RT '):
            textToPost = "@" + xeet["screen_name"] + " tweeted\n" + xeet["text"] + quotedAuthor + quotedText
            textToPost = textToPost.replace('amp;', '')
            print(textToPost)
            xeets.append(textToPost)
        if xeet["text"].startswith('RT '):
            cleanText = xeet["text"][3:]
            textToPost = "🔁 @" + xeet["screen_name"] + " retweeted\n" + retweetedAuthor + retweetedText
            textToPost = textToPost.replace('amp;', '')
            print(textToPost)
            xeets.append(textToPost)

## Now that we have all active tweets in memory, check back every so often and, if there are any new ones, post them to Bluesky!
## The python list "xeets" stores the tweets.  Every loop through the timeline will now check to see if a tweet exists in xeets.  If missing,
## it will a) be added to xeets and b) be posted to Bluesky

sleepInterval = 300  ##5 minutes
rebootInterval = sleepInterval*12*240 ##24 hours
while rebootInterval > sleepInterval:
    count = 0
    rebootInterval = rebootInterval - sleepInterval
    time.sleep(sleepInterval)
    print("Checking for updates...")
    try:
        responseTweets = requests.get(urlTweets, headers=headersTweets, params=querystringTweets)
        listresults = json.loads(responseTweets.text)
        timeline = listresults["timeline"]    
        timeline = timeline[:20]  
        for xeet in timeline:
            now = datetime.now(timezone.utc).astimezone()
            print(str(xeet["created_at"]) + "   "+ str((now + timedelta(days=-1))))
            img_url = ''
            retweetedText = ''
            retweetedAuthor = ''
            quotedText = ''
            quotedAuthor = ''

            if datetime.strptime(xeet["created_at"], '%a %b %d %H:%M:%S %z %Y') < (now + timedelta(days=-1)) : 

                next
            if "media" in xeet:
                if "photo" in xeet["media"]:
                    img_url = xeet["media"]["photo"][0]["media_url_https"]
                if "video" in xeet["media"]:
                    img_url = xeet["media"]["video"][0]["media_url_https"]
            if "retweeted_tweet" in xeet:
                retweetedText = xeet["retweeted_tweet"]["text"]
                retweetedText = retweetedText.replace('amp;', '')
                retweetedAuthor = "@" + xeet["retweeted_tweet"]["author"]["screen_name"] + ' '
            if "quoted" in xeet:
                quotedText = xeet["quoted"]["text"]
                quotedText = quotedText.replace('amp;', '')
                quotedAuthor = quotes + "@" + xeet["quoted"]["author"]["screen_name"] + ' '
            if xeet["text"] is not None:
                if not xeet["text"].startswith('RT '):
                    textToPost = "@" + xeet["screen_name"] + " tweeted\n" + xeet["text"] + quotedAuthor + quotedText
                    textToPost = textToPost.replace('amp;', '')
                    if(textToPost not in xeets):
                        count = count + 1 + (round(len(textToPost) / 300))
                        xeets.append(textToPost)
                        print(textToPost)
                        if(count <=30):  ##Bluesky limits you to so many posts per minute.  This counter is to prevent being rate limited
                            postToBluesky(textToPost, img_url, client)
                            logging.info("Posted to Bluesky: " + textToPost)
                        else:
                            print("Tweet has too many characters or rate limit exceeded.  Not posting to Bluesky -- " + textToPost)
                            logging.error("Tweet has too many characters or rate limit exceeded.  Not posting to Bluesky -- " + textToPost)
                if xeet["text"].startswith('RT '):
                    cleanText = xeet["text"][3:]
                    textToPost = "🔁 @" + xeet["screen_name"] + " retweeted\n" + retweetedAuthor + retweetedText
                    textToPost = textToPost.replace('amp;', '')
                    if(textToPost not in xeets):
                        count = count + 1 + (round(len(textToPost) / 300))
                        xeets.append(textToPost)
                        print(textToPost)
                        if(count <=30):  ##Bluesky limits you to so many posts per minute.  This counter is to prevent being rate limited
                            postToBluesky(textToPost, img_url, client)
                            logging.info("Posted to Bluesky: " + textToPost)
                        else:
                            print("Tweet has too many characters or rate limit exceeded.  Not posting to Bluesky -- " + textToPost)
                            logging.error("Tweet has too many characters or rate limit exceeded.  Not posting to Bluesky -- " + textToPost)
    except Exception as e:
        print(f"Issue encountered with Twitter API.  Moving on: {e}")
        logging.error(f"Issue encountered with Twitter API.  Moving on: {e}")

myDateTime = datetime.today().strftime('%Y-%m-%d %H:%M:%S')
emailStatus(moduleName + ' ended at ' + myDateTime)
