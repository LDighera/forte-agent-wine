#!/usr/bin/env python3
"""Shared, conservatively reserved catch-up limits; no provider code."""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pop_backlog

MAX_MESSAGES=1000
MAX_BYTES=1024**3
MESSAGE_BYTES=10*1024**2


def require(ok, code):
    if not ok: raise ValueError(code)


def read(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size<4096,'budget_file_invalid')
    data=json.loads(path.read_text(),object_pairs_hook=pop_backlog.unique)
    require(set(data)=={'maximum_messages','maximum_bytes','charged_messages','charged_bytes'},'budget_fields_invalid')
    for key,value in data.items(): require(type(value) is int and value>=0,'budget_number_invalid')
    require(0<=data['charged_messages']<=data['maximum_messages']<=MAX_MESSAGES,'message_budget_invalid')
    require(0<=data['charged_bytes']<=data['maximum_bytes']<=MAX_BYTES,'byte_budget_invalid')
    return data


def write(path,data):
    # Use the same fsync/atomic-replace primitive as backlog snapshots, but this
    # schema is distinct. The lock is on a separate inode, never the replaced file.
    import tempfile
    fd,tmp=tempfile.mkstemp(prefix='.budget-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(data,f,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
        fd=os.open(path.parent,os.O_DIRECTORY|os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


@contextlib.contextmanager
def locked(path):
    fd=os.open(str(path)+'.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        yield
    finally: os.close(fd)


def reserve(path,requested=10,*,peers=1):
    require(type(requested) is int and 1<=requested<=50,'reservation_invalid')
    require(type(peers) is int and peers in (1,4),'reservation_peers_invalid')
    with locked(path):
        data=read(path)
        remaining_messages=data['maximum_messages']-data['charged_messages']
        remaining_slots=(data['maximum_bytes']-data['charged_bytes'])//MESSAGE_BYTES
        allowance=min(requested,data['maximum_messages']-data['charged_messages'],
                      (data['maximum_bytes']-data['charged_bytes'])//MESSAGE_BYTES,
                      max(1,remaining_messages-(peers-1)),max(1,remaining_slots-(peers-1)))
        require(allowance>0,'catchup_budget_exhausted')
        data['charged_messages']+=allowance
        data['charged_bytes']+=allowance*MESSAGE_BYTES
        write(path,data)  # Durable reservation BEFORE any provider connection.
    return allowance


def settle(path,reserved,messages,octets,deferred):
    require(0<=messages+deferred<=reserved and 0<=octets<=messages*MESSAGE_BYTES,'settlement_invalid')
    with locked(path):
        data=read(path)
        data['charged_messages']-=reserved-messages-deferred
        data['charged_bytes']-=reserved*MESSAGE_BYTES-octets-deferred*MESSAGE_BYTES
        require(data['charged_messages']>=0 and data['charged_bytes']>=0,'settlement_underflow')
        write(path,data)


class IncrementalReservation:
    """Reserve count slots once, but at most one worst-case message in flight.

    A failed transaction never refunds automatically. Successful RETR content is
    charged before more RETRs are admitted. Unknown-size deferred RETRs retain
    one full MESSAGE_BYTES charge. Unused count/byte credit is returned only
    after the existing listener commit handshake.
    """
    def __init__(self,path,slots,peers):
        self.path=path; self.slots=slots; self.peers=peers
        self.credit=True; self.accepted=0; self.octets=0; self.closed=False

    @classmethod
    def open(cls,path,requested=25,*,peers=4):
        require(type(requested) is int and 1<=requested<=50,'reservation_invalid')
        require(type(peers) is int and peers in (1,4),'reservation_peers_invalid')
        with locked(path):
            data=read(path)
            remaining=data['maximum_messages']-data['charged_messages']
            slots=min(requested,remaining,max(1,remaining-(peers-1)))
            require(slots>0 and data['maximum_bytes']-data['charged_bytes']>=MESSAGE_BYTES,
                    'catchup_budget_exhausted')
            data['charged_messages']+=slots
            data['charged_bytes']+=MESSAGE_BYTES
            write(path,data) # Durable count and first-message credit BEFORE connection.
        return cls(path,slots,peers)

    def claim(self):
        require(not self.closed and self.accepted<self.slots,'reservation_state_invalid')
        if self.credit:
            return True
        with locked(self.path):
            data=read(self.path)
            # Leave one first-message credit for every other possible account.
            # At the low-water mark finish this batch, without consuming its UID.
            if data['maximum_bytes']-data['charged_bytes']<self.peers*MESSAGE_BYTES:
                return False
            data['charged_bytes']+=MESSAGE_BYTES
            write(self.path,data)
        self.credit=True
        return True

    def accept(self,octets):
        require(not self.closed and self.credit and self.accepted<self.slots,
                'reservation_state_invalid')
        require(type(octets) is int and 0<=octets<=MESSAGE_BYTES,'message_charge_invalid')
        with locked(self.path):
            data=read(self.path)
            require(data['charged_bytes']>=MESSAGE_BYTES,'reservation_charge_missing')
            data['charged_bytes']-=MESSAGE_BYTES-octets
            write(self.path,data)
        self.credit=False; self.accepted+=1; self.octets+=octets

    def finish(self,messages,octets,deferred):
        require(not self.closed,'reservation_already_closed')
        require(type(messages) is int and type(octets) is int and type(deferred) is int,
                'settlement_invalid')
        require(messages==self.accepted and octets==self.octets and deferred in (0,1)
                and messages+deferred<=self.slots and (not deferred or self.credit),
                'settlement_invalid')
        with locked(self.path):
            data=read(self.path)
            data['charged_messages']-=self.slots-messages-deferred
            if self.credit and not deferred:
                data['charged_bytes']-=MESSAGE_BYTES
            require(data['charged_messages']>=0 and data['charged_bytes']>=0,'settlement_underflow')
            write(self.path,data)
        self.closed=True


if __name__=='__main__':
    raise SystemExit('INTERNAL MODULE: use the portable debian_launcher.py entrypoint.')
