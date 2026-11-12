# a2a-omega-mesh-mcp (ENCRYPTED)

The encrypted A2A transport repo. **This is where the fleet's shared task
state lives.**

## What changed (boss 11-10)

> "stop using a2a omega mesh, leave the github frozen, from now on we use
> a2a mcp only. put a2a omega mesh the way it was. update a2a omega mesh mcp
> encrypted repo. do not delete the bulletin board, just disable it and
> switch to the kanban board setup."

| Component | Status |
|---|---|
| `a2a-omega-mesh` (plain repo) | **FROZEN** at `f23f48c` - no further pushes |
| `a2a-omega-mesh-mcp` (this repo) | **ACTIVE** - the a2a mcp + kanban live here |
| mesh message board (bulletin) | **DISABLED** - stopped, unit disabled, DB + files kept intact |
| a2a kanban board | **LIVE** - the durable task state that replaces it |

## Two boards, two jobs

**Bulletin board** (`mesh-board-prod-20261031`) - transient instructions.
`LIFETIME=120`, `RETENTION=300`, `kind=request|response`. Rows are DELETED
after ~5 minutes. Now disabled (boss: "do not delete the bulletin board,
just disable it"). Its DB is backed up at
`/root/backups/mesh-board-disabled/board.db.<ts>` and the unit file is
still on disk, so it can be re-enabled without rebuilding anything.

**Kanban board** (`omega-kanban-20261110`) - durable task cards.
`todo -> doing -> done` (+ `blocked`). Cards NEVER expire. One owner per
card, `done` is terminal, and every transition is appended to a signed
history. This is the replacement for "messages disappear after 5 minutes".

## Security model (unchanged principles, new transport)

1. **Ed25519 signatures** - every RPC envelope is signed by the acting
   principal with `ssh-keygen -Y sign` and verified by the board against
   `roster.json`. An agent cannot act as another agent.
2. **AES-256-GCM encryption** - card title/detail/notes are ciphertext at
   rest. The board stores opaque blobs and never holds the key.
3. **Ownership ACL** - only the owner (or creator of an unowned card) can
   move a card. Two agents cannot fight over the same work.
4. **Terminal state** - `done` cannot be reopened; a finished card is
   final.

### Encryption key model

`mesh_enc.py` (per-recipient, for point-to-point messages) is unchanged.
`kanban_enc.py` uses a **shared CEK** instead, because a card is SHARED
state: the owner moves it, every principal lists it. A shared key keeps
that workable; the AAD binds each ciphertext to `board + card_id`, so a
ciphertext cannot be transplanted onto another card.

## Files

| File | Purpose |
|---|---|
| `kanban_mcp_server.py` | The a2a mcp stdio server - 8 `kanban_*` tools |
| `kanban_enc.py` | Shared-CEK AES-256-GCM for card content |
| `kanban_board.py` | The board: signed RPC, ACL, durable cards, history |
| `kanban-board.service` | systemd unit for the board (init + receipt prune) |
| `omega_kanban_relay.py` | Host-side relay for the omega container |
| `omega-kanban-relay.service` | systemd unit for that relay |
| `gvps_kanban.py` | googlevps client for the union verification lane |

## MCP tools

```
kanban_create(title, detail, lane, priority, owner, state) -> card id
kanban_claim(card_id, note)          -> own it, state=doing
kanban_move(card_id, state, note)    -> todo|doing|done|blocked
kanban_comment(card_id, note)        -> append a note
kanban_get(card_id)                  -> one card
kanban_list(state, owner, lane)      -> filtered cards
kanban_board(lane)                   -> {todo:[],doing:[],done:[],blocked:[]}
kanban_history(card_id)              -> signed transition log
```

## Principals

`laptop`, `rci-vps`, `googlevps`, `lane-chat`, `lane-coding`, `lane-vision`,
`lane-asi1-gig`, `lane-asi1-airdrop` (+ `exodus`, `s21fe`, `s24` for the
phones in the mesh roster).

## Wiring a profile

Profiles are INDEPENDENT ISLANDS - there is no config inheritance, so every
profile needs its own `mcp_servers` block with its own principal:

```yaml
mcp_servers:
  a2a-kanban:
    command: C:/Users/<user>/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe
    args: [C:/Users/<user>/work/_tmp/kanban_mcp/kanban_mcp_server.py]
    env:
      KANBAN_PRINCIPAL: lane-coding
      KANBAN_KEY: C:/Users/<user>/work/_tmp/kanban_principals/lane-coding.key
      KANBAN_ENC_KEYS: C:/Users/<user>/work/_tmp/kanban_mcp/kanban_cek.json
      KANBAN_BOARD_HOST: 100.116.149.122
      KANBAN_SSH_KEY: C:/Users/<user>/.ssh/id_rci_vps
      PYTHONPATH: C:/Users/<user>/work/_tmp/kanban_mcp
    timeout: 120
```

The server runs LOCALLY (it must hold the lane's private signing key) and
reaches the board over ssh. The board host is omega (100.116.149.122).

## What else is required to make it work

Wiring the MCP block is the easy half. Verified checklist:

**Per lane / client host**

- Its OWN ed25519 signing keypair, public key registered in `roster.json` for
  that principal (`ssh-keygen -t ed25519 -f kanban_principals/<principal>.key`).
- The shared CEK (`kanban_cek.json`, mode 0600) and `kanban_enc.py`. The board
  never holds the CEK; no CEK = every create/read fails CLOSED.
- `ssh-keygen` on PATH (that is what signs envelopes) and an ssh key that
  reaches the board host with `BatchMode=yes`.
- An AES-GCM backend: `pycryptodome` or `cryptography` (`pycryptodome` on Termux).
- `KANBAN_ROSTER` / `KANBAN_DB` are **board-host paths**, not local paths --
  pointing them at `C:/...` makes the board die with `FileNotFoundError: roster.json`.

**Board host**

- `kanban_board.py` + live `roster.json` at `/opt/mesh-board-prod-20261031/`,
  `kanban_mcp_server.py` + `kanban_enc.py` + `kanban_cek.json` (0600) at `/opt/kanban-mcp/`,
  DB at `/var/lib/mesh-kanban/kanban.db` created by `--init`.
- `kanban-board.service` enabled, sshd reachable.

**Skills the agent must have loaded** (code does not need them, the operator does):
`omega-mesh-mcp` (calling the a2a MCP, envelope model), `a2a-omega-mesh` (fleet
topology / which host runs what), `a2a-fleet-ops` (hub + pollers), and
`a2a-brain-bridge-ops` if the brain side of the mesh is being patched. A lane
with the MCP wired but no `omega-mesh-mcp` skill improvises envelopes and gets
rejected.

**Pollers / daemons -- without these the board is a dead mirror**

| Where | Process | Cadence | Role |
|---|---|---|---|
| board host | `kanban-board.service` | oneshot at boot | schema init + receipt prune; cards never expire |
| board host | `omega-kanban-relay.service` | 20 s loop | container spool <-> board, signs as `rci-vps` |
| laptop | `Hermes_Lane_Dispatch` (schtasks, 4 h) -> `dispatch_all.py` -> `kanban_bridge.py` | 4 h | mirrors lanes.md PENDING/DOING/CLOSED onto cards |
| googlevps | `gvps_kanban.py` | on demand | UNION verification lane claims/verifies |
| any lane | MCP tools | on demand | lane records progress on the card it was given |

If `dispatch_all.py` is not running, no cards get created and the board looks
dead even though every lane can read it.

**Preflight** -- run before wiring a profile, and whenever a lane "cannot see
the board": `python kanban_doctor.py --live`. Nine checks: AES backend,
ssh-keygen, signing key + CEK present and 0600, key matches the roster entry,
ssh reaches the board, a signed `kanban_list` returns, and a card body
decrypts. Exit 0 = green. Verified green 2026-11-12 against the live board
(14 cards visible, principal `lane-coding`).

Full install / wiring / troubleshooting (incl. the fixed-bug table: comment
IntegrityError, list-has-no-detail, spool perms, payload defaults, inbound
refresh) lives in the private repo **`jamesparser/a2a-omega-kanban`**.
