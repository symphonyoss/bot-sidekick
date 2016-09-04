# Sidekick

See the slides ![pdf](sidekick-20160724.pdf) for a better description
and discussion of Sidekick.

## Functionality

* Sidekick = a stateful bot written in Python

* Stateful means: Sidekick keeps state for each of its customers.

* Demonstrated services, PoC for:

  * automatic out-of-office messages
   (sent the first time the absent user is mentioned, once a day, per room)
  * pre-scheduled announcements (e.g. repetitive reminders, single+all rooms)
  * user-logging and content-watch using arbitrary regex (forwarding content of interest to subscribing user, also as a daily digest)
  * bulk room management (e.g. promote all room members with single cmd line) -- this has not been implemented

* Noteworthy I: you can define personalized shortcuts to trigger the bot, or define abbreviations for complex cmds

* Noteworthy II: The IM chat between user and bot becomes a protected control channel, which is useful for sending sensitive/private stuff to the command issuer even if the command was issued in a public room.


## Instructions

The code was not polished for a good onboarding experience, nor does this
repo containt a certificate. Therefore:

* Get a valid certificate for your incarnation of Sidekick

* Extract the cert and privatekey, according to the instructions
  found on Matt Joyce's "python symphony" repo and store the
  resulting files in the "./cert" directory

* No need to change the UID in the code to refer to your bot account:
  based on the certificate, Sidekick self-identifies with a call to the
  agent-bridge.

* In the main directory start the bot with
```
  ./sidekick.py
```

* Use Symphony to create an IM towards your bot, send there a simple
  /sidekick

* FYI: The bot's state ends up in ~/.symphony/sidekick-store.json. Trace
  files will be created at the same place and for each start of Sidekick.

Have fun, c
