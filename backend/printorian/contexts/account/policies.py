"""The rules of the customer's own record.

Two of them, and both exist because a screen would otherwise be able to write
something the farm cannot honour.
"""

from __future__ import annotations

#: How many delivery addresses one customer may keep.
#:
#: Not a storage limit — twenty rows is nothing. It is a limit on an unauthenticated
#: shape of abuse: the address book is the one place a customer writes free text
#: that staff later read on a packing slip, and an unbounded list is an unbounded
#: place to put it.
MAX_ADDRESSES = 20

#: Notifications about money are not optional.
#:
#: The kit draws this switch on and disabled, and it is right to. When an order
#: runs past its promised date the price drops automatically (ADR-0004's decay
#: policy) — that is a change to what the customer is charged, and a farm that
#: lets someone opt out of hearing about it has arranged not to tell them their
#: money moved. The API reports the switch so the screen can draw it; there is no
#: field behind it to turn off.
LATE_CREDIT_IS_MANDATORY = True
