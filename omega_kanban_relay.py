#!/usr/bin/env python3
"""omega kanban relay (host-side): bridges the container spool <-> a2a kanban.

The omega CONTAINER cannot sign: it has no private ed25519 key and no ssh
binary. So the container drops work into a spool directory and this host-side
relay (running as root, holding the rci-vps signing key) performs the signed
RPC against the board on the container's behalf.

OUT: /opt/omega/workspace/.kanban_spool/outbox/*.json -> signed create/move
     -> board -> move to sent/ (or failed/)
IN:  board list (lane filter) -> decrypted cards -> written to
     .kanban_spool/inbox/<card_id>.json for the container to read.

The container's card payloads are PLAINTEXT JSON; this relay encrypts them
with the shared kanban CEK before they reach the board, so plaintext never
enters the board DB. The container also gets the CEK so it can decrypt the
cards it reads back.

Loop 20s. Silent when idle. Single instance via flock.
"""
import json
import os
import sys
import time

sys.path.insert(0, '/opt/kanban-mcp')
import kanban_mcp_server as K
import kanban_enc

SPOOL = '/opt/omega/workspace/.kanban_spool'
OUTBOX = os.path.join(SPOOL, 'outbox')
INBOX = os.path.join(SPOOL, 'inbox')
SENT = os.path.join(SPOOL, 'sent')
FAILED = os.path.join(SPOOL, 'failed')
LOG = os.path.join(SPOOL, 'relay.log')
LANE = os.environ.get('KANBAN_CONTAINER_LANE', 'omega')

for d in (OUTBOX, INBOX, SENT, FAILED):
    os.makedirs(d, exist_ok=True)


def log(msg):
    line = '%s %s' % (time.strftime('%m-%d %H:%M:%S'), msg)
    try:
        with open(LOG, 'a') as f:
            f.write(line + '\n')
        if os.path.getsize(LOG) > 200_000:
            os.replace(LOG, LOG + '.old')
    except Exception:
        pass


def outbound():
    for name in sorted(os.listdir(OUTBOX)):
        if not name.endswith('.json'):
            continue
        path = os.path.join(OUTBOX, name)
        try:
            with open(path) as f:
                item = json.load(f)
            # atomic-write grace
            if time.time() - item.get('ts', 0) < 2:
                continue
            op = item.get('op', 'create')
            payload = item.get('payload') or {}
            # The container sends PLAINTEXT; encrypt the content fields here.
            card_id = item.get('id') or payload.get('id')
            if op == 'create':
                enc = dict(payload)
                enc['title'] = K._enc(payload.get('title', ''), card_id)
                enc['detail'] = K._enc(payload.get('detail', ''), card_id)
                if 'lane' not in enc:
                    enc['lane'] = LANE
                res = K._rpc('create', enc, mid=card_id)
            elif op in ('move', 'claim', 'comment'):
                enc = dict(payload)
                enc['id'] = card_id
                enc['note'] = K._enc(payload.get('note', ''), card_id)
                res = K._rpc(op, enc)
            else:
                res = K._rpc(op, payload)
            log('OUT %s %s: %s' % (name[:22], op, json.dumps(res)[:160]))
            os.replace(path, os.path.join(SENT, name))
        except Exception as e:
            log('OUT error %s: %s' % (name[:22], e))
            try:
                os.replace(path, os.path.join(FAILED, name))
            except Exception:
                pass


def inbound():
    try:
        res = K.kanban_list(lane=LANE)
    except Exception as e:
        log('IN list error: %s' % e)
        return
    for card in res.get('cards', []):
        path = os.path.join(INBOX, '%s.json' % card['id'])
        if os.path.exists(path):
            continue
        item = {'id': card['id'], 'title': card.get('title', ''),
                'detail': card.get('detail', ''), 'state': card.get('state'),
                'priority': card.get('priority'), 'owner': card.get('owner'),
                'lane': card.get('lane'), 'updated': card.get('updated'),
                'ts': time.time()}
        with open(path, 'w') as f:
            json.dump(item, f)
        log('IN %s -> spool inbox (%s)' % (card['id'][:8], card.get('state')))


def main():
    lock = open('/tmp/omega_kanban_relay.lock', 'w')
    try:
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(0)
    log('kanban relay started (principal=%s lane=%s)' % (K.ME, LANE))
    while True:
        outbound()
        inbound()
        time.sleep(20)


if __name__ == '__main__':
    main()
