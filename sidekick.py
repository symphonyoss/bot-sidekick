#!/usr/bin/env python

 #
 #
 #
 # Copyright 2016 Symphony Communication Services, LLC
 #
 # Licensed to Symphony Communication Services, LLC under one
 # or more contributor license agreements.  See the NOTICE file
 # distributed with this work for additional information
 # regarding copyright ownership.  The ASF licenses this file
 # to you under the Apache License, Version 2.0 (the
 # "License"); you may not use this file except in compliance
 # with the License.  You may obtain a copy of the License at
 #
 #  http://www.apache.org/licenses/LICENSE-2.0
 #
 # Unless required by applicable law or agreed to in writing,
 # software distributed under the License is distributed on an
 # "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 # KIND, either express or implied.  See the License for the
 # specific language governing permissions and limitations
 # under the License.
 #
 #

'''
    The Sidekick Bot

    a PoC for a statful bot keeping per-user state,
    offering a OOO service, announcements, tracing rooms and more

    July 2016, <cft@symphony.com>

    includes demo code of <matt.joyce@symphony.com>
'''

import binascii
import base64
from   bs4 import BeautifulSoup, element
from   datetime import datetime, time, timedelta
import json
import os
import random
import pytz
import pytz.reference
import re
import requests
import socket
import sys
import urllib2
import yaml


# API endpoint URI
__url__      = 'https://corporate-api.symphony.com:8444/'
__cert__     = ('cert/certificate.pem', 'cert/plainkey.pem')

# misc
__SIDEKICK_VERSION__ = "0.1"
__SERVER_TIMEZONE__  = "US/Pacific"

# limitation for testing (only react to commands if from this room)
# __TESTROOM__  = 'B7jT5tKwCmVffN0jidhhOn___qnxnQYhdA'

# ---------------------------------------------------------------------------

class SymphonyBridge:

    def __init__(self, url, certs):
        self.url = url
        self.certs = certs
        r = requests.post(self.url + 'sessionauth/v1/authenticate',
                          cert=self.certs, verify=True)
        if not r.status_code == 200:
            raise Exception(" REST(sessionauth): %s" % str(r))
        self.sessionToken = r.json()['token']
        r = requests.post(self.url + 'keyauth/v1/authenticate',
                          cert=self.certs, verify=True)
        if not r.status_code == 200:
            raise Exception(" REST(keyauth): %s" % str(r))
        self.keymngrToken = r.json()['token']

    def _request_or_except(self, endpoint):
        r = requests.get(self.url + endpoint, cert=self.certs, verify=True,
                          headers = {'sessionToken': self.sessionToken})
        if not r.status_code == 200:
            raise Exception(" REST(%s): %s" % (endpoint, str(r)))
        return r

    def get_my_id(self):
        r = self._request_or_except('pod/v1/sessioninfo')
        return str(r.json()['userId'])

    def get_user_email(self, uid):
        r = self._request_or_except('pod/v1/admin/user/' + uid)
        return r.json()['userAttributes']['emailAddress']

    def get_user_name(self, uid):
        r = requests.get(self.url + 'pod/v1/admin/user/' + uid,
                         cert=self.certs, verify=True,
                         headers = {'sessionToken': self.sessionToken})
        if r.status_code/100 == 4:
            return "[uid=%s]" % uid
        if not r.status_code == 200:
            raise Exception(" REST(%s): %s" % ('pod/v1/admin/user/', str(r)))
        return r.json()['userAttributes']['displayName']

    def get_room_name(self, sid):
        return "[sid=%s]" % sid
        r = requests.get(self.url + 'pod/v2/room/' + sid + '/info',
                         cert=self.certs, verify=True,
                         headers = {'sessionToken': self.sessionToken})
        if r.status_code/100 == 4:
            return "[sid=%s]" % sid
        if not r.status_code == 200:
            raise Exception(" REST(%s): %s" % ('pod/v2/room/', str(r)))
        return r.json()['roomAttributes']['name']

    def get_user_IM(self, uid):
        r = requests.post(self.url + 'pod/v1/im/create',
                          cert=self.certs, verify=True,
                          headers = {'sessionToken': self.sessionToken,
                                     'Content-Type': 'application/json'},
                          data = json.dumps( [ int(uid) ] ))
        if not r.status_code == 200:
            raise Exception(" REST(%s): %s" % ('pod/v1/im/create', str(r)))
        return r.json()['id']

    def create_datafeed(self):
        headers = {'Content-Type': 'application/json',
                   'sessionToken': self.sessionToken,
                   'keyManagerToken': self.keymngrToken}
        r = requests.post(self.url + 'agent/v1/datafeed/create',
                          headers=headers, cert=self.certs, verify=True)
        if not r.status_code == 200:
            raise Exception(" REST(%s): %s" % ('datafeed/create', str(r)))
        return r.json()['id']

    def read_datafeed(self, streamid):
        headers = {'Content-Type': 'application/json',
                   'sessionToken': self.sessionToken,
                   'keyManagerToken': self.keymngrToken}
        r = requests.get(self.url + 'agent/v2/datafeed/' + str(streamid) + '/read',
                         headers=headers, cert=self.certs, verify=True)
        if not r.status_code/100 == 2:
            raise Exception(" REST(%s): %s" % ('datafeed/read', str(r)))
        return r.text

    def send_message(self, streamid, msgFormat, message, attachments=None):
        headers = {'content-type': 'application/json',
                   'sessionToken': self.sessionToken,
                   'keyManagerToken': self.keymngrToken}
        data = { 'format': msgFormat, 'message': message }
        if attachments:
            data['attachments'] = attachments
        r = requests.post(self.url + 'agent/v2/stream/' + streamid + \
                          '/message/create',
                          headers=headers, data=json.dumps(data),
                          cert=self.certs, verify=True)
        if not r.status_code == 200:
            raise Exception(" REST(%s): %s" % ('message/create', str(r)))
        return r.text

# ---------------------------------------------------------------------------

class SidekickStore:

    def __init__(self):
        self.cacheFN = os.environ['HOME'] + '/.symphony'
        if not os.path.exists(self.cacheFN):
            os.makedirs(self.cacheFN)
        os.chmod(self.cacheFN, 0o700)
        self.cacheFN += '/sidekick-store.json'
        self.data = None           # root dir of our data tree 
        self.dirty = False
        self.saved = False

    def reset(self):
        if os.path.exists(self.cacheFN):
            os.unlink(self.cacheFN)

    def load(self):
        if os.path.exists(self.cacheFN):
            with open(self.cacheFN, 'r') as f:
                self.data = json.load(f)
            self.dirty = False
        else:
            self.data = {}
            self.dirty = True
        self.saved = False

    def sync(self):
        if not self.dirty:
            return
        with open(self.cacheFN + '.tmp', 'w') as f:
            json.dump(self.data, f)
        os.chmod(self.cacheFN + '.tmp', 0o600)
        if not self.saved:
            for n in range(0, 50):
                i = 50 - n
                if os.path.exists(self.cacheFN + '~' + str(i)):
                    os.rename(self.cacheFN + '~' + str(i),
                              self.cacheFN + '~' + str(i+1))
            if os.path.exists(self.cacheFN):
                os.rename(self.cacheFN, self.cacheFN + '~1')
        os.rename(self.cacheFN + '.tmp', self.cacheFN)
        self.saved = True
        self.dirty = False

    def get(self, name, ndx1=None, fresh=False):
        return None

    def config_get(self, name):
        if name in self.data['config']:
            return self.data['config'][name]
        return None

    def config_setVal(self, name, val):
        self.data['config'][name] = val
        self.dirty = True

# ---------------------------------------------------------------------------
# convenience:

def send_with_mention(sid, uid, msg, attachments=None):
    try:
        email = get_cached_user_email(uid)
        msg = "<mention email=\"%s\"/> %s" % (email, msg)
        msg = '<messageML>' + msg + '</messageML>'
        sym.send_message(sid, 'MESSAGEML', msg, attachments)
    except:
        sym.send_message(sid, 'TEXT', msg, attachments)

def send_txt_message(sid, msg):
        sym.send_message(sid, 'TEXT', msg)

def get_cached_user_IM(uid):
    if 'im' in SKS.data['user'][uid]:
        return SKS.data['user'][uid]['im']
    im = sym.get_user_IM(uid)
    SKS.data['user'][uid]['im'] = im
    SKS.dirty = True
    return im

def get_cached_user_email(uid):
    if 'email' in SKS.data['user'][uid]:
        return SKS.data['user'][uid]['email']
    # once we are entitled to get this info, we should cache it here
    return sym.get_user_email(uid)

def get_cached_user_name(uid):
    if 'displayName' in SKS.data['user'][uid]:
        return SKS.data['user'][uid]['displayName']
    # once we are entitled to get this info, we should cache it here
    return sym.get_user_name(uid)

tracef = None

def trace(s):
    global tracef
    if not tracef:
        fn = os.environ['HOME'] + '/.symphony'
        if not os.path.exists(fn):
            os.makedirs(fn)
            os.chmod(SKS.cacheFN, 0o700)
        fn += '/sidekick-' + datetime.today().strftime('%Y%m%d-%H%M%S') + '.log'
        tracef = open(fn, 'w')
    tracef.write(datetime.today().strftime('%Y%m%d-%H%M%S') + ': ' + s + '\n')
    tracef.flush()

def is_IM(sid):
    s = [ SKS.data['user'][uid]['im'] for uid in SKS.data['user'] \
                                           if 'im' in SKS.data['user'][uid] ]
    return sid in s

def parse_time(d, t, z):
    try:
        if d == 'today':
            d = datetime.today().strftime("%m/%d/%y")
        dt = datetime.strptime(d + ' ' + t, '%x %H:%M')
        if z in ['PDT', 'PST']:
            z = "US/Pacific"
        elif z in ['CST', 'CDT']:
            z = "US/Central"
        elif z in ['EST', 'EDT']:
            z = "US/Eastern"
        elif z in ['BST']:
            z = "Europe/London"
        elif z in ['CET', 'CEST', 'CEDT']:
            z = "Europe/Amsterdam"
        tz = pytz.timezone(z)
        dt = tz.localize(dt)
    except Exception, details:
        return None
    return dt

def render_time(dt):
    return dt.strftime('%x %H:%M ') + dt.tzinfo.zone

# ---------------------------------------------------------------------------

def not_implemented(e, line, args):
    send_txt_message(e['streamId'], "'%s' is not implemented yet" % args[1])

def do_cmd_list(e, line, args):
    send_txt_message(e['streamId'], help_text['cmd_list'])

def do_examples(e, line, args):
    send_txt_message(e['streamId'], help_text['examples'])

def do_help(e, line, args):
    send_txt_message(e['streamId'], help_text['help'])

def do_intro(e, line, args):
    send_txt_message(e['streamId'], help_text['intro'])

def do_status_alias(sid, uid):
    lst = [a for a in SKS.data['user'][uid]['alias']]
    if len(lst) == 0:
        send_txt_message(sid, "Aliases: (none)")
        return
    s = 'Aliases:\n'
    for a in lst:
        s += "  alias %s=%s\n" % (a[0], a[1])
    send_txt_message(sid, s)
    
def do_status_announce(sid, uid):
    lst = [a for a in SKS.data['user'][uid]['announce']]
    if len(lst) == 0:
        send_txt_message(sid, "Announce tasks: (none)")
        return
    n = 1
    s = 'Announce tasks:\n'
    for a in lst:
        if a['stream'] == '*':
            rn = "all rooms"
        elif a['stream'] == sid:
            rn = "this room"
        else:
            rn = sym.get_room_name(a['stream'])
        if a['repeat'] == 'daily':
            rpt = ' -daily'
        elif a['repeat'] == 'weekly':
            rpt = ' -weekly'
        elif a['repeat'] == 'monthly':
            rpt = ' -monthly'
        else:
            rpt=' '
        s += "%d)  announce%s %s %s (--> %s)\n" % (n, rpt, a['when'], a['msg'], rn)
        n += 1
    send_txt_message(sid, s)
    
def do_status_ooo(sid, uid):
    lst = [o for o in SKS.data['user'][uid]['ooo']]
    if len(lst) == 0:
        send_txt_message(sid, "Out-of-office tasks: (none)")
        return
    n = 1
    s = 'Out-of-office tasks:\n'
    for o in lst:
        if o['stream'] == '*':
            rn = "all rooms"
        elif o['stream'] == sid:
            rn = "this room"
        else:
            rn = sym.get_room_name(o['stream'])
        s += "%d)  ooo %s %s (--> %s)\n" % (n, o['till'], o['msg'], rn)
        n += 1
    send_txt_message(sid, s)

def do_status_watch(sid, uid):
    lst = [w for w in SKS.data['user'][uid]['watch']]
    if len(lst) == 0:
        send_txt_message(sid, "Watch tasks: (none)")
        return
    n = 1
    s = 'Watch tasks:\n'
    for w in lst:
        if w['stream'] == '*':
            rn = "all rooms"
        elif w['stream'] == sid:
            rn = "this room"
        else:
            rn = sym.get_room_name(w['stream'])
        s += "%d)  watch for '%s' (<-- %s)\n" % (n, w['regex'], rn)
        n += 1
    send_txt_message(sid, s)
    
def do_status(e, line, args):
    uid = str(e['fromUserId'])
    send_with_mention(e['streamId'], uid,
           "A status report will be sent to your Sidekick chat room shortly.")

    sid = get_cached_user_IM(uid)
    send_txt_message(sid, "Here is your status report:")
    do_status_alias(sid, uid)
    do_status_announce(sid, uid)
    do_status_ooo(sid, uid)
    do_status_watch(sid, uid)

def do_alias(e, line, args):
    uid = str(e['fromUserId'])
    if len(args) < 3:
        do_status_alias(e['streamId'], uid)
        return
    line = line[line.index(args[2]):].split('=', 1)
    if len(line) == 1: # display existing alias bindings
        for a in SKS.data['user'][uid]['alias']:
            if a[0] == line[0]:
                send_txt_message(e['streamId'], "alias %s=%s" % (a[0], a[1]))
                return
        send_txt_message(e['streamId'], "No such alias %s" % line[0])
        return
    if line[-1] == '': # undefine existing alias
        for a in SKS.data['user'][uid]['alias']:
            if a[0] == line[0]:
                SKS.data['user'][uid]['alias'].remove(a)
                SKS.dirty = True
                send_txt_message(e['streamId'], "Removing alias for %s" % a[0])
                return
        send_txt_message(e['streamId'], "No such alias %s" % line[0])
        return
    # print line
    if line[0] in ['/sidekick', '/sk']:
        send_txt_message(e['streamId'],
                         "You can't redefine the default Sidekick triggers.")
        return
    a = [a for a in SKS.data['user'][uid]['alias'] if a[0] == line[0]]
    if len(a) == 0:
        a = [line[0], line[1]]
        # print "appending alias %s" % str(a)
        SKS.data['user'][uid]['alias'].append(a)
        SKS.dirty = True
        send_txt_message(e['streamId'],
                         "Adding new alias %s=%s" % (line[0], line[1]))
    else:
        a[1] = line[1]
        send_txt_message(e['streamId'],
                         "Redefining alias %s=%s" % (line[0], line[1]))
   
def do_announce(e, line, args):
    uid = str(e['fromUserId'])
    if len(args) < 3:
        send_txt_message(e['streamId'],
            "A status report will be sent to your Sidekick chat room shortly.")
        do_status_announce(get_cached_user_IM(uid), uid)
        return
    # print "announce command: %s" % str(args)
    allRooms = False
    repeat = 'once'
    while len(args) > 2:
        if args[2] == '-all':
            allRooms = True
            del args[2]
        elif args[2] == '-daily':
            repeat = 'daily'
            del args[2]
        elif args[2] == '-weekly':
            repeat = 'weekly'
            del args[2]
        elif args[2] == '-monthly':
            repeat = 'monthly'
            del args[2]
        else:
            break
    line = line[line.index(args[2]):].split(' ')

    if line[0] == 'cancel':
        if len(line) == 1 or line[1] == '':
            do_status_announce(e['streamId'], uid)
            SKS.data['user'][uid]['announce'] = []
            SKS.dirty = True
            send_txt_message(e['streamId'],
                             "All announce entries above were deleted")
            return
        nr = 0
        try:
            nr = int(line[1])
        except Exception:
            nr = 0
        alist = SKS.data['user'][uid]['announce']
        if nr < 1 or nr > len(alist):
            msg = "invalid number or number out of range"
        else:
            nr -= 1
            msg = "announce entry for %s (%s) was removed" % (alist[nr]['when'],
                                                           alist[nr]['msg'])
            del alist[nr]
            SKS.dirty = True
        send_txt_message(e['streamId'], msg)
        return

    now = datetime.today()
    now = pytz.timezone(__SERVER_TIMEZONE__).localize(now)
    if line[0] == 'now':
        dts = render_time(now)
        del line[0]
    elif len(line) > 2:
        dt = parse_time(line[0], line[1], line[2])
        if dt == None:
            send_txt_message(e['streamId'], "Cannot parse \"%s %s %s\"" % \
                                                   (line[0], line[1], line[2]))
            return
        dts = render_time(dt)
        if dt < now:
            send_txt_message(e['streamId'],
                             "announce command: '%s' is in the past" % dts)
            return
        del line[0]
        del line[0]
        del line[0]
    else:
        send_txt_message(e['streamId'],
                         "announce command: not enough arguments")
        return

    a = { 'when' : dts, 'msg' : ' '.join(line), 'repeat' : repeat,
          'createDate': now.strftime("%x")}
    if allRooms:
        sid = '*'
    else:
        sid = e['streamId']
    a['stream'] = sid
    SKS.data['user'][uid]['announce'].append(a)
    SKS.dirty = True

    if sid == '*':
        sid = "in all rooms"
    else:
        sid = "in this room only"
    send_with_mention(e['streamId'], uid,
                      "A new announce task is active for %s: %s (%s)" % \
                                                   (a['when'], a['msg'], sid))

def do_ooo(e, line, args):
    uid = str(e['fromUserId'])
    trace("do_ooo " + uid)
    if len(args) < 3:
        send_txt_message(e['streamId'],
            "A status report will be sent to your Sidekick chat room shortly.")
        do_status_ooo(get_cached_user_IM(uid), uid)
        return
    # print "ooo command: %s" % str(args)
    allRooms = False
    if len(args) > 2 and args[2] == '-all':
        allRooms = True
        del args[2]
    line = line[line.index(args[2]):].split(' ', 1)
    # print line
    if line[0] == 'cancel':
        if len(line) == 1 or line[1] == '':
            do_status_ooo(e['streamId'], uid)
            SKS.data['user'][uid]['ooo'] = []
            SKS.dirty = True
            send_txt_message(e['streamId'], "All OOO entries above were deleted")
            return
        nr = 0
        try:
            nr = int(line[1])
        except Exception:
            nr = 0
        olist = SKS.data['user'][uid]['ooo']
        if nr < 1 or nr > len(olist):
            msg = "invalid number or number out of range"
        else:
            nr -= 1
            msg = "ooo entry for %s (%s) was removed" % (olist[nr]['till'],
                                                           olist[nr]['msg'])
            del olist[nr]
            SKS.dirty = True
        send_txt_message(e['streamId'], msg)
        return

    sid = get_cached_user_IM(uid)
    if False and sid == e['streamId'] and not allRooms:
        send_txt_message(e['streamId'], "This is a private room, only you " +
                         "would see the OOO messages.\nAdd -all after the " +
                         "ooo keyword to cover all rooms, or issue the " +
                         "ooo command in a room shared with others.")
        return

    try:
        dt = datetime.strptime(line[0], '%x')
    except:
        send_txt_message(e['streamId'], "Cannot parse date %s" % line[0])
        return
    if dt < datetime.today():
        send_txt_message(e['streamId'],
                     "ooo command: date '%s' already over" % dt.strftime('%x'))
        return

    o = { 'till' : line[0], 'msg' : line[1], 'notified' : {} }
    if allRooms:
        sid = '*'
    else:
        sid = e['streamId']
    o['stream'] = sid
    SKS.data['user'][uid]['ooo'].append(o)
    SKS.dirty = True

    if sid == '*':
        sid = "in all rooms"
    else:
        sid = "in this room only"
    send_with_mention(e['streamId'], uid,
                      "Your new OOO message is active until %s: %s (%s)" % \
                                                     (line[0], line[1], sid))

def do_ooo_notification(sid, uid):
    if not uid in SKS.data['user']:
        return
    olist = SKS.data['user'][uid]['ooo']
    now = datetime.today()
    nowstr = now.strftime('%x')
    for o in olist:
        # only notify if all rooms selected, or if in originating room
        if not o['stream'] == sid and \
           not o['stream'] == '*':
            continue
        dt = datetime.strptime(o['till'], '%x')
        # remove stale entry:
        if dt < now:
            olist.remove(o)
            SKS.dirty = True
            continue
        if sid in o['notified'] and o['notified'][sid] == nowstr:
            continue
        reply = "This is an out-of-office message on behalf of %s:\n" % \
                get_cached_user_name(uid)
        reply += "away until %s because of \"%s\"" % (o['till'], o['msg'])
        send_txt_message(e['streamId'], reply)
        # update all active ooo entries (with matching streamId)
        for o in olist:
            if o['stream'] == sid or o['stream'] == '*':
                o['notified'][sid] = nowstr
                SKS.dirty = True
        
        break

def do_version(e, line, args):
    with open(__cert__[0], "r") as f:
        bot = re.compile('/CN=([^ /]+)/').search(f.read()).group(1)
    s  = "This is Sidekick\n"
    s += "- started %s\n" % startDate
    s += "- on host %s\n" % socket.gethostname()
    s += "- as %s@symphony.com\n" % bot
    s += "- database has %d users and %d rooms\n" % \
               (len(SKS.data['user']), len(SKS.data['config']['myStreamIDs']))
    s += "v0.1, July 2016, cft@symphony.com"
    send_txt_message(e['streamId'], s)

def do_watch(e, line, args):
    uid = str(e['fromUserId'])
    if len(args) < 3:
        send_txt_message(e['streamId'],
            "A status report will be sent to your Sidekick chat room shortly.")
        do_status_watch(get_cached_user_IM(uid), uid)
        return
    # print "watch command: %s" % str(args)
    allRooms = False
    if len(args) > 2 and args[2] == '-all':
        allRooms = True
        del args[2]
    line = line[line.index(args[2]):].split(' ', 1)
    print line
    if line[0] == 'cancel':
        if len(line) == 1 or line[1] == '':
            do_status_watch(e['streamId'], uid)
            SKS.data['user'][uid]['watch'] = []
            SKS.dirty = True
            send_txt_message(e['streamId'], "All watch entries above were deleted")
            return
        nr = 0
        try:
            nr = int(line[1])
        except Exception:
            nr = 0
        wlist = SKS.data['user'][uid]['watch']
        if nr < 1 or nr > len(wlist):
            msg = "invalid number or number out of range"
        else:
            nr -= 1
            msg = "watch entry for '%s' was removed" % olist[nr]['regex']
            del wlist[nr]
            SKS.dirty = True
        send_txt_message(e['streamId'], msg)
        return

    sid = get_cached_user_IM(uid)
    if False and sid == e['streamId'] and not allRooms:
        send_txt_message(e['streamId'], "This is a private room, only you " +
                         "would see matching messages.\nAdd -all after the " +
                         "watch keyword to cover all rooms, or issue the " +
                         "watch command in a room shared with others.")
        return

    w = { 'regex' : line[0] }
    if allRooms:
        sid = '*'
    else:
        sid = e['streamId']
    w['stream'] = sid
    SKS.data['user'][uid]['watch'].append(w)
    SKS.dirty = True

    if sid == '*':
        sid = "in all rooms"
    else:
        sid = "in this room only"
    send_with_mention(e['streamId'], uid,
                      "Your new watch task for '%s' is now active (%s)" % \
                                                              (line[0], sid))

# ---------------------------------------------------------------------------

# layout of user data structure:
'''
  uid : {
    'alias'    : [ [s1,s2], ...]
    'announce' : [ ],
    'displayName': str,
    'email'    : str,
    'ooo'      : [ {
           'till'     : str,
           'msg'      : str,
           'stream'   : str,
           'notified' : { sid : str }
           } ],
    'watch'    : [ regexp1, ... ]
  }
'''

command_table = {
    '?'       : do_cmd_list,
    'alias'   : do_alias,
    'announce': do_announce,
    'examples': do_examples,
    'help'    : do_help,
    'intro'   : do_intro,
    'manage'  : not_implemented,
    'ooo'     : do_ooo,
    'status'  : do_status,
    'version' : do_version,
    'watch'   : do_watch,
    }

def hunt_for_announceTime():
    now = datetime.today()
    now = pytz.timezone(__SERVER_TIMEZONE__).localize(now)
    for uid in SKS.data['user']:
        alist = SKS.data['user'][uid]['announce']
        for a in alist:
            t = a['when'].split(' ')
            dt = parse_time(t[0], t[1], t[2])
            if now < dt:
                continue
            ct = ''
            if 'createDate' in a:
                ct = " from %s" % a['createDate']
            reply = "This is a prerecorded message on behalf of %s%s: %s\n" % \
                                      (get_cached_user_name(uid), ct, a['msg'])
            if a['stream'] == '*':
                lst = SKS.data['config']['myStreamIDs']
            else:
                lst = [ a['stream'] ]
            trace('announcement by %s re %s' % (uid, str(a)))
            for sid in lst:
                # XX if -all, we should loop over all non-IM rooms here,
                # but the bridge API can't tell us which rooms are IMs...
                send_txt_message(sid, reply)

            if a['repeat'] == 'daily':
                dt += timedelta(days=1)
                a['when'] = render_time(dt)
            elif a['repeat'] == 'weekly':
                dt += timedelta(days=7)
                a['when'] = render_time(dt)
            elif a['repeat'] == 'monthly':
                dt += timedelta(months=1)
                a['when'] = render_time(dt)
            else:
                alist.remove(a)
            SKS.dirty = True

def hunt_for_command(e, msg):
    # while debugging: react in our test room only, be deaf elsewhere
    if '__TESTROOM__' in globals() and not e['streamId'] == __TESTROOM__:
        return

    # does message start with text?
    if len(msg.messageML.contents) == 0 or \
         not type(msg.messageML.contents[0]) == element.NavigableString:
        return
    line = msg.messageML.contents[0].encode('ascii',
                                            'backslashreplace')
    if len(line) > 0 and line[-1:] == '\xa0':
        line = line[:-1]
    line.replace('\xa0', ' ')

    uid = str(e['fromUserId'])
    if not uid in SKS.data['user']: # add user to our database
        SKS.data['user'][uid] = {
            'alias' : [], 'announce' : [], 'ooo' : [], 'watch' : []
        }
        SKS.dirty = True

    args = line.split(' ')
    if not args[0] in ['/sk', '/sidekick']:
        # see if sending user had a private trigger
        a = [a for a in SKS.data['user'][uid]['alias'] if a[0] == args[0]]
        if a == []:
            return
        # print "doing alias rewriting: %s --> %s" % (args[0], a[0][1])
        args[0] = a[0][1]
        line = ' '.join(args)
        args = line.split(' ') # parse the (rewritten) command line again

    # print args
    if not args[0] in ['/sk', '/sidekick']:
        send_txt_message(e['streamId'], "Unknown Sidekick trigger %s" % line)
        return

    # trigger was recognized, now react:
    trace('cmd by %s in %s' % (uid, str(e)))
    if len(args) == 1:
        command_table['intro'](e, line, args)
    elif args[1] in command_table:
        command_table[args[1]](e, line, args)
    else:
        send_txt_message(e['streamId'], "Unknown command %s" % args[1])

def hunt_for_regex(e, msg):
    # flatten the msg:
    line = []
    for c in msg.messageML.contents:
        if not type(c) == element.NavigableString:
            continue
        line.append(c.encode('ascii', 'backslashreplace'))
    line = ' '.join(line)

    orig_uid = str(e['fromUserId'])
    orig_sid = e['streamId']
    if is_IM(orig_sid): # never watch a private IM
        return

    # do the bot war
    for b in bots:
        if not orig_uid == b['uid']:
            continue
        regexp = re.compile(b['trigger'])
        if regexp.search(line):
            b['action'](e['streamId'], orig_uid, line)

    for uid in SKS.data['user']:
        wlist = SKS.data['user'][uid]['watch']
        for w in wlist:
            if not w['stream'] == '*' and not w['stream'] == orig_sid:
                continue;
            regexp = re.compile(w['regex'])
            if not regexp.search(line):
                continue
            sid = get_cached_user_IM(uid)
            trace('watch for "%s"' % uid)
            send_txt_message(sid,
                             "WATCH REPORT: room %s, user %s\n\"%s\"" %
                             (sym.get_room_name(orig_sid),
                              get_cached_user_name(orig_uid), line))
            break

# ---------------------------------------------------------------------------
# bot war

def reply_with_scotch(sid, uid, msg):
    soup = BeautifulSoup(urllib2.urlopen(
          urllib2.Request(
              "https://www.google.co.in/search?q=scotch&source=lnms&tbm=isch",
              headers= {'User-Agent': 'Mozilla/5.0'})),
                     "html.parser")
    images = [a['src'] for a in \
              soup.find_all("img", {"src": re.compile("gstatic.com")})]
    img = images[ int(random.random() * len(images)) ]
    raw = urllib2.urlopen(img).read()

    files = {'file': ('scotch.jpg', raw, 'image/jpeg')}
    headers = {'sessionToken': sym.sessionToken,
               'keyManagerToken': sym.keymngrToken}
    r = requests.post(__url__ + \
                          'agent/v1/stream/%s/attachment/create' % sid,
                      cert=sym.certs, verify=True, files=files,
                      headers=headers)
    imgId = r.json()['id']

    cheers = [ "Cheers!",
               "You deserve it!",
               "Don't drink too much, will you?",
               "Everybody knows you are not picky about the brand.",
               "Going overboard, again?",
               "... makes you feel cool, I know. Here is a little something, old chap!",
               "Want to try <a href=\"http://www.aa.org/\"/>, instead?" ]
    cheers = cheers[ int(random.random() * len(cheers)) ]
    send_with_mention(sid, uid, cheers, [ {
        'id': imgId, 'name': 'scotch.jpg', 'size': len(raw),
        'contentType': 'image/jpeg', 'encrypted': True
    } ] )

def reply_with_disdain(sid, uid, msg):
    if int(random.random() * 2) > 0:
        return
    grunts = [ "Oh come on!",
               "wts?",
               "bah",
               "piss off bot",
               "go home",
               "how funny you are!"
    ]
    grunts = grunts[ int(random.random() * len(grunts)) ]
    send_with_mention(sid, uid, grunts)

def reply_with_echo(sid, uid, msg):
#    uid = get_cached_user_name(uid)
    send_with_mention(sid, uid, 'Echo: "%s"' % msg)

bots = [
    { 'uid': '71811853189562',          # bot.user8 (Woodhouse)
      'trigger': 'Time for scotch!',
      'action': reply_with_scotch},
    { 'uid': '71811853189566',          # bot.user11 (Symfuny)
      'trigger': 'punch|poke|kick',
      'action': reply_with_disdain},
    { 'uid': '71811853189427',          # cft (for testing)
      'trigger': 'abc|101',
      'action': reply_with_echo}
#      'action': reply_with_disdain}
#      'action': reply_with_scotch}
    ]

# ---------------------------------------------------------------------------

now = datetime.today()
now = pytz.timezone(__SERVER_TIMEZONE__).localize(now)
startDate = render_time(now)
print ">> " + startDate
trace("start")

SKS = SidekickStore()
SKS.load()
if not 'user' in SKS.data:
    SKS.data['user'] = {}
if not 'config' in SKS.data:
    SKS.data['config'] = { 'myStreamIDs' : [] }
if not 'sidekick.state.version' in SKS.data:
    SKS.data['sidekick.state.version'] = '0.1'
SKS.sync()  # creates the file if not yet existing
myStreamIDs = SKS.data['config']['myStreamIDs']

for uid in SKS.data['user']:
    u = SKS.data['user'][uid]
    for n in ['alias', 'announce', 'ooo', 'watch']:
        if not n in u:
            u[n] = []

with open('sidekick-help.yaml', 'r') as f:
    help_text = yaml.load(f.read())

# hit the net --------------------------------------------------------------v
print ">> connecting ..."
try:
    sym = SymphonyBridge(__url__, __cert__)
    my_id = sym.get_my_id()
    datafeed_id = sym.create_datafeed()
except Exception, details:
    s = "Error contacting the corporate API bridge: " + str(details)
    trace(s)
    print s
    sys.exit(-1)

print ">> starting loop:"
while True:
    events = sym.read_datafeed(datafeed_id)

    if not events or len(events) == 0: # empty keep-alive msg
        try:
            SKS.sync()
            hunt_for_announceTime()
        except Exception, details:
            s = "Error in periodic: " + str(details)
            trace(s)
            print s
        continue

    for e in json.loads(events):
        # collect all streamIds for which we receive msgs
        # (meaning: these are the streams we are part of,
        #  the bridge/API cannot produce that list ...)
        if not e['streamId'] in myStreamIDs:
            myStreamIDs.append(e['streamId'])
            SKS.dirty = True

        if 'message' in e:
            try:
                if str(e['fromUserId']) == my_id: # skip own msgs
                    continue
                print
                print e
                msg = BeautifulSoup(e['message'], 'xml')
                for m in msg.find_all("mention"): # check for ooo reactions
                    do_ooo_notification(e['streamId'], m.attrs['uid'])
                hunt_for_command(e, msg)
                hunt_for_regex(e, msg)
            except Exception, details:
                s = "Error in cmd: %s %s" % (str(e), str(details))
                trace(s)
                print s

# eof -----------------------------------------------------------------------
