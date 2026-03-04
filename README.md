# frizzle-phone

[![codecov](https://codecov.io/gh/frizzle-chan/frizzle-phone/graph/badge.svg?token=RQSA7M18EY)](https://codecov.io/gh/frizzle-chan/frizzle-phone)

frizzle-phone allows you use your favorite SIP VoIP phone to call into your favorite Discord voice channels.
For instance you can make it so dialing extension 100 will call your home server's vc and maybe extension 200 calls into your friend's server.

frizzle-phone is completely self contained, you just run the server and point your SIP phone at it.
No other infrastructure is needed.

## Features

- Functions as a SIP server that bridges Discord voice channel audio to your VoIP phone.
- Delightfully crunchy G.711 μ-law audio.
- Lightweight webapp for mapping Discord voice channels to phone extensions on the fly.

## Demo

https://github.com/user-attachments/assets/cea1d4cc-ff2c-44a8-afed-1600c8baabb2

## Compatibility

Right now frizzle-phone is optimized for my Cisco 7970G running 9.x SIP firmware negotiating G.711 μ-law.
But it should work for most SIP (RFC 3261) based phones. If it doesn't work with your phone, open an issue!
