"""The Postgres shim against a dropped connection: a stand-in psycopg whose server 'closes the connection' on
demand. A read outside a transaction reconnects and retries once; inside tx() the failure propagates and the
transaction rolls back cleanly on the new connection; a plain SQL error is never retried."""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class OperationalError(Exception): pass
class InterfaceError(Exception): pass
class ProgrammingError(Exception): pass


class FakeCursor:
    def __init__(self, conn): self.conn = conn; self.description = None
    def execute(self, q, params=()):
        self.conn.log.append(q.strip().split()[0].upper())
        if self.conn.dead:
            raise OperationalError("consuming input failed: server closed the connection unexpectedly")
        if "BOOM" in q:
            raise ProgrammingError('relation "boom" does not exist')
        self.description = None
    def fetchone(self): return None
    def fetchall(self): return []


class FakeConn:
    opened = 0
    def __init__(self):
        FakeConn.opened += 1; self.dead = False; self.closed = False; self.log = []
    def cursor(self): return FakeCursor(self)
    def execute(self, q): return FakeCursor(self).execute(q)
    def close(self): self.closed = True


fake = types.ModuleType("psycopg")
fake.OperationalError = OperationalError; fake.InterfaceError = InterfaceError
fake.connect = lambda url, **kw: FakeConn()
sys.modules["psycopg"] = fake

from publicbank import db  # noqa: E402

con = db._PgConn("postgresql://fake")
first = con._pg
assert FakeConn.opened == 1

# a dropped connection outside a transaction: reconnect, retry, same call succeeds
first.dead = True
con.execute("SELECT 1 FROM members WHERE telegram_id=?", (1,))
assert FakeConn.opened == 2 and first.closed and con._pg is not first
assert con._pg.log == ["SELECT"]

# a SQL error is not a lost connection: no reconnect, the error propagates
try:
    con.execute("SELECT * FROM BOOM"); raise AssertionError("retried a SQL error")
except ProgrammingError:
    pass
assert FakeConn.opened == 2

# inside tx(): the connection dies after BEGIN; the write fails, tx() rolls back on the fresh connection
second = con._pg
try:
    with db.tx(con):
        assert con._in_tx
        second.dead = True
        con.execute("UPDATE members SET upi_id=? WHERE id=?", ("x", "y"))
    raise AssertionError("a half-lost transaction was retried")
except OperationalError:
    pass
assert FakeConn.opened == 3 and not con._in_tx
assert con._pg.log == ["ROLLBACK"], con._pg.log          # rollback went to the new connection, nothing else did
assert "UPDATE" not in con._pg.log                       # the write was not replayed

# a healthy transaction tracks state
with db.tx(con):
    assert con._in_tx
    con.execute("INSERT OR IGNORE INTO idempotency (key) VALUES (?)", ("k",))
assert not con._in_tx and con._pg.log[-3:] == ["BEGIN", "INSERT", "COMMIT"]

# the classifier
assert db._connection_lost(OperationalError("SSL SYSCALL error: EOF detected"))
assert db._connection_lost(InterfaceError("the connection is closed"))
assert not db._connection_lost(OperationalError("FATAL: password authentication failed"))
assert not db._connection_lost(ProgrammingError("syntax error"))

print("DB OK · reconnect once on a dropped connection · no retry mid-transaction · SQL errors untouched")
