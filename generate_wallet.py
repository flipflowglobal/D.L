#!/usr/bin/env python3

from eth_account import Account

acct = Account.create()

print("==== NEW WALLET GENERATED ====")
print("Address:", acct.address)
print("Private Key:", acct.key.hex())
