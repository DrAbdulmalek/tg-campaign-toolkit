#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tg_relogin2.py — rebuild Telegram session after env rollback / auth-key burn.

v2 FIX (critical): `code` mode now explicitly passes phone_code_hash read from
.secrets/tg_phone_code_hash.txt. The v1 local build silently omitted it, which
made sign_in() fail in confusing ways (SessionPasswordNeeded look-alike).

Usage:
  python tg_relogin2.py send          -> send login code, persist phone_code_hash
  python tg_relogin2.py code <CODE>   -> finish login, persist StringSession

Never prints the session string or code hash. Prints status tokens only.

!!! SINGLE-CLIENT IRON RULE !!!
The StringSession and the device session share the SAME auth key. At any moment
ONLY ONE TelegramClient process may use it. Two concurrent clients (e.g. a
forwarder + a sender) => AuthKeyDuplicatedError => key permanently BURNED
(TG invalidates it, IP rotation accelerates detection). Always run pipelines
sequentially: one fwd round -> one res step -> one fwd round ...
After successful login, export the StringSession and treat the device session
as bootstrap-only.
"""
import sys, json, asyncio, os
from telethon import TelegramClient, errors
from telethon.sessions import StringSession

BASE = '/home/z/my-project'
SEC = f'{BASE}/.secrets'
APIF = f'{SEC}/telegram_api.json'
HASHF = f'{SEC}/tg_phone_code_hash.txt'
STRF = f'{SEC}/tg_string_session.txt'
API = json.load(open(APIF))
PHONE = API['phone']


async def main():
    if len(sys.argv) < 2:
        print('USAGE: send | code <CODE>')
        sys.exit(2)
    mode = sys.argv[1]
    client = TelegramClient(StringSession(), int(API['api_id']), API['api_hash'],
                            request_retries=2, retry_delay=1, connection_retries=2,
                            timeout=15)
    await client.connect()
    if not await client.is_user_authorized():
        if mode == 'send':
            sent = await client.send_code_request(PHONE)
            with open(HASHF, 'w') as f:
                f.write(sent.phone_code_hash)
            print('CODE_SENT', flush=True)
        elif mode == 'code':
            pass  # fall through to sign_in below (hash is read from HASHF)
    if mode == 'code':
        # fallthrough only if still unauthorized; if already authed just export
        if await client.is_user_authorized():
            with open(STRF, 'w') as f:
                f.write(StringSession(client.session).save())
            print('ALREADY_AUTH', flush=True)
        else:
            code = sys.argv[2]
            # --- v2 critical fix: always pass the stored phone_code_hash ---
            pch = open(HASHF).read().strip() if os.path.exists(HASHF) else ''
            kw = dict(phone=PHONE, code=code)
            if pch:
                kw['phone_code_hash'] = pch
            try:
                await client.sign_in(**kw)
            except errors.SessionPasswordNeededError:
                print('PASSWORD_NEEDED', flush=True)
                sys.exit(5)
            except errors.PhoneCodeInvalidError:
                print('CODE_INVALID', flush=True)
                sys.exit(6)
            except errors.PhoneCodeExpiredError:
                print('CODE_EXPIRED (run send again)', flush=True)
                sys.exit(7)
            except errors.AuthKeyDuplicatedError:
                print('AUTH_KEY_DUPLICATED (key burned - archive session files '
                      'and start a fresh send)', flush=True)
                sys.exit(8)
            s = StringSession(client.session)
            with open(STRF, 'w') as f:
                f.write(s.save())
            me = await client.get_me()
            print('AUTH_OK', getattr(me, 'username', None) or getattr(me, 'first_name', ''),
                  flush=True)
    elif mode == 'send' and await client.is_user_authorized():
        with open(STRF, 'w') as f:
            f.write(StringSession(client.session).save())
        print('ALREADY_AUTH', flush=True)
    await client.disconnect()


if __name__ == '__main__':
    asyncio.run(main())
